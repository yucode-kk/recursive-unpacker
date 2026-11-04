"""Widget construction for the workbench shell.

Kept apart from :mod:`gui` so that module stays focused on behaviour. Each builder
takes the app instance and attaches the widgets it creates to it.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

from . import __version__
from .theme import ACCENT, BG_SURFACE, BORDER, LOG_TAGS, TEXT

if TYPE_CHECKING:
    from .gui import RecursiveUnpackerApp

# Minimum number of log lines; the panel then grows with the window.
LOG_HEIGHT = 12

_MISSING_COLOUR = "#A32D2D"
_METRICS = (("found", "发现"), ("ok", "成功"), ("failed", "失败"), ("remaining", "未展开"))


def make_text(
    app: RecursiveUnpackerApp,
    parent: tk.Misc,
    *,
    height: int,
    width: int,
    wrap: str,
    mono: bool,
    state: str,
) -> tk.Text:
    """Themed ``Text``; ``ScrolledText`` would drag in an unthemed scrollbar.

    ``width`` is mandatory because ``tk.Text`` otherwise defaults to 80 columns,
    which would blow the two-column layout far past the window width.
    """
    return tk.Text(
        parent,
        height=height,
        width=width,
        wrap=wrap,
        state=state,
        font=app.fonts.mono if mono else app.fonts.body,
        background=BG_SURFACE,
        foreground=TEXT,
        insertbackground=TEXT,
        relief="flat",
        borderwidth=0,
        highlightthickness=1,
        highlightbackground=BORDER,
        highlightcolor=ACCENT,
        padx=10,
        pady=8,
        spacing1=1,
        spacing3=1,
    )


def build_header(app: RecursiveUnpackerApp, outer: ttk.Frame) -> None:
    header = ttk.Frame(outer)
    header.grid(row=0, column=0, columnspan=2, sticky="ew")
    header.columnconfigure(0, weight=1)
    ttk.Label(header, text="递归解压助手", style="Heading.TLabel").grid(row=0, column=0, sticky="w")
    ttk.Label(header, text=f"v{__version__}", style="Pill.TLabel").grid(row=0, column=1, sticky="e")
    ttk.Label(
        header,
        text="批量处理文件与文件夹，自动识别错误后缀、轮询候选密码，并递归展开内层压缩包。",
        style="Muted.TLabel",
    ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))


def build_status_strip(app: RecursiveUnpackerApp, outer: ttk.Frame) -> None:
    strip = ttk.Frame(outer, style="Strip.TFrame", padding=(10, 7))
    strip.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 12))
    strip.columnconfigure(0, weight=1)
    app.status_pill = ttk.Label(strip, textvariable=app.status_var, style="Pill.TLabel")
    app.status_pill.grid(row=0, column=0, sticky="w")
    ttk.Label(strip, textvariable=app.summary_var, style="Muted.OnStrip.TLabel").grid(
        row=0, column=1, sticky="e"
    )


def build_sources_panel(app: RecursiveUnpackerApp, parent: ttk.Frame) -> None:
    panel = ttk.LabelFrame(parent, text="待处理压缩包（可混合添加文件与文件夹）")
    panel.grid(row=0, column=0, columnspan=3, sticky="nsew")
    panel.columnconfigure(0, weight=1)

    app.source_tree = ttk.Treeview(
        panel,
        columns=("path", "kind"),
        show="headings",
        height=4,
        selectmode="extended",
    )
    app.source_tree.heading("path", text="路径", anchor="w")
    app.source_tree.heading("kind", text="类型", anchor="center")
    app.source_tree.column("path", width=380, stretch=True, anchor="w")
    app.source_tree.column("kind", width=80, stretch=False, anchor="center")
    app.source_tree.tag_configure("volume", foreground=ACCENT)
    app.source_tree.tag_configure("missing", foreground=_MISSING_COLOUR)
    app.source_tree.grid(row=0, column=0, sticky="nsew")
    scrollbar = ttk.Scrollbar(panel, orient="vertical", command=app.source_tree.yview)
    scrollbar.grid(row=0, column=1, sticky="ns", padx=(3, 0))
    app.source_tree.configure(yscrollcommand=scrollbar.set)
    app.source_tree.bind("<<TreeviewSelect>>", app._on_source_select)

    buttons = ttk.Frame(panel)
    buttons.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
    buttons.columnconfigure(4, weight=1)

    app.add_files_button = ttk.Button(buttons, text="添加文件…", command=app._add_files)
    app.add_files_button.grid(row=0, column=0)
    app.add_folder_button = ttk.Button(buttons, text="添加文件夹…", command=app._add_folder)
    app.add_folder_button.grid(row=0, column=1, padx=(6, 0))
    app.remove_button = ttk.Button(buttons, text="移除所选", command=app._remove_selected)
    app.remove_button.grid(row=0, column=2, padx=(6, 0))
    app.clear_button = ttk.Button(buttons, text="清空列表", command=app._clear_sources)
    app.clear_button.grid(row=0, column=3, padx=(6, 0))
    ttk.Label(buttons, textvariable=app.source_count_var, style="Muted.TLabel").grid(
        row=0, column=5, sticky="e"
    )


def build_overview(app: RecursiveUnpackerApp, parent: ttk.Frame) -> None:
    panel = ttk.LabelFrame(parent, text="运行概览")
    panel.grid(row=0, column=0, sticky="ew")
    for index in range(len(_METRICS)):
        panel.columnconfigure(index, weight=1)
    for index, (key, caption) in enumerate(_METRICS):
        cell = ttk.Frame(panel, style="Card.TFrame", padding=(10, 8))
        cell.grid(row=0, column=index, sticky="ew", padx=(0, 8) if index < 3 else (0, 0))
        ttk.Label(cell, text=caption, style="MetricCaption.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(cell, textvariable=app.metric_vars[key], style="MetricValue.TLabel").grid(
            row=1, column=0, sticky="w"
        )


def build_log_panel(app: RecursiveUnpackerApp, parent: ttk.Frame) -> None:
    panel = ttk.LabelFrame(parent, text="运行日志")
    panel.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
    panel.columnconfigure(0, weight=1)
    panel.rowconfigure(1, weight=1)

    header = ttk.Frame(panel)
    header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
    header.columnconfigure(0, weight=1)
    ttk.Label(header, textvariable=app.progress_var, style="Muted.TLabel").grid(
        row=0, column=0, sticky="w"
    )
    app.progress = ttk.Progressbar(
        header,
        mode="determinate",
        maximum=1,
        value=0,
        length=150,
        style="Accent.Horizontal.TProgressbar",
    )
    app.progress.grid(row=0, column=1, sticky="e")

    app.log_text = make_text(
        app, panel, height=LOG_HEIGHT, width=44, wrap="word", mono=True, state="disabled"
    )
    app.log_text.grid(row=1, column=0, sticky="nsew")
    scrollbar = ttk.Scrollbar(panel, orient="vertical", command=app.log_text.yview)
    scrollbar.grid(row=1, column=1, sticky="ns", padx=(3, 0))
    app.log_text.configure(yscrollcommand=scrollbar.set)
    for name, colour in LOG_TAGS.items():
        app.log_text.tag_configure(name, foreground=colour)


def build_advanced(app: RecursiveUnpackerApp, parent: ttk.Frame) -> None:
    app.advanced_var = tk.BooleanVar(value=False)
    toggle = ttk.Checkbutton(
        parent,
        text="高级设置（递归层数、外部解压程序）",
        variable=app.advanced_var,
        command=app._toggle_advanced,
    )
    toggle.grid(row=5, column=0, columnspan=3, sticky="w", pady=(12, 0))

    frame = ttk.LabelFrame(parent, text="高级设置", padding=(10, 6))
    frame.columnconfigure(1, weight=1)
    app.advanced_frame = frame

    ttk.Label(frame, text="最大递归层数").grid(row=0, column=0, sticky="w")
    app.depth_spin = ttk.Spinbox(frame, from_=1, to=20, width=5, textvariable=app.depth_var)
    app.depth_spin.grid(row=0, column=1, sticky="w", padx=(10, 20))
    app.subfolder_check = ttk.Checkbutton(
        frame, text="包含子文件夹（扫描子目录）", variable=app.include_subfolders_var
    )
    app.subfolder_check.grid(row=0, column=2, sticky="w", padx=(0, 20))
    app.delete_nested_check = ttk.Checkbutton(
        frame, text="内层压缩包成功解压后删除原压缩包", variable=app.delete_nested_var
    )
    app.delete_nested_check.grid(row=0, column=3, columnspan=2, sticky="w")

    ttk.Label(frame, text="Bandizip 控制台路径（优先，需 bz.exe）").grid(
        row=1, column=0, sticky="w", pady=(8, 0)
    )
    app.bandizip_entry = ttk.Entry(frame, textvariable=app.bandizip_var)
    app.bandizip_entry.grid(row=1, column=1, columnspan=3, sticky="ew", padx=(10, 8), pady=(8, 0))
    app.bandizip_button = ttk.Button(frame, text="浏览…", command=app._choose_bandizip)
    app.bandizip_button.grid(row=1, column=4, sticky="e", pady=(8, 0))

    ttk.Label(frame, text="7-Zip 路径（可选回退）").grid(row=2, column=0, sticky="w", pady=(8, 0))
    app.seven_zip_entry = ttk.Entry(frame, textvariable=app.seven_zip_var)
    app.seven_zip_entry.grid(row=2, column=1, columnspan=3, sticky="ew", padx=(10, 8), pady=(8, 0))
    app.seven_zip_button = ttk.Button(frame, text="浏览…", command=app._choose_7zip)
    app.seven_zip_button.grid(row=2, column=4, sticky="e", pady=(8, 0))
