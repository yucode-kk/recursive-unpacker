from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import recursive_unpacker.config as config


def _write(target: Path, text: str) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def test_save_then_load_round_trip(tmp_config: Path) -> None:
    original = config.AppConfig(
        output_dir="D:/out",
        extensions=".zip, .rar",
        max_depth=7,
        bandizip_path="D:/tools/bz.exe",
        seven_zip_path="D:/tools/7z.exe",
        delete_nested=False,
        include_subfolders=False,
        passwords=["alpha", "beta"],
    )

    written = config.save_config(original, tmp_config)

    assert written == tmp_config
    assert config.load_config(tmp_config) == original


def test_save_preserves_non_ascii_passwords(tmp_config: Path) -> None:
    config.save_config(config.AppConfig(passwords=["密码123"]), tmp_config)

    assert config.load_config(tmp_config).passwords == ["密码123"]
    assert "密码123" in tmp_config.read_text(encoding="utf-8")


def test_load_missing_file_returns_defaults_without_creating_file(tmp_config: Path) -> None:
    loaded = config.load_config(tmp_config)

    assert loaded == config.AppConfig()
    assert not tmp_config.exists()


def test_load_corrupt_json_backs_up_and_rebuilds(tmp_config: Path) -> None:
    _write(tmp_config, "{broken")
    notes: list[str] = []

    loaded = config.load_config(tmp_config, warn=notes.append)

    assert loaded == config.AppConfig()
    backup = tmp_config.with_name(tmp_config.name + config.BACKUP_SUFFIX)
    assert backup.read_text(encoding="utf-8") == "{broken"
    assert json.loads(tmp_config.read_text(encoding="utf-8"))["version"] == config.CONFIG_VERSION
    assert any("已备份" in note for note in notes)


def test_load_treats_non_object_json_as_corrupt(tmp_config: Path) -> None:
    _write(tmp_config, "[1, 2, 3]")
    notes: list[str] = []

    loaded = config.load_config(tmp_config, warn=notes.append)

    assert loaded == config.AppConfig()
    assert any("JSON 对象" in note for note in notes)


def test_load_partial_dict_fills_defaults_and_ignores_unknown(tmp_config: Path) -> None:
    _write(
        tmp_config,
        json.dumps({"max_depth": 9, "nope": 1, "passwords": ["a", 3, "", "b", "a"]}),
    )

    loaded = config.load_config(tmp_config)

    assert loaded.max_depth == 9
    assert loaded.extensions == config.DEFAULT_EXTENSIONS
    assert loaded.delete_nested is True
    assert loaded.include_subfolders is True
    assert loaded.passwords == ["a", "b"]


@pytest.mark.parametrize(
    "raw, expected_depth",
    [({"max_depth": "abc"}, 5), ({"max_depth": 99}, 5), ({"max_depth": 0}, 5), ({"max_depth": True}, 5)],
)
def test_load_coerces_bad_depth(tmp_config: Path, raw: dict[str, object], expected_depth: int) -> None:
    _write(tmp_config, json.dumps(raw))

    assert config.load_config(tmp_config).max_depth == expected_depth


def test_load_coerces_bad_types(tmp_config: Path) -> None:
    _write(
        tmp_config,
        json.dumps(
            {
                "output_dir": 5,
                "extensions": None,
                "delete_nested": 0,
                "include_subfolders": "no",
                "passwords": "secret",
                "bandizip_path": ["nope"],
            }
        ),
    )

    loaded = config.load_config(tmp_config)

    assert loaded.output_dir == ""
    assert loaded.extensions == config.DEFAULT_EXTENSIONS
    assert loaded.delete_nested is True
    assert loaded.include_subfolders is True
    assert loaded.passwords == []
    assert loaded.bandizip_path == ""


def test_load_drops_passwords_with_control_characters(tmp_config: Path) -> None:
    _write(tmp_config, json.dumps({"passwords": ["ok", "bad\x00value"]}))

    assert config.load_config(tmp_config).passwords == ["ok"]


