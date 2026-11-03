from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

import recursive_unpacker.config as config
import recursive_unpacker.core as core


@pytest.fixture
def zip_bytes():
    """Build an in-memory ZIP so tests can write archives with any (wrong) suffix."""

    def build(entries: dict[str, bytes | str]) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, value in entries.items():
                archive.writestr(name, value)
        return buffer.getvalue()

    return build


@pytest.fixture
def no_external_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hide any locally installed Bandizip/7-Zip so results are machine independent.

    Deliberately not autouse: the ``find_*_executable`` discovery tests need the real
    lookup behaviour.
    """
    monkeypatch.setattr(core, "find_bandizip_executable", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(core, "find_7zip_executable", lambda *_args, **_kwargs: None)


@pytest.fixture
def tmp_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the settings path into ``tmp_path`` so tests never touch real settings."""
    program = tmp_path / "app"
    appdata = tmp_path / "appdata"
    monkeypatch.setattr(config, "program_dir", lambda: program)
    monkeypatch.setattr(config, "appdata_config_path", lambda: appdata / config.CONFIG_FILENAME)
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("LOCALAPPDATA", str(appdata))
    return program / config.CONFIG_FILENAME
