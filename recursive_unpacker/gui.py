from __future__ import annotations

import os
import queue
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import panels
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
from .volumes import is_volume_start
from .theme import apply_theme, log_tag_for

DEFAULT_OUTPUT = Path.home() / "Unpacked"
_WINDOW_WIDTH = 1000
# Fits the "路径" column of the source table; longer values are cut with an ellipsis
# rather than being silently clipped by the column edge.
_ELIDE_LIMIT = 56
_ARCHIVE_TYPES = "*.zip *.7z *.rar *.001 *.bin *.dat *.gz *.xz *.bz2 *.tar"

_STATUS_STYLES = {
    "idle": "Pill.TLabel",
    "run": "Run.Pill.TLabel",
    "ok": "Ok.Pill.TLabel",
    "warn": "Warn.Pill.TLabel",
    "error": "Error.Pill.TLabel",
}

# Minimum number of log lines; the panel then grows with the window.
_LOG_HEIGHT = 12


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
        self.root.minsize(820, 580)
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
        self.summary_var = tk.StringVar(value="尚未添加待处理项")
        self.progress_var = tk.StringVar(value="0 / 0")
        self.metric_vars = {
            key: tk.StringVar(value="—") for key in ("found", "ok", "failed", "remaining")
        }

        self._build_ui()
        self._refill_passwords(config.passwords)
        self._refresh_source_list()
        self._report_config_notes()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._poll_messages)

    # ------------------------------------------------------------------ layout

    def _build_ui(self) -> None:
        self.fonts = apply_theme(self.root)

        outer = ttk.Frame(self.root, padding=(16, 12))
        outer.pack(fill="both", expand=True)
        # Workbench split: inputs on the left, run telemetry on the right.
        outer.columnconfigure(0, weight=5)
        outer.columnconfigure(1, weight=4)
        outer.rowconfigure(2, weight=1)

        panels.build_header(self, outer)
        panels.build_status_strip(self, outer)

        left = ttk.Frame(outer)
        left.grid(row=2, column=0, sticky="nsew", padx=(0, 12))
        left.columnconfigure(1, weight=1)
        left.rowconfigure(0, weight=1)

        right = ttk.Frame(outer)
        right.grid(row=2, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        panels.build_sources_panel(self, left)

        ttk.Label(left, text="输出目录").grid(row=1, column=0, sticky="w", pady=(12, 0))
        self.output_entry = ttk.Entry(left, textvariable=self.output_var)
        self.output_entry.grid(row=1, column=1, sticky="ew", padx=(12, 8), pady=(12, 0))
        self.output_button = ttk.Button(left, text="选择目录…", command=self._choose_output)
        self.output_button.grid(row=1, column=2, sticky="ew", pady=(12, 0))

        ttk.Label(left, text="尝试后缀").grid(row=2, column=0, sticky="w", pady=(9, 0))
        self.extensions_entry = ttk.Entry(left, textvariable=self.extensions_var)
        self.extensions_entry.grid(row=2, column=1, columnspan=2, sticky="ew", padx=(12, 0), pady=(9, 0))

        ttk.Label(left, text="候选密码").grid(row=3, column=0, sticky="nw", pady=(12, 0))
        self.password_text =         panels.make_text(
            self, left, height=3, width=34, wrap="none", mono=True, state="normal"
        )
        self.password_text.grid(row=3, column=1, columnspan=2, sticky="ew", padx=(12, 0), pady=(12, 0))
        self.password_hint = ttk.Label(left, text="", style="Faint.TLabel", wraplength=430)
        self.password_hint.grid(row=4, column=1, columnspan=2, sticky="w", padx=(12, 0), pady=(4, 0))

        panels.build_advanced(self, left)
        panels.build_overview(self, right)
        panels.build_log_panel(self, right)

        bottom = ttk.Frame(outer)
        bottom.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        bottom.columnconfigure(0, weight=1)
        self.open_button = ttk.Button(
            bottom, text="打开输出目录", command=self._open_output, state="disabled"
        )
        self.open_button.grid(row=0, column=1, padx=(0, 8))
        self.cancel_button = ttk.Button(bottom, text="取消", command=self._cancel, state="disabled")
        self.cancel_button.grid(row=0, column=2, padx=(0, 8))
        self.start_button = ttk.Button(
            bottom, text="开始递归解压", command=self._start, style="Accent.TButton"
        )
        self.start_button.grid(row=0, column=3)

        note = ttk.Label(
            outer,
            text=(
                "安全提示：原始文件不会被修改；遇到不安全路径会拒绝解压；达到层数上限时会保留并报告剩余压缩包。"
                "分卷压缩包请添加 .001（需 Bandizip 或 7-Zip）。密码以明文保存在本机配置文件，不写入运行日志。"
            ),
            style="Faint.TLabel",
            wraplength=900,
        )
        note.grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))

        self._fit_to_screen()

    def _fit_to_screen(self) -> None:
        """Open at the natural height, but never taller than the display allows."""
        self.root.update_idletasks()
        needed = self.root.winfo_reqheight()
        limit = max(self.root.winfo_screenheight() - 96, 480)
        self.root.geometry(f"{_WINDOW_WIDTH}x{min(needed, limit)}")

    def _toggle_advanced(self) -> None:
        if self.advanced_var.get():
            self.advanced_frame.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        else:
            self.advanced_frame.grid_remove()
        # Give the extra rows room rather than squeezing the source list.
        self._fit_to_screen()

    # -------------------------------------------------------------- source list

    def _describe_source(self, path: Path) -> tuple[str, str]:
        """Return ``(kind label, tree tag)`` for a queued path."""
        try:
            if not path.exists():
                return "不存在", "missing"
        except OSError:
            return "不存在", "missing"
        if path.is_dir():
            return "文件夹", ""
        if is_volume_start(path):
            return "分卷", "volume"
        return "文件", ""

    def _on_source_select(self, _event: object = None) -> None:
        selection = self.source_tree.selection()
        if not selection:
            return
        try:
            index = int(selection[-1])
        except ValueError:
            return
        if 0 <= index < len(self.source_paths):
            self.status_var.set(str(self.source_paths[index]))
            self.status_pill.configure(style="Pill.TLabel")

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
        selected = sorted(
            (int(iid) for iid in self.source_tree.selection() if iid.isdigit()),
            reverse=True,
        )
        if not selected:
            return
        for index in selected:
            if 0 <= index < len(self.source_paths):
                del self.source_paths[index]
        self._refresh_source_list()

    def _clear_sources(self) -> None:
        self.source_paths.clear()
        self._refresh_source_list()

    def _refresh_source_list(self) -> None:
        for iid in self.source_tree.get_children():
            self.source_tree.delete(iid)
        for index, path in enumerate(self.source_paths):
            kind, tag = self._describe_source(path)
            self.source_tree.insert(
                "",
                "end",
                iid=str(index),
                values=(_elide(str(path)), kind),
                tags=(tag,) if tag else (),
            )
        count = len(self.source_paths)
        self.source_count_var.set(f"共 {count} 项")
        self.summary_var.set(
            f"已添加 {count} 项" if count else "尚未添加待处理项"
        )
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

    def _append_log(self, message: str, tag: str | None = None) -> None:
        if tag is None:
            tag = log_tag_for(message)
        self.log_text.configure(state="normal")
        if tag:
            self.log_text.insert("end", message + "\n", tag)
        else:
            self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_status(self, message: str, kind: str = "idle") -> None:
        self.status_var.set(message)
        self.status_pill.configure(style=_STATUS_STYLES.get(kind, "Pill.TLabel"))

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
        self.source_tree.configure(selectmode="none" if busy else "extended")
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
        self._set_status("正在扫描待处理文件…", "run")
        self.progress_var.set("扫描中")
        self.metric_vars["found"].set("扫描中")
        for key in ("ok", "failed", "remaining"):
            self.metric_vars[key].set("—")
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
            self._set_status("正在请求取消；当前解压操作结束后停止…", "warn")
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
                    label = f"共发现 {total} 个压缩包" if total else "未发现匹配的压缩包"
                    self.summary_var.set(label)
                    self._set_status(label, "run" if total else "warn")
                    self.metric_vars["found"].set(str(total))
                    self.progress_var.set(f"0 / {total}")
                elif kind == "progress":
                    index, total, name = payload  # type: ignore[misc]
                    self.progress.configure(value=index)
                    self.progress_var.set(f"{index} / {total}")
                    self._set_status(f"正在处理 {index}/{total}：{name}", "run")
                elif kind == "done":
                    self._handle_done(payload)
                elif kind == "error":
                    self._finish()
                    self._set_status("处理失败", "error")
                    self._append_log(f"错误：{payload}")
                    messagebox.showerror("解压失败", str(payload), parent=self.root)
                elif kind == "cancelled":
                    self._handle_cancelled(payload)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_messages)

    def _apply_metrics(self, succeeded: int, failed: int, remaining: int) -> None:
        self.metric_vars["ok"].set(str(succeeded))
        self.metric_vars["failed"].set(str(failed))
        self.metric_vars["remaining"].set(str(remaining))

    def _handle_done(self, payload: object) -> None:
        self._finish()
        if isinstance(payload, BatchReport):
            self.last_output = payload.output_base
            self.open_button.configure(state="normal")
            self._apply_metrics(
                len(payload.succeeded), len(payload.failed), len(payload.remaining_archives)
            )
            self._present_batch_report(payload)
            return
        if isinstance(payload, ExtractionReport):
            self.last_output = payload.output_dir
            self.open_button.configure(state="normal")
            self._apply_metrics(payload.extracted_archives, 0, len(payload.remaining_archives))
            self._set_status(f"完成：已解压 {payload.extracted_archives} 个压缩包", "ok")
            return
        self._set_status("处理结束", "warn")
        self._append_log(f"未知结果类型：{type(payload).__name__}")

    def _present_batch_report(self, report: BatchReport) -> None:
        total = report.total
        failed = report.failed
        remaining = len(report.remaining_archives)
        self._set_status(
            f"完成：成功 {len(report.succeeded)}/{total}"
            + (f"，失败 {len(failed)}" if failed else ""),
            "ok" if not failed else "warn",
        )
        self.summary_var.set(f"成功 {len(report.succeeded)}/{total}，累计解压 {report.extracted_archives} 个")
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
        self._set_status("已取消", "warn")
        self._append_log("操作已取消。")
        if isinstance(payload, BatchReport) and payload.total:
            self._apply_metrics(
                len(payload.succeeded), len(payload.failed), len(payload.remaining_archives)
            )
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