def test_load_warns_when_config_version_is_newer(tmp_config: Path) -> None:
    _write(tmp_config, json.dumps({"version": 99, "max_depth": 6}))
    notes: list[str] = []

    loaded = config.load_config(tmp_config, warn=notes.append)

    assert loaded.max_depth == 6
    assert loaded.version == config.CONFIG_VERSION
    assert any("版本较新" in note for note in notes)


def test_save_leaves_no_temp_files(tmp_config: Path) -> None:
    config.save_config(config.AppConfig(), tmp_config)
    config.save_config(config.AppConfig(passwords=["x"]), tmp_config)

    leftovers = [path.name for path in tmp_config.parent.iterdir() if path.name.startswith(".config-")]
    assert leftovers == []


def test_save_replaces_through_same_directory_temp_file(
    tmp_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Path, Path]] = []
    original = os.replace

    def tracking(source: str | Path, destination: str | Path) -> None:
        calls.append((Path(source), Path(destination)))
        original(source, destination)

    monkeypatch.setattr(config.os, "replace", tracking)

    written = config.save_config(config.AppConfig(), tmp_config)

    source, destination = calls[-1]
    assert source.parent == destination.parent
    assert destination == written
    assert source.name.startswith(".config-")
    assert not source.exists()


def test_resolve_uses_program_dir_when_writable(tmp_config: Path) -> None:
    location = config.resolve_config_path()

    assert location.path == tmp_config
    assert location.is_fallback is False
    assert location.note == ""


def test_resolve_falls_back_to_appdata(tmp_config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "is_writable_dir", lambda _directory: False)

    location = config.resolve_config_path()

    assert location.is_fallback is True
    assert location.note
    assert location.path == config.appdata_config_path()
    assert location.path != tmp_config


def test_program_dir_prefers_executable_when_frozen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "tools" / "RecursiveUnpacker.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"")
    monkeypatch.setattr(config.sys, "frozen", True, raising=False)
    monkeypatch.setattr(config.sys, "executable", str(executable))

    assert config.program_dir() == executable.parent


def test_program_dir_uses_project_root_when_not_frozen() -> None:
    assert config.program_dir() == Path(config.__file__).resolve().parent.parent


def test_save_falls_back_when_primary_write_fails(
    tmp_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []
    original = config._write_atomic

    def flaky(target: Path, payload: str) -> None:
        calls.append(Path(target))
        if len(calls) == 1:
            raise OSError("read-only directory")
        original(target, payload)

    monkeypatch.setattr(config, "_write_atomic", flaky)

    written = config.save_config(config.AppConfig(passwords=["keep"]), tmp_config)

    assert len(calls) == 2
    assert written != tmp_config
    assert written == config.appdata_config_path()
    assert config.load_config(written).passwords == ["keep"]


def test_save_raises_config_error_when_all_paths_fail(
    tmp_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def always_fail(target: Path, payload: str) -> None:
        raise OSError("no permission")

    monkeypatch.setattr(config, "_write_atomic", always_fail)

    with pytest.raises(config.ConfigError, match="配置保存失败"):
        config.save_config(config.AppConfig(), tmp_config)


def test_passwords_are_hidden_from_repr() -> None:
    assert "s3cret" not in repr(config.AppConfig(passwords=["s3cret"]))


def test_text_round_trip_preserves_order_and_spaces() -> None:
    passwords = ["  leading", "trailing  ", "with space"]

    assert config.text_to_passwords(config.passwords_to_text(passwords)) == passwords


def test_text_to_passwords_drops_blank_lines_and_duplicates() -> None:
    assert config.text_to_passwords("a\n\n  \nb\n\r\nc\na") == ["a", "b", "c"]


def test_text_to_passwords_handles_windows_newlines() -> None:
    assert config.text_to_passwords("alpha\r\nbeta\r\n") == ["alpha", "beta"]
