from __future__ import annotations

import os
import queue
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from .batch import BatchReport, discover_archives, extract_batch
from .config import (
    AppConfig,
    ConfigError,
    load_config,
    passwords_to_text,
    resolve_config_path,
    save_config,
    text_to_passwords,
)
from .core import (
    ExtractionCancelled,
    ExtractionReport,
    find_7zip_executable,
    find_bandizip_executable,
    parse_extensions,
)

DEFAULT_OUTPUT = Path.home() / "Unpacked"
_ELIDE_LIMIT = 88
_ARCHIVE_TYPES = "*.zip *.7z *.rar *.001 *.bin *.dat *.gz *.xz *.bz2 *.tar"


def _elide(text: str, limit: int = _ELIDE_LIMIT) -> str:
    """Shorten a long path for display, keeping the informative tail."""
    if len(text) <= limit:
        return text
    head = max(limit // 3, 8)
    tail = max(limit - head - 1, 8)
    return f"{text[:head]}…{text[-tail:]}"


def _path_key(path: Path) -> str:
    try:
        return os.path.normcase(str(path.resolve()))
    except OSError:
        return os.path.normcase(str(path))


class RecursiveUnpackerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("递归解压助手")
        self.root.geometry("960x820")
        self.root.minsize(820, 700)
        self.messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel_event: threading.Event | None = None
        self.last_output: Path | None = None
        self.source_paths: list[Path] = []
        self.config_notes: list[str] = []
        self._busy = False

        location = resolve_config_path()
        self.config_path = location.path
        if location.note:
            self.config_notes.append(location.note)
        config = load_config(location.path, warn=self.config_notes.append)

        self.output_var = tk.StringVar(value=config.output_dir or str(DEFAULT_OUTPUT))
        self.extensions_var = tk.StringVar(value=config.extensions)
        self.depth_var = tk.IntVar(value=config.max_depth)
        self.bandizip_var = tk.StringVar(
            value=config.bandizip_path or find_bandizip_executable() or ""
        )
        self.seven_zip_var = tk.StringVar(
            value=config.seven_zip_path or find_7zip_executable() or ""
        )
        self.delete_nested_var = tk.BooleanVar(value=config.delete_nested)
        self.include_subfolders_var = tk.BooleanVar(value=config.include_subfolders)
        self.status_var = tk.StringVar(value="就绪")
        self.source_count_var = tk.StringVar(value="共 0 项")

        self._build_ui()
        self._refill_passwords(config.passwords)
        self._refresh_source_list()
        self._report_config_notes()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._poll_messages)

    # ------------------------------------------------------------------ layout

    def _build_ui(self) -> None:
        try:
            style = ttk.Style(self.root)
            if "vista" in style.theme_names():
                style.theme_use("vista")
            elif "clam" in style.theme_names():
                style.theme_use("clam")
        except tk.TclError:
            pass

        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(8, weight=1)

        title = ttk.Label(outer, text="递归解压助手", font=("Microsoft YaHei UI", 18, "bold"))
        title.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))
        subtitle = ttk.Label(
            outer,
            text="批量处理文件与文件夹，自动识别错误后缀、轮询候选密码，并递归展开内层压缩包。",
            foreground="#555555",
        )
        subtitle.grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 12))

        ttk.Label(outer, text="输出目录").grid(row=3, column=0, sticky="w", pady=5)
        self.output_entry = ttk.Entry(outer, textvariable=self.output_var)
        self.output_entry.grid(row=3, column=1, sticky="ew", padx=(10, 8), pady=5)
        self.output_button = ttk.Button(outer, text="选择目录…", command=self._choose_output)
        self.output_button.grid(row=3, column=2, sticky="ew", pady=5)

        ttk.Label(outer, text="尝试后缀").grid(row=4, column=0, sticky="w", pady=5)
        self.extensions_entry = ttk.Entry(outer, textvariable=self.extensions_var)
        self.extensions_entry.grid(row=4, column=1, columnspan=2, sticky="ew", padx=(10, 0), pady=5)

        password_frame = ttk.Frame(outer)
        password_frame.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(10, 5))
        password_frame.columnconfigure(1, weight=1)
        ttk.Label(password_frame, text="候选密码").grid(row=0, column=0, sticky="nw", padx=(0, 10))
        self.password_text = scrolledtext.ScrolledText(
            password_frame, height=4, wrap="none", font=("Consolas", 10)
        )
        self.password_text.grid(row=0, column=1, sticky="ew")
        self.password_hint = ttk.Label(
            password_frame, text="", foreground="#555555", wraplength=660
        )
        self.password_hint.grid(row=1, column=1, sticky="w", pady=(4, 0))

        options = ttk.LabelFrame(outer, text="递归与解压设置", padding=(10, 8))
        options.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(10, 8))
        options.columnconfigure(1, weight=1)

        ttk.Label(options, text="最大递归层数").grid(row=0, column=0, sticky="w")
        self.depth_spin = ttk.Spinbox(options, from_=1, to=20, width=5, textvariable=self.depth_var)
        self.depth_spin.grid(row=0, column=1, sticky="w", padx=(8, 18))
        self.subfolder_check = ttk.Checkbutton(
            options,
            text="包含子文件夹（扫描子目录）",
            variable=self.include_subfolders_var,
        )
        self.subfolder_check.grid(row=0, column=2, sticky="w", padx=(0, 18))
        self.delete_nested_check = ttk.Checkbutton(
            options,
            text="内层压缩包成功解压后删除原压缩包",
            variable=self.delete_nested_var,
        )
        self.delete_nested_check.grid(row=0, column=3, columnspan=2, sticky="w")

        ttk.Label(options, text="Bandizip 控制台路径（优先，需 bz.exe）").grid(
            row=1, column=0, sticky="w", pady=(9, 0)
        )
        self.bandizip_entry = ttk.Entry(options, textvariable=self.bandizip_var)
        self.bandizip_entry.grid(row=1, column=1, columnspan=3, sticky="ew", padx=(8, 8), pady=(9, 0))
        self.bandizip_button = ttk.Button(options, text="浏览…", command=self._choose_bandizip)
        self.bandizip_button.grid(row=1, column=4, sticky="e", pady=(9, 0))

        ttk.Label(options, text="7-Zip 路径（可选回退）").grid(row=2, column=0, sticky="w", pady=(9, 0))
        self.seven_zip_entry = ttk.Entry(options, textvariable=self.seven_zip_var)
        self.seven_zip_entry.grid(row=2, column=1, columnspan=3, sticky="ew", padx=(8, 8), pady=(9, 0))
        self.seven_zip_button = ttk.Button(options, text="浏览…", command=self._choose_7zip)
        self.seven_zip_button.grid(row=2, column=4, sticky="e", pady=(9, 0))

        log_header = ttk.Frame(outer)
        log_header.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(8, 4))
        log_header.columnconfigure(0, weight=1)
        ttk.Label(log_header, text="运行日志", font=("Microsoft YaHei UI", 10, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        self.progress = ttk.Progressbar(log_header, mode="determinate", maximum=1, value=0, length=180)
        self.progress.grid(row=0, column=1, sticky="e")

        self.log_text = scrolledtext.ScrolledText(
            outer, height=12, wrap="word", state="disabled", font=("Consolas", 9)
        )
        self.log_text.grid(row=8, column=0, columnspan=3, sticky="nsew")

        bottom = ttk.Frame(outer)
        bottom.grid(row=9, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        bottom.columnconfigure(0, weight=1)
        ttk.Label(bottom, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        self.open_button = ttk.Button(
            bottom, text="打开输出目录", command=self._open_output, state="disabled"
        )
        self.open_button.grid(row=0, column=1, padx=(0, 8))
        self.cancel_button = ttk.Button(bottom, text="取消", command=self._cancel, state="disabled")
        self.cancel_button.grid(row=0, column=2, padx=(0, 8))
        self.start_button = ttk.Button(bottom, text="开始递归解压", command=self._start)
        self.start_button.grid(row=0, column=3)

        note = ttk.Label(
            outer,
            text=(
                "安全提示：原始文件不会被修改；遇到不安全路径会拒绝解压；达到层数上限时会保留并报告剩余压缩包。"
                "分卷压缩包请添加 .001（需 Bandizip 或 7-Zip）。密码以明文保存在本机配置文件，不写入运行日志。"
            ),
            foreground="#555555",
            wraplength=900,
        )
        note.grid(row=10, column=0, columnspan=3, sticky="w", pady=(10, 0))

        self._build_sources_panel(outer)

    def _build_sources_panel(self, outer: ttk.Frame) -> None:
        panel = ttk.LabelFrame(outer, text="待处理压缩包（可混合添加文件与文件夹）", padding=(10, 8))
        panel.grid(row=2, column=0, columnspan=3, sticky="ew")
        panel.columnconfigure(0, weight=1)

        self.source_list = tk.Listbox(
            panel,
            height=6,
            selectmode="extended",
            exportselection=False,
            activestyle="none",
            font=("Consolas", 9),
        )
        self.source_list.grid(row=0, column=0, sticky="ew")
        scrollbar = ttk.Scrollbar(panel, orient="vertical", command=self.source_list.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.source_list.configure(yscrollcommand=scrollbar.set)
        self.source_list.bind("<<ListboxSelect>>", self._on_source_select)

        buttons = ttk.Frame(panel)
        buttons.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        buttons.columnconfigure(4, weight=1)

        self.add_files_button = ttk.Button(buttons, text="添加文件…", command=self._add_files)
        self.add_files_button.grid(row=0, column=0)
        self.add_folder_button = ttk.Button(buttons, text="添加文件夹…", command=self._add_folder)
        self.add_folder_button.grid(row=0, column=1, padx=(6, 0))
        self.remove_button = ttk.Button(buttons, text="移除所选", command=self._remove_selected)
        self.remove_button.grid(row=0, column=2, padx=(6, 0))
        self.clear_button = ttk.Button(buttons, text="清空列表", command=self._clear_sources)
        self.clear_button.grid(row=0, column=3, padx=(6, 0))
        ttk.Label(buttons, textvariable=self.source_count_var, foreground="#555555").grid(
            row=0, column=5, sticky="e"
        )

    # -------------------------------------------------------------- source list

    def _on_source_select(self, _event: object = None) -> None:
        selection = self.source_list.curselection()
        if not selection:
            return
        index = selection[-1]
        if 0 <= index < len(self.source_paths):
            self.status_var.set(str(self.source_paths[index]))

    def _add_files(self) -> None:
        selected = filedialog.askopenfilenames(
            title="选择待处理压缩包（可多选）",
            filetypes=[("压缩包及任意文件", _ARCHIVE_TYPES), ("所有文件", "*.*")],
        )
        if selected:
            self._append_sources([Path(item) for item in selected])

    def _add_folder(self) -> None:
        selected = filedialog.askdirectory(title="选择包含压缩包的文件夹", mustexist=True)
        if selected:
            self._append_sources([Path(selected)])

    def _append_sources(self, paths: list[Path]) -> None:
        seen = {_path_key(path) for path in self.source_paths}
        added = 0
        for path in paths:
            key = _path_key(path)
            if key in seen:
                continue
            seen.add(key)
            self.source_paths.append(path)
            added += 1
        self._refresh_source_list()
        skipped = len(paths) - added
        if skipped:
            self._append_log(f"已忽略 {skipped} 个重复项。")

    def _remove_selected(self) -> None:
        selection = sorted(self.source_list.curselection(), reverse=True)
        if not selection:
            return
        for index in selection:
            if 0 <= index < len(self.source_paths):
                del self.source_paths[index]
        self._refresh_source_list()

    def _clear_sources(self) -> None:
        self.source_paths.clear()
        self._refresh_source_list()

    def _refresh_source_list(self) -> None:
        self.source_list.delete(0, "end")
        for path in self.source_paths:
            self.source_list.insert("end", _elide(str(path)))
        self.source_count_var.set(f"共 {len(self.source_paths)} 项")
        if self._busy:
            self.remove_button.configure(state="disabled")
            self.clear_button.configure(state="disabled")
            return
        state = "normal" if self.source_paths else "disabled"
        self.remove_button.configure(state=state)
        self.clear_button.configure(state=state)

    # ------------------------------------------------------------- pick dialogs

    def _choose_output(self) -> None:
        selected = filedialog.askdirectory(title="选择输出目录", mustexist=False)
        if selected:
            self.output_var.set(selected)

    def _choose_bandizip(self) -> None:
        selected = filedialog.askopenfilename(
            title="选择 Bandizip 控制台程序 bz.exe",
            filetypes=[("Bandizip 控制台程序", "bz.exe"), ("可执行文件", "*.exe"), ("所有文件", "*.*")],
        )
        if selected:
            self.bandizip_var.set(selected)

    def _choose_7zip(self) -> None:
        selected = filedialog.askopenfilename(
            title="选择 7z.exe",
            filetypes=[("7-Zip 程序", "7z.exe"), ("可执行文件", "*.exe"), ("所有文件", "*.*")],
        )
        if selected:
            self.seven_zip_var.set(selected)

    # ------------------------------------------------------------------- config

    def _refill_passwords(self, passwords: list[str]) -> None:
        if not passwords:
            return
        self.password_text.insert("1.0", passwords_to_text(passwords))

    def _update_password_hint(self) -> None:
        self.password_hint.configure(
            text=(
                "每行一个密码；会先尝试无密码/空密码。"
                f"密码以明文保存在本机 {self.config_path.name}（路径见上方日志），不会写入日志。"
            )
        )

    def _report_config_notes(self) -> None:
        self._append_log(f"配置文件：{self.config_path}")
        for note in self.config_notes:
            self._append_log(f"配置提示：{note}")
        self._update_password_hint()

    def _current_passwords(self) -> list[str]:
        return text_to_passwords(self.password_text.get("1.0", "end-1c"))

    def _persist_settings(self, passwords: list[str] | None = None) -> None:
        try:
            max_depth = int(self.depth_var.get())
        except (tk.TclError, ValueError):
            max_depth = 5
        config = AppConfig(
            output_dir=self.output_var.get().strip().strip('"'),
            extensions=self.extensions_var.get().strip(),
            max_depth=max_depth,
            bandizip_path=self.bandizip_var.get().strip().strip('"'),
            seven_zip_path=self.seven_zip_var.get().strip().strip('"'),
            delete_nested=bool(self.delete_nested_var.get()),
            include_subfolders=bool(self.include_subfolders_var.get()),
            passwords=self._current_passwords() if passwords is None else passwords,
        )
        try:
            self.config_path = save_config(config, self.config_path)
        except ConfigError as exc:
            self._append_log(f"配置未能保存：{exc}")
        except OSError as exc:
            self._append_log(f"配置未能保存：{exc}")
        self._update_password_hint()

    # --------------------------------------------------------------------- run

    def _append_log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        for widget in (
            self.add_files_button,
            self.add_folder_button,
            self.output_entry,
            self.output_button,
            self.extensions_entry,
            self.depth_spin,
            self.bandizip_entry,
            self.bandizip_button,
            self.seven_zip_entry,
            self.seven_zip_button,
            self.subfolder_check,
            self.delete_nested_check,
        ):
            widget.configure(state=state)
        self.source_list.configure(state="normal" if not busy else "disabled")
        self.password_text.configure(state=state)
        if busy:
            self.remove_button.configure(state="disabled")
            self.clear_button.configure(state="disabled")
            self.start_button.configure(state="disabled")
            self.cancel_button.configure(state="normal")
            self.open_button.configure(state="disabled")
            return
        self.start_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        self.open_button.configure(state="normal" if self.last_output else "disabled")
        self._refresh_source_list()

    def _excluded_output_dirs(self, sources: list[Path], output_text: str) -> list[Path]:
        try:
            output_path = Path(output_text).expanduser().resolve()
        except OSError:
            return []
        # Never exclude a folder the user explicitly added, otherwise scanning it
        # would silently produce nothing.
        for source in sources:
            try:
                if source.is_dir() and _path_key(source) == _path_key(output_path):
                    return []
            except OSError:
                continue
        return [output_path]

    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self.source_paths:
            messagebox.showerror("缺少输入", "请先添加至少一个压缩包或文件夹。", parent=self.root)
            return
        output_text = self.output_var.get().strip().strip('"')
        if not output_text:
            messagebox.showerror("缺少输出目录", "请先选择输出目录。", parent=self.root)
            return
        try:
            extensions = parse_extensions(self.extensions_var.get())
            max_depth = int(self.depth_var.get())
            if not 1 <= max_depth <= 20:
                raise ValueError("最大递归层数必须在 1 到 20 之间")
        except ValueError as exc:
            messagebox.showerror("配置有误", str(exc), parent=self.root)
            return

        sources = list(self.source_paths)
        recursive = bool(self.include_subfolders_var.get())
        passwords = self._current_passwords()
        bandizip_path = self.bandizip_var.get().strip().strip('"') or None
        seven_zip_path = self.seven_zip_var.get().strip().strip('"') or None
        delete_nested = bool(self.delete_nested_var.get())
        excluded = self._excluded_output_dirs(sources, output_text)

        self._persist_settings(passwords)

        self.cancel_event = threading.Event()
        self._set_busy(True)
        self.progress.configure(mode="indeterminate", maximum=1, value=0)
        self.progress.start(12)
        self.status_var.set("正在扫描待处理文件…")
        self._append_log("\n" + "=" * 54)

        def worker_main() -> None:
            try:
                archives = discover_archives(
                    sources,
                    extensions,
                    recursive,
                    exclude_dirs=excluded,
                    log=lambda line: self.messages.put(("log", line)),
                    cancel_event=self.cancel_event,
                )
                self.messages.put(("plan", len(archives)))
                report = extract_batch(
                    archives,
                    output_text,
                    extensions=extensions,
                    passwords=passwords,
                    max_depth=max_depth,
                    bandizip_path=bandizip_path,
                    seven_zip_path=seven_zip_path,
                    delete_nested=delete_nested,
                    log=lambda line: self.messages.put(("log", line)),
                    cancel_event=self.cancel_event,
                    on_progress=lambda index, total, source: self.messages.put(
                        ("progress", (index, total, source.name))
                    ),
                )
                self.messages.put(("done", report))
            except ExtractionCancelled as exc:
                self.messages.put(("cancelled", getattr(exc, "report", None)))
            except Exception as exc:
                self.messages.put(("error", str(exc)))

        self.worker = threading.Thread(
            target=worker_main, name="recursive-unpack-worker", daemon=True
        )
        self.worker.start()

    def _cancel(self) -> None:
        if self.cancel_event:
            self.cancel_event.set()
            self.status_var.set("正在请求取消；当前解压操作结束后停止…")
            self._append_log("已请求取消。")
            self.cancel_button.configure(state="disabled")

    def _finish(self) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate", maximum=1, value=0)
        self._set_busy(False)

    # ---------------------------------------------------------------- messages

    def _poll_messages(self) -> None:
        try:
            while True:
                kind, payload = self.messages.get_nowait()
                if kind == "log":
                    self._append_log(str(payload))
                elif kind == "plan":
                    total = int(payload) if isinstance(payload, int) else 0
                    self.progress.stop()
                    self.progress.configure(mode="determinate", maximum=max(total, 1), value=0)
                    self.status_var.set(
                        f"共发现 {total} 个压缩包" if total else "未发现匹配的压缩包"
                    )
                elif kind == "progress":
                    index, total, name = payload  # type: ignore[misc]
                    self.progress.configure(value=index)
                    self.status_var.set(f"正在处理 {index}/{total}：{name}")
                elif kind == "done":
                    self._handle_done(payload)
                elif kind == "error":
                    self._finish()
                    self.status_var.set("处理失败")
                    self._append_log(f"错误：{payload}")
                    messagebox.showerror("解压失败", str(payload), parent=self.root)
                elif kind == "cancelled":
                    self._handle_cancelled(payload)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_messages)

    def _handle_done(self, payload: object) -> None:
        self._finish()
        if isinstance(payload, BatchReport):
            self.last_output = payload.output_base
            self.open_button.configure(state="normal")
            self._present_batch_report(payload)
            return
        if isinstance(payload, ExtractionReport):
            self.last_output = payload.output_dir
            self.open_button.configure(state="normal")
            self.status_var.set(f"完成：已解压 {payload.extracted_archives} 个压缩包")
            return
        self.status_var.set("处理结束")
        self._append_log(f"未知结果类型：{type(payload).__name__}")

    def _present_batch_report(self, report: BatchReport) -> None:
        total = report.total
        failed = report.failed
        remaining = len(report.remaining_archives)
        self.status_var.set(
            f"完成：成功 {len(report.succeeded)}/{total}"
            + (f"，失败 {len(failed)}" if failed else "")
        )
        if not total:
            messagebox.showinfo(
                "没有可处理的压缩包",
                "未在所选路径中发现匹配后缀的压缩包。\n"
                "请检查「尝试后缀」或是否勾选「包含子文件夹」。",
                parent=self.root,
            )
            return

        lines = [
            f"成功 {len(report.succeeded)} / 共 {total} 个压缩包，"
            f"累计解压 {report.extracted_archives} 个。",
            f"\n输出目录：{report.output_base}",
        ]
        if failed:
            lines.append("\n失败清单：")
            for item in failed[:10]:
                lines.append(f"  · {item.source.name} — {item.error}")
            if len(failed) > 10:
                lines.append(f"  … 其余 {len(failed) - 10} 个详见运行日志")
        if remaining:
            lines.append(f"\n仍有 {remaining} 个压缩包未展开（达到层数上限或格式不受支持）。")

        text = "\n".join(lines)
        if failed or remaining:
            messagebox.showwarning("批量解压结束，但未完全成功", text, parent=self.root)
        else:
            messagebox.showinfo("批量解压完成", text, parent=self.root)

    def _handle_cancelled(self, payload: object) -> None:
        self._finish()
        self.status_var.set("已取消")
        self._append_log("操作已取消。")
        if isinstance(payload, BatchReport) and payload.total:
            self._append_log(
                f"取消前已完成 {len(payload.succeeded)}/{payload.total}，"
                f"输出目录：{payload.output_base}"
            )
            self.last_output = payload.output_base
            self.open_button.configure(state="normal")

    # ------------------------------------------------------------------ window

    def _open_output(self) -> None:
        path = self.last_output
        if not path or not path.exists():
            messagebox.showwarning("输出目录不存在", "当前没有可打开的输出目录。", parent=self.root)
            return
        try:
            if os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif os.name == "posix":
                opener = "open" if __import__("sys").platform == "darwin" else "xdg-open"
                subprocess.Popen([opener, str(path)])
        except Exception as exc:
            messagebox.showerror("无法打开目录", str(exc), parent=self.root)

    def _on_close(self) -> None:
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(
                "仍在处理",
                "解压仍在进行，确定要退出吗？已完成的输出会保留。",
                parent=self.root,
            ):
                return
            if self.cancel_event:
                self.cancel_event.set()
            self.root.destroy()
            return
        self._persist_settings()
        self.root.destroy()


def launch_app() -> None:
    root = tk.Tk()
    RecursiveUnpackerApp(root)
    root.mainloop()
