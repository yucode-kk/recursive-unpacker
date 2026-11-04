"""Visual identity for the Tkinter shell.

Colours, fonts and ``ttk`` style configuration live here so that :mod:`gui`
stays about behaviour. Everything is built on the stock ``clam`` theme — it is
the only bundled theme that lets us recolour widgets, and it needs no extra
dependency, which keeps the packaged EXE size unchanged.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk

# --------------------------------------------------------------------- palette

BG_APP = "#F7F8FA"
BG_SURFACE = "#FFFFFF"
BG_SUBTLE = "#F2F4F7"
BORDER = "#E4E7EC"
BORDER_STRONG = "#D0D5DD"

TEXT = "#344054"
TEXT_HEADING = "#101828"
TEXT_MUTED = "#667085"
TEXT_FAINT = "#98A2B3"

ACCENT = "#185FA5"
ACCENT_HOVER = "#0C447C"
ACCENT_PRESSED = "#042C53"
ACCENT_SOFT = "#E6F1FB"
ON_ACCENT = "#FFFFFF"

SUCCESS = "#185FA5"
ERROR = "#A32D2D"
WARNING = "#854F0B"

# Log line colours, keyed by the tag name stored on each ``Text`` insert.
LOG_TAGS = {
    "ok": SUCCESS,
    "error": ERROR,
    "warn": WARNING,
    "dim": TEXT_FAINT,
}

_UI_CANDIDATES = ("Microsoft YaHei UI", "微软雅黑", "Segoe UI", "Tahoma")
_MONO_CANDIDATES = ("Cascadia Mono", "Consolas", "Courier New")


@dataclass(frozen=True)
class Fonts:
    """Resolved font tuples, ready to hand to Tk widgets."""

    title: tuple
    heading: tuple
    body: tuple
    body_bold: tuple
    small: tuple
    mono: tuple
    metric: tuple


def _first_available(candidates: tuple[str, ...], families: set[str]) -> str:
    for name in candidates:
        if name in families:
            return name
    return candidates[-1]


def resolve_fonts(root: tk.Misc) -> Fonts:
    """Pick the first available UI/mono family so machines without *Cascadia Mono*
    (or without *Microsoft YaHei*) still lay out sanely."""
    families = set(str(name) for name in root.tk.call("font", "families"))
    ui = _first_available(_UI_CANDIDATES, families)
    mono = _first_available(_MONO_CANDIDATES, families)
    return Fonts(
        title=(ui, 16, "bold"),
        heading=(ui, 10, "bold"),
        body=(ui, 9),
        body_bold=(ui, 9, "bold"),
        small=(ui, 8),
        mono=(mono, 9),
        metric=(ui, 14, "bold"),
    )


def apply_theme(root: tk.Tk) -> Fonts:
    """Switch to ``clam`` and paint every widget style we use. Returns the fonts
    so callers can pass them to classic widgets (``Text``/``Menu``), which do not
    inherit ``ttk`` styles."""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    fonts = resolve_fonts(root)
    root.option_add("*Font", fonts.body)

    style.configure(
        ".",
        background=BG_APP,
        foreground=TEXT,
        fieldbackground=BG_SURFACE,
        bordercolor=BORDER,
        lightcolor=BG_SURFACE,
        darkcolor=BORDER,
        troughcolor=BG_SUBTLE,
        selectbackground=ACCENT_SOFT,
        selectforeground=TEXT_HEADING,
        font=fonts.body,
    )

    style.configure("TFrame", background=BG_APP)
    style.configure("Strip.TFrame", background=BG_SUBTLE)
    style.configure("Card.TFrame", background=BG_SURFACE)

    style.configure("TLabel", background=BG_APP, foreground=TEXT)
    style.configure("Heading.TLabel", font=fonts.title, foreground=TEXT_HEADING)
    style.configure("Section.TLabel", font=fonts.heading, foreground=TEXT_HEADING)
    style.configure("Muted.TLabel", foreground=TEXT_MUTED)
    style.configure("Faint.TLabel", foreground=TEXT_FAINT)
    style.configure("OnStrip.TLabel", background=BG_SUBTLE, foreground=TEXT)
    style.configure("Muted.OnStrip.TLabel", background=BG_SUBTLE, foreground=TEXT_MUTED)

    style.configure(
        "Pill.TLabel",
        background=BG_SUBTLE,
        foreground=TEXT_MUTED,
        padding=(8, 3),
        font=fonts.small,
    )
    for name, background, foreground in (
        ("Run.Pill.TLabel", ACCENT_SOFT, ACCENT),
        ("Ok.Pill.TLabel", "#B5D4F4", "#0C447C"),
        ("Warn.Pill.TLabel", "#FAEEDA", WARNING),
        ("Error.Pill.TLabel", "#FCEBEB", ERROR),
    ):
        style.configure(name, background=background, foreground=foreground, padding=(8, 3), font=fonts.small)

    style.configure(
        "TLabelframe",
        background=BG_APP,
        bordercolor=BORDER,
        relief="solid",
        borderwidth=1,
        padding=(12, 10),
    )
    style.configure(
        "MetricCaption.TLabel",
        background=BG_SURFACE,
        foreground=TEXT_MUTED,
        font=fonts.small,
    )
    style.configure(
        "MetricValue.TLabel",
        background=BG_SURFACE,
        foreground=TEXT_HEADING,
        font=fonts.metric,
    )
    style.configure(
        "TLabelframe.Label",
        background=BG_APP,
        foreground=TEXT_HEADING,
        font=fonts.heading,
    )

    style.configure(
        "TButton",
        background=BG_SURFACE,
        foreground=TEXT,
        bordercolor=BORDER_STRONG,
        lightcolor=BG_SURFACE,
        darkcolor=BORDER_STRONG,
        focuscolor=ACCENT_SOFT,
        padding=(11, 5),
        font=fonts.body,
    )
    style.map(
        "TButton",
        background=[("active", BG_SUBTLE), ("disabled", BG_SURFACE)],
        foreground=[("disabled", TEXT_FAINT)],
        bordercolor=[("focus", ACCENT), ("active", BORDER_STRONG)],
    )
    style.configure(
        "Accent.TButton",
        background=ACCENT,
        foreground=ON_ACCENT,
        bordercolor=ACCENT,
        lightcolor=ACCENT,
        darkcolor=ACCENT,
        focuscolor=ACCENT,
        padding=(14, 6),
        font=fonts.body_bold,
    )
    style.map(
        "Accent.TButton",
        background=[("active", ACCENT_HOVER), ("pressed", ACCENT_PRESSED), ("disabled", BORDER)],
        foreground=[("disabled", TEXT_MUTED)],
        bordercolor=[("disabled", BORDER)],
    )

    style.configure(
        "TEntry",
        fieldbackground=BG_SURFACE,
        foreground=TEXT,
        bordercolor=BORDER_STRONG,
        lightcolor=BORDER_STRONG,
        darkcolor=BORDER_STRONG,
        insertcolor=TEXT,
        padding=(7, 5),
        font=fonts.body,
    )
    style.map(
        "TEntry",
        fieldbackground=[("disabled", BG_SUBTLE), ("readonly", BG_SURFACE)],
        foreground=[("disabled", TEXT_FAINT)],
        bordercolor=[("focus", ACCENT)],
    )

    # ``clam`` paints the indicator with ``indicatorbackground`` (box fill) and
    # ``indicatorforeground`` (the tick). Leaving the tick at its default makes it
    # invisible against a filled accent box, so both are mapped together.
    style.configure(
        "TCheckbutton",
        background=BG_APP,
        foreground=TEXT,
        indicatorcolor=BG_SURFACE,
        indicatorbackground=BG_SURFACE,
        indicatorforeground=TEXT,
        upperbordercolor=BORDER_STRONG,
        lowerbordercolor=BORDER_STRONG,
        bordercolor=BORDER_STRONG,
        focuscolor=ACCENT_SOFT,
        padding=(4, 3),
        font=fonts.body,
    )
    style.map(
        "TCheckbutton",
        indicatorcolor=[("selected", ACCENT), ("disabled", BG_SUBTLE)],
        indicatorbackground=[("selected", ACCENT), ("disabled", BG_SUBTLE)],
        indicatorforeground=[("selected", ON_ACCENT), ("disabled", TEXT_FAINT)],
        background=[("active", BG_APP)],
        foreground=[("disabled", TEXT_FAINT)],
    )
    style.configure(
        "OnStrip.TCheckbutton",
        background=BG_SUBTLE,
        foreground=TEXT,
        indicatorbackground=BG_SURFACE,
        indicatorforeground=TEXT,
        upperbordercolor=BORDER_STRONG,
        lowerbordercolor=BORDER_STRONG,
        font=fonts.body,
    )
    style.map(
        "OnStrip.TCheckbutton",
        indicatorbackground=[("selected", ACCENT), ("disabled", BG_SUBTLE)],
        indicatorforeground=[("selected", ON_ACCENT), ("disabled", TEXT_FAINT)],
        background=[("active", BG_SUBTLE)],
        foreground=[("disabled", TEXT_FAINT)],
    )

    style.configure(
        "TSpinbox",
        fieldbackground=BG_SURFACE,
        foreground=TEXT,
        bordercolor=BORDER_STRONG,
        lightcolor=BORDER_STRONG,
        darkcolor=BORDER_STRONG,
        insertcolor=TEXT,
        arrowcolor=TEXT_MUTED,
        padding=(7, 5),
        font=fonts.body,
    )
    style.map(
        "TSpinbox",
        fieldbackground=[("disabled", BG_SUBTLE)],
        foreground=[("disabled", TEXT_FAINT)],
        bordercolor=[("focus", ACCENT)],
        arrowcolor=[("disabled", TEXT_FAINT)],
    )

    style.configure(
        "Accent.Horizontal.TProgressbar",
        background=ACCENT,
        troughcolor=BG_SUBTLE,
        bordercolor=BORDER,
        lightcolor=ACCENT,
        darkcolor=ACCENT,
        thickness=6,
    )

    style.configure(
        "Treeview",
        background=BG_SURFACE,
        fieldbackground=BG_SURFACE,
        foreground=TEXT,
        bordercolor=BORDER,
        rowheight=26,
        font=fonts.body,
    )
    style.configure(
        "Treeview.Heading",
        background=BG_SUBTLE,
        foreground=TEXT_MUTED,
        bordercolor=BORDER,
        relief="flat",
        font=fonts.body_bold,
    )
    style.map(
        "Treeview",
        background=[("selected", ACCENT_SOFT)],
        foreground=[("selected", TEXT_HEADING)],
    )

    style.configure(
        "Vertical.TScrollbar",
        background=BG_SUBTLE,
        troughcolor=BG_APP,
        bordercolor=BORDER,
        arrowcolor=TEXT_MUTED,
        lightcolor=BG_SUBTLE,
        darkcolor=BG_SUBTLE,
        relief="flat",
    )
    style.map("Vertical.TScrollbar", background=[("active", BORDER_STRONG)])

    return fonts


def log_tag_for(message: str) -> str | None:
    """Classify a log line so the text area can colour it.

    Matches on keywords rather than prefixes because the wording comes from
    several modules and is not guaranteed to stay prefix-stable.
    """
    if "失败" in message or message.startswith("!"):
        return "error"
    if any(
        word in message
        for word in ("警告", "跳过", "未成功", "未找到", "未展开", "不完整", "没有待处理", "缺少")
    ):
        return "warn"
    if "成功" in message or "完成" in message:
        return "ok"
    if message.startswith("配置") or "提示" in message or message.startswith("="):
        return "dim"
    return None
