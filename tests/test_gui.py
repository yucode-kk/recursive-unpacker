from __future__ import annotations

from pathlib import Path

import pytest

import recursive_unpacker.config as config
import recursive_unpacker.gui as gui
from recursive_unpacker.gui import _elide, _path_key


@pytest.fixture
def app(tk_root, tmp_config: Path):
    tk = pytest.importorskip("tkinter")
    window = tk.Toplevel(tk_root)
    window.withdraw()
    instance = gui.RecursiveUnpackerApp(window)
    yield instance
    try:
        window.destroy()
    except tk.TclError:
        pass


@pytest.fixture
def seeded_app(tk_root, tmp_config: Path):
    """An app built *after* the settings file is written, so refill can be observed."""
    config.save_config(
        config.AppConfig(passwords=["alpha", "beta"], include_subfolders=False), tmp_config
    )
    tk = pytest.importorskip("tkinter")
    window = tk.Toplevel(tk_root)
    window.withdraw()
    instance = gui.RecursiveUnpackerApp(window)
    yield instance
    try:
        window.destroy()
    except tk.TclError:
        pass


def test_elide_keeps_short_text_unchanged() -> None:
    assert _elide(r"D:\archives\a.zip") == r"D:\archives\a.zip"


def test_elide_keeps_the_informative_tail() -> None:
    long_path = "C:/very/deeply/nested/folder/structure/" + "segment/" * 10 + "final-archive.zip"

    result = _elide(long_path, 60)

    assert len(result) == 60
    assert "…" in result
    assert result.endswith("final-archive.zip")


def test_path_key_normalizes_separators(tmp_path: Path) -> None:
    assert _path_key(tmp_path / "a.zip") == _path_key(tmp_path / "a.zip")


def test_app_refills_and_persists_settings(seeded_app, tmp_config: Path) -> None:
    app = seeded_app
    assert app.config_path == tmp_config
    assert app.password_text.get("1.0", "end-1c") == "alpha\nbeta"
    assert app.include_subfolders_var.get() is False
    app.output_var.set("D:/somewhere")
    app._on_close()

    assert config.load_config(tmp_config).output_dir == "D:/somewhere"


def test_source_tree_labels_each_kind_and_flags_volumes(app, tmp_path: Path) -> None:
    archive = tmp_path / "plain.zip"
    archive.write_bytes(b"PK\x03\x04")
    first = tmp_path / "split.7z.001"
    first.write_bytes(b"x")
    (tmp_path / "split.7z.002").write_bytes(b"x")
    folder = tmp_path / "bundle"
    folder.mkdir()

    app._append_sources([archive, first, folder])
    rows = [app.source_tree.item(iid)["values"] for iid in app.source_tree.get_children()]
    assert [row[1] for row in rows] == ["文件", "分卷", "文件夹"]
    assert app.source_tree.item("1")["tags"] == ["volume"]
    assert not app.source_tree.item("0")["tags"]


def test_source_tree_marks_paths_that_no_longer_exist(app, tmp_path: Path) -> None:
    gone = tmp_path / "vanished.zip"
    gone.write_bytes(b"PK\x03\x04")

    app._append_sources([gone])
    gone.unlink()
    app._refresh_source_list()
    assert app.source_tree.item("0")["values"][1] == "不存在"
    assert app.source_tree.item("0")["tags"] == ["missing"]


def test_advanced_panel_starts_collapsed_and_toggles(app) -> None:
    assert app.advanced_frame.grid_info() == {}
    app.advanced_var.set(True)
    app._toggle_advanced()
    app.root.update_idletasks()
    assert app.advanced_frame.grid_info()

    app.advanced_var.set(False)
    app._toggle_advanced()
    app.root.update_idletasks()
    assert app.advanced_frame.grid_info() == {}


def test_workbench_has_input_and_telemetry_columns(app) -> None:
    # Sources and settings live in one column, telemetry in the other.
    assert app.source_tree.winfo_parent() != app.log_text.winfo_parent()
    assert set(app.metric_vars) == {"found", "ok", "failed", "remaining"}
    assert all(var.get() == "—" for var in app.metric_vars.values())
    assert app.progress_var.get() == "0 / 0"


def test_append_log_colours_lines_by_outcome(app) -> None:
    app._append_log("解压成功：archive.zip")
    app._append_log("[3/3] 失败：corrupt.rar — 所有候选密码均未通过")
    app._append_log("[1/3] 开始处理 archive.zip")

    assert app.log_text.tag_ranges("ok")
    assert app.log_text.tag_ranges("error")
    # A neutral line carries no tag at all.
    assert not app.log_text.tag_ranges("warn")


def test_metrics_are_filled_from_counts(app) -> None:
    app._apply_metrics(7, 2, 1)
    assert app.metric_vars["ok"].get() == "7"
    assert app.metric_vars["failed"].get() == "2"
    assert app.metric_vars["remaining"].get() == "1"


def test_status_pill_switches_style_with_state(app) -> None:
    app._set_status("正在处理 1/2", "run")
    assert app.status_pill.cget("style") == "Run.Pill.TLabel"
    app._set_status("完成", "ok")
    assert app.status_pill.cget("style") == "Ok.Pill.TLabel"
    app._set_status("就绪")
    assert app.status_pill.cget("style") == "Pill.TLabel"
