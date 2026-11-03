from __future__ import annotations

from pathlib import Path

import pytest

import recursive_unpacker.config as config
from recursive_unpacker.gui import _elide, _path_key


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


def test_app_refills_and_persists_settings(tmp_config: Path) -> None:
    tk = pytest.importorskip("tkinter")
    config.save_config(
        config.AppConfig(passwords=["alpha", "beta"], include_subfolders=False), tmp_config
    )

    from recursive_unpacker.gui import RecursiveUnpackerApp

    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available")
    root.withdraw()

    app = RecursiveUnpackerApp(root)
    try:
        assert app.config_path == tmp_config
        assert app.password_text.get("1.0", "end-1c") == "alpha\nbeta"
        assert app.include_subfolders_var.get() is False
        app.output_var.set("D:/somewhere")
        app._on_close()
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass

    assert config.load_config(tmp_config).output_dir == "D:/somewhere"
