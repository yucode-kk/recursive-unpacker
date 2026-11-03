"""Local settings file: path resolution, tolerant loading and atomic saving."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable

APP_NAME = "RecursiveUnpacker"
CONFIG_FILENAME = "config.json"
BACKUP_SUFFIX = ".bak"
CONFIG_VERSION = 1

DEFAULT_EXTENSIONS = ".zip, .7z, .rar, .tar.gz"
DEFAULT_MAX_DEPTH = 5
MIN_DEPTH = 1
MAX_DEPTH = 20


class ConfigError(RuntimeError):
    """Raised when the settings file cannot be written anywhere."""


@dataclass(frozen=True)
class ConfigLocation:
    path: Path
    is_fallback: bool = False
    note: str = ""


@dataclass
class AppConfig:
    """User settings persisted next to the program.

    ``passwords`` is excluded from ``repr`` so an accidental ``print(config)`` or an
    exception message never carries the cleartext passwords into the logs.
    """

    version: int = CONFIG_VERSION
    output_dir: str = ""
    extensions: str = DEFAULT_EXTENSIONS
    max_depth: int = DEFAULT_MAX_DEPTH
    bandizip_path: str = ""
    seven_zip_path: str = ""
    delete_nested: bool = True
    include_subfolders: bool = True
    passwords: list[str] = field(default_factory=list, repr=False)


def program_dir() -> Path:
    """Return the directory that owns the running program.

    Frozen builds must use ``sys.executable``: under PyInstaller's onefile mode both
    ``__file__`` and ``sys._MEIPASS`` point into a ``%TEMP%\\_MEIxxxx`` folder that is
    deleted on exit, so settings written there would silently disappear.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def appdata_config_path() -> Path:
    """Return the per-user fallback location used when the program directory is read-only."""
    base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else (Path.home() / ".config")
    return root / APP_NAME / CONFIG_FILENAME


def is_writable_dir(directory: Path) -> bool:
    """Probe ``directory`` by actually writing a temporary file.

    ``os.access`` is unreliable on Windows for ACLs and UAC-redirected folders, so a
    real write is the only trustworthy check.
    """
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    probe_name: str = ""
    try:
        handle, probe_name = tempfile.mkstemp(prefix=".write-probe-", dir=directory)
        os.close(handle)
        return True
    except OSError:
        return False
    finally:
        if probe_name:
            try:
                os.unlink(probe_name)
            except OSError:
                pass


def resolve_config_path() -> ConfigLocation:
    """Pick the settings path, preferring the program directory."""
    primary = program_dir() / CONFIG_FILENAME
    if is_writable_dir(primary.parent):
        return ConfigLocation(path=primary)
    fallback = appdata_config_path()
    return ConfigLocation(
        path=fallback,
        is_fallback=True,
        note=f"程序目录不可写，配置改存到 {fallback}",
    )


def _warn(warn: Callable[[str], None] | None, message: str) -> None:
    if warn:
        warn(message)


def _as_text(value: object, default: str) -> str:
    return value if isinstance(value, str) else default


def _as_bool(value: object, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _as_depth(value: object, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value if MIN_DEPTH <= value <= MAX_DEPTH else default


def _as_passwords(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            continue
        if "\x00" in item or "\n" in item or "\r" in item:
            continue
        if item not in result:
            result.append(item)
    return result


def _from_mapping(raw: dict[str, object]) -> AppConfig:
    defaults = AppConfig()
    version = raw.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        version = CONFIG_VERSION
    return AppConfig(
        version=version,
        output_dir=_as_text(raw.get("output_dir"), defaults.output_dir),
        extensions=_as_text(raw.get("extensions"), defaults.extensions),
        max_depth=_as_depth(raw.get("max_depth"), defaults.max_depth),
        bandizip_path=_as_text(raw.get("bandizip_path"), defaults.bandizip_path),
        seven_zip_path=_as_text(raw.get("seven_zip_path"), defaults.seven_zip_path),
        delete_nested=_as_bool(raw.get("delete_nested"), defaults.delete_nested),
        include_subfolders=_as_bool(raw.get("include_subfolders"), defaults.include_subfolders),
        passwords=_as_passwords(raw.get("passwords")),
    )


def _serialize(config: AppConfig) -> str:
    payload = asdict(config)
    payload["version"] = CONFIG_VERSION
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _write_atomic(target: Path, payload: str) -> None:
    """Write ``payload`` to ``target`` through a same-directory temp file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=target.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, target)
    except BaseException:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise


def _same_path(left: Path, right: Path) -> bool:
    try:
        return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))
    except OSError:
        return False


def _backup_path(target: Path) -> Path:
    return target.with_name(target.name + BACKUP_SUFFIX)


def _recover_corrupt_file(
    target: Path, warn: Callable[[str], None] | None, reason: str
) -> AppConfig:
    backup = _backup_path(target)
    try:
        os.replace(target, backup)
    except OSError:
        try:
            shutil.copy2(target, backup)
        except OSError:
            _warn(warn, f"{reason}；同时无法备份原文件，将直接重建。")
            return AppConfig()
    default = AppConfig()
    try:
        save_config(default, target)
    except ConfigError:
        _warn(warn, f"{reason}；已备份为 {backup.name}，但无法写回默认配置。")
        return default
    _warn(warn, f"{reason}；已备份为 {backup.name} 并重建默认配置。")
    return default


def load_config(
    path: str | Path | None = None, *, warn: Callable[[str], None] | None = None
) -> AppConfig:
    """Load settings, never raising on a missing or damaged file."""
    target = Path(path) if path is not None else resolve_config_path().path
    if not target.is_file():
        return AppConfig()
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        _warn(warn, f"无法读取配置文件 {target.name}：{exc}")
        return AppConfig()
    try:
        raw = json.loads(text)
    except ValueError:
        return _recover_corrupt_file(target, warn, "配置文件不是合法 JSON")
    if not isinstance(raw, dict):
        return _recover_corrupt_file(target, warn, "配置文件顶层不是 JSON 对象")
    config = _from_mapping(raw)
    if config.version > CONFIG_VERSION:
        _warn(warn, "配置文件版本较新，仅读取已知字段。")
    config.version = CONFIG_VERSION
    return config


def save_config(config: AppConfig, path: str | Path | None = None) -> Path:
    """Persist settings atomically and return the path actually written."""
    payload = _serialize(config)
    target = Path(path) if path is not None else resolve_config_path().path
    try:
        _write_atomic(target, payload)
        return target
    except OSError as exc:
        fallback = appdata_config_path()
        if _same_path(target, fallback):
            raise ConfigError(f"配置保存失败：{exc}") from exc
        try:
            _write_atomic(fallback, payload)
        except OSError as second:
            raise ConfigError(f"配置保存失败：{second}") from second
        return fallback


def passwords_to_text(passwords: Iterable[str]) -> str:
    return "\n".join(str(item) for item in passwords)


def text_to_passwords(text: str) -> list[str]:
    """Split the password box into candidates, keeping inner and edge spaces."""
    result: list[str] = []
    for line in text.splitlines():
        value = line.replace("\r", "")
        if not value.strip():
            continue
        if value not in result:
            result.append(value)
    return result
