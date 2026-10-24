from __future__ import annotations

import os
import queue
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from .core import (
    ExtractionCancelled,
    ExtractionReport,
    extract_recursive,
    find_7zip_executable,
    find_bandizip_executable,
    parse_extensions,
)


class RecursiveUnpackerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("递归解压助手")
        self.root.geometry("900x760")
        self.root.minsize(780, 650)
        self.messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel_event: threading.Event | None = None
        self.last_output: Path | None = None

        self.source_var = tk.StringVar()
        self.output_var = tk.StringVar(value=str(Path.home() / "Unpacked"))
        self.extensions_var = tk.StringVar(value=".zip, .7z, .rar, .tar.gz")
        self.depth_var = tk.IntVar(value=5)
        self.bandizip_var = tk.StringVar(value=find_bandizip_executable() or "")
        self.seven_zip_var = tk.StringVar(value=find_7zip_executable() or "")
        self.delete_nested_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="就绪")

        self._build_ui()
        self.root.after(100, self._poll_messages)

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
        outer.rowconfigure(7, weight=1)

        title = ttk.Label(outer, text="递归解压助手", font=("Microsoft YaHei UI", 18, "bold"))
        title.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))
        subtitle = ttk.Label(
            outer,
            text="自动识别错误后缀、轮询候选密码，并递归展开内层压缩包。",
            foreground="#555555",
        )
        subtitle.grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 14))

        ttk.Label(outer, text="待处理文件").grid(row=2, column=0, sticky="w", pady=5)
        self.source_entry = ttk.Entry(outer, textvariable=self.source_var)
        self.source_entry.grid(row=2, column=1, sticky="ew", padx=(10, 8), pady=5)
        ttk.Button(outer, text="选择文件…", command=self._choose_source).grid(row=2, column=2, sticky="ew", pady=5)

        ttk.Label(outer, text="输出目录").grid(row=3, column=0, sticky="w", pady=5)
        ttk.Entry(outer, textvariable=self.output_var).grid(row=3, column=1, sticky="ew", padx=(10, 8), pady=5)
        ttk.Button(outer, text="选择目录…", command=self._choose_output).grid(row=3, column=2, sticky="ew", pady=5)

        ttk.Label(outer, text="尝试后缀").grid(row=4, column=0, sticky="w", pady=5)
        ttk.Entry(outer, textvariable=self.extensions_var).grid(row=4, column=1, columnspan=2, sticky="ew", padx=(10, 0), pady=5)

        password_frame = ttk.Frame(outer)
        password_frame.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(10, 5))
        password_frame.columnconfigure(1, weight=1)
        ttk.Label(password_frame, text="候选密码").grid(row=0, column=0, sticky="nw", padx=(0, 10))
        self.password_text = scrolledtext.ScrolledText(password_frame, height=4, wrap="none", font=("Consolas", 10))
        self.password_text.grid(row=0, column=1, sticky="ew")
        self.password_text.insert("1.0", "")
        ttk.Label(
            password_frame,
            text="每行一个密码；会先尝试无密码/空密码。密码不会保存，也不会写入日志。",
            foreground="#555555",
        ).grid(row=1, column=1, sticky="w", pady=(4, 0))

        options = ttk.LabelFrame(outer, text="递归与解压设置", padding=(10, 8))
        options.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(10, 8))
        options.columnconfigure(3, weight=1)
        ttk.Label(options, text="最大递归层数").grid(row=0, column=0, sticky="w")
        self.depth_spin = ttk.Spinbox(options, from_=1, to=20, width=5, textvariable=self.depth_var)
        self.depth_spin.grid(row=0, column=1, sticky="w", padx=(8, 18))
        ttk.Checkbutton(
            options,
            text="内层压缩包成功解压后删除原压缩包",
            variable=self.delete_nested_var,
        ).grid(row=0, column=2, columnspan=2, sticky="w")

        ttk.Label(options, text="Bandizip 控制台路径（优先，需 bz.exe）").grid(row=1, column=0, sticky="w", pady=(9, 0))
        ttk.Entry(options, textvariable=self.bandizip_var).grid(row=1, column=1, columnspan=2, sticky="ew", padx=(8, 8), pady=(9, 0))
        ttk.Button(options, text="浏览…", command=self._choose_bandizip).grid(row=1, column=3, sticky="e", pady=(9, 0))

        ttk.Label(options, text="7-Zip 路径（可选回退）").grid(row=2, column=0, sticky="w", pady=(9, 0))
        ttk.Entry(options, textvariable=self.seven_zip_var).grid(row=2, column=1, columnspan=2, sticky="ew", padx=(8, 8), pady=(9, 0))
        ttk.Button(options, text="浏览…", command=self._choose_7zip).grid(row=2, column=3, sticky="e", pady=(9, 0))

        log_header = ttk.Frame(outer)
        log_header.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(8, 4))
        log_header.columnconfigure(0, weight=1)
        ttk.Label(log_header, text="运行日志", font=("Microsoft YaHei UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        self.progress = ttk.Progressbar(log_header, mode="indeterminate", length=150)
        self.progress.grid(row=0, column=1, sticky="e")

        self.log_text = scrolledtext.ScrolledText(outer, height=14, wrap="word", state="disabled", font=("Consolas", 9))
        self.log_text.grid(row=8, column=0, columnspan=3, sticky="nsew")
        outer.rowconfigure(8, weight=1)

        bottom = ttk.Frame(outer)
        bottom.grid(row=9, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        bottom.columnconfigure(0, weight=1)
        ttk.Label(bottom, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        self.open_button = ttk.Button(bottom, text="打开输出目录", command=self._open_output, state="disabled")
        self.open_button.grid(row=0, column=1, padx=(0, 8))
        self.cancel_button = ttk.Button(bottom, text="取消", command=self._cancel, state="disabled")
        self.cancel_button.grid(row=0, column=2, padx=(0, 8))
        self.start_button = ttk.Button(bottom, text="开始递归解压", command=self._start, style="Accent.TButton")
        self.start_button.grid(row=0, column=3)

        note = ttk.Label(
            outer,
            text="安全提示：原始文件不会被修改；遇到不安全路径会拒绝解压。达到层数上限时会保留并报告剩余压缩包。",
            foreground="#555555",
            wraplength=840,
        )
        note.grid(row=10, column=0, columnspan=3, sticky="w", pady=(10, 0))

    def _choose_source(self) -> None:
        selected = filedialog.askopenfilename(
            title="选择待处理文件",
            filetypes=[("压缩包及任意文件", "*.zip *.7z *.rar *.001 *.bin *.dat *.gz *.xz *.bz2"), ("所有文件", "*.*")],
        )
        if selected:
            self.source_var.set(selected)
            if not self.output_var.get().strip():
                self.output_var.set(str(Path(selected).parent / "Unpacked"))

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

    def _append_log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        source_text = self.source_var.get().strip().strip('"')
        output_text = self.output_var.get().strip().strip('"')
        if not source_text or not Path(source_text).is_file():
            messagebox.showerror("缺少输入文件", "请先选择一个存在的待处理文件。", parent=self.root)
            return
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
        passwords = self.password_text.get("1.0", "end-1c").splitlines()
        bandizip_path = self.bandizip_var.get().strip().strip('"') or None
        seven_zip_path = self.seven_zip_var.get().strip().strip('"') or None
        delete_nested = bool(self.delete_nested_var.get())
        self.cancel_event = threading.Event()
        self.start_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.open_button.configure(state="disabled")
        self.progress.start(10)
        self.status_var.set("正在处理…")
        self._append_log("\n" + "=" * 54)

        def worker_main() -> None:
            try:
                report = extract_recursive(
                    source_text,
                    output_text,
                    extensions=extensions,
                    passwords=passwords,
                    max_depth=max_depth,
                    bandizip_path=bandizip_path,
                    seven_zip_path=seven_zip_path,
                    delete_nested=delete_nested,
                    log=lambda line: self.messages.put(("log", line)),
                    cancel_event=self.cancel_event,
                )
                self.messages.put(("done", report))
            except ExtractionCancelled:
                self.messages.put(("cancelled", None))
            except Exception as exc:
                self.messages.put(("error", str(exc)))

        self.worker = threading.Thread(target=worker_main, name="recursive-unpack-worker", daemon=True)
        self.worker.start()

    def _cancel(self) -> None:
        if self.cancel_event:
            self.cancel_event.set()
            self.status_var.set("正在请求取消；当前解压操作结束后停止…")
            self._append_log("已请求取消。")
            self.cancel_button.configure(state="disabled")

    def _finish(self) -> None:
        self.progress.stop()
        self.start_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")

    def _poll_messages(self) -> None:
        try:
            while True:
                kind, payload = self.messages.get_nowait()
                if kind == "log":
                    self._append_log(str(payload))
                elif kind == "done":
                    report = payload
                    assert isinstance(report, ExtractionReport)
                    self.last_output = report.output_dir
                    self.open_button.configure(state="normal")
                    self._finish()
                    if report.remaining_archives:
                        self.status_var.set(f"处理结束：仍有 {len(report.remaining_archives)} 个压缩包未展开")
                        messagebox.showwarning(
                            "处理结束，但未完全展开",
                            f"已解压 {report.extracted_archives} 个压缩包。\n仍有 {len(report.remaining_archives)} 个压缩包或疑似压缩包未展开。\n\n输出目录：{report.output_dir}",
                            parent=self.root,
                        )
                    else:
                        self.status_var.set(f"完成：已解压 {report.extracted_archives} 个压缩包")
                        messagebox.showinfo(
                            "递归解压完成",
                            f"已解压 {report.extracted_archives} 个压缩包。\n\n输出目录：{report.output_dir}",
                            parent=self.root,
                        )
                elif kind == "error":
                    self._finish()
                    self.status_var.set("处理失败")
                    self._append_log(f"错误：{payload}")
                    messagebox.showerror("解压失败", str(payload), parent=self.root)
                elif kind == "cancelled":
                    self._finish()
                    self.status_var.set("已取消")
                    self._append_log("操作已取消。")
        except queue.Empty:
            pass
        self.root.after(100, self._poll_messages)

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


def launch_app() -> None:
    root = tk.Tk()
    RecursiveUnpackerApp(root)
    root.mainloop()
