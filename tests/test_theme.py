from __future__ import annotations

import pytest

import recursive_unpacker.theme as theme


def test_first_available_prefers_the_installed_family() -> None:
    assert theme._first_available(("A", "B", "C"), {"B", "C"}) == "B"


def test_first_available_falls_back_to_the_last_candidate() -> None:
    assert theme._first_available(("A", "B", "C"), set()) == "C"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("解压成功：archive.zip", "ok"),
        ("[3/3] 失败：corrupt.rar — 所有候选密码均未通过", "error"),
        ("警告：分卷不完整，缺少 .002", "warn"),
        ("跳过不存在的路径：D:/gone.zip", "warn"),
        ("本轮未成功（Bad7zFile）", "warn"),
        ("配置文件：D:/app/config.json", "dim"),
        ("[1/3] 开始处理 archive.zip", None),
    ],
)
def test_log_tag_for_classifies_by_keyword(message: str, expected: str | None) -> None:
    assert theme.log_tag_for(message) == expected


def test_apply_theme_paints_widgets_and_resolves_fonts(tk_root) -> None:
    from tkinter import ttk

    fonts = theme.apply_theme(tk_root)
    style = ttk.Style(tk_root)

    assert style.theme_use() == "clam"
    assert style.lookup("Accent.TButton", "background") == theme.ACCENT
    assert style.lookup("TFrame", "background") == theme.BG_APP
    assert int(style.lookup("Treeview", "rowheight")) == 26
    # clam does not grey out disabled widgets on its own.
    assert ("disabled", theme.TEXT_FAINT) in style.map("TButton")["foreground"]
    assert fonts.body[0] in theme._UI_CANDIDATES
    assert fonts.mono[0] in theme._MONO_CANDIDATES
