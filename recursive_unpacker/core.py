from __future__ import annotations

import bz2
import gzip
import lzma
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import threading
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Callable, Iterable

_LOG = Callable[[str], None]
MAX_MEMBER_COUNT = 100_000
MAX_EXPANDED_BYTES = 20 * 1024**3


class ExtractionError(RuntimeError):
    """Raised when an archive cannot be safely extracted."""


class UnsafeArchiveError(ExtractionError, ValueError):
    """Raised when an archive contains paths or entries we refuse to extract."""


class ExtractionCancelled(RuntimeError):
    """Raised when the user cancels an extraction operation."""


@dataclass
class ExtractionReport:
    output_dir: Path
    extracted_archives: int
    remaining_archives: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def parse_extensions(value: str | Iterable[str]) -> list[str]:
    """Normalize a comma/newline-separated extension list."""
    if isinstance(value, str):
        raw_values = re.split(r"[,;\n\r\t ]+", value)
    else:
        raw_values = list(value)
    extensions: list[str] = []
    for raw in raw_values:
        item = str(raw).strip().lower()
        if not item:
            continue
        if not item.startswith("."):
            item = "." + item
        if len(item) < 2 or not re.fullmatch(r"\.[a-z0-9][a-z0-9._-]*", item):
            raise ValueError(f"扩展名格式不正确：{raw}")
        if item not in extensions:
            extensions.append(item)
    if not extensions:
        raise ValueError("请至少填写一个扩展名")
    return extensions


def validate_member_names(names: Iterable[str]) -> None:
    """Reject archive member names that could escape the extraction root."""
    for raw_name in names:
        name = str(raw_name).replace("\\", "/")
        posix = PurePosixPath(name)
        windows = PureWindowsPath(str(raw_name))
        if (
            not name
            or name.startswith("/")
            or name.startswith("//")
            or posix.is_absolute()
            or windows.is_absolute()
            or bool(windows.drive)
            or any(part == ".." for part in posix.parts)
        ):
            raise UnsafeArchiveError(f"压缩包包含不安全路径：{raw_name}")


def detect_archive(path: str | Path) -> str | None:
    """Identify common archive formats from file contents, not the suffix."""
    candidate = Path(path)
    if not candidate.is_file():
        return None
    try:
        if zipfile.is_zipfile(candidate):
            return "zip"
        with candidate.open("rb") as stream:
            signature = stream.read(8)
        if signature.startswith(b"7z\xbc\xaf\x27\x1c"):
            return "7z"
        if signature.startswith(b"Rar!\x1a\x07\x00") or signature.startswith(b"Rar!\x1a\x07\x01\x00"):
            return "rar"
        # Check tar before gzip/bzip2/xz because tarfile can inspect compressed TARs.
        if tarfile.is_tarfile(candidate):
            return "tar"
        if signature.startswith(b"\x1f\x8b"):
            return "gzip"
        if signature.startswith(b"BZh"):
            return "bzip2"
        if signature.startswith(b"\xfd7zXZ\x00"):
            return "xz"
    except (OSError, tarfile.TarError, EOFError):
        return None
    return None


def find_7zip_executable(configured_path: str | Path | None = None) -> str | None:
    """Return an explicit or PATH-discovered 7-Zip executable, if available."""
    if configured_path:
        candidate = str(configured_path).strip().strip('"')
        if candidate and Path(candidate).is_file():
            return candidate
        found = shutil.which(candidate) if candidate else None
        if found:
            return found
    for name in ("7z", "7zz", "7za", "7z.exe", "7zz.exe", "7za.exe"):
        found = shutil.which(name)
        if found:
            return found
    return None


def find_bandizip_executable(configured_path: str | Path | None = None) -> str | None:
    """Find Bandizip's console executable (bz.exe), not the GUI-only entry point."""
    if configured_path:
        raw = str(configured_path).strip().strip('"')
        if raw:
            candidate = Path(raw).expanduser()
            if candidate.is_dir():
                for name in ("bz.exe", "bz"):
                    child = candidate / name
                    if child.is_file():
                        return str(child.resolve())
            if candidate.is_file():
                filename = candidate.name.lower()
                if filename in {"bz.exe", "bz"}:
                    return str(candidate.resolve())
                # The command `l` is documented for bz.exe, so prefer a sibling
                # console executable if the user selected Bandizip.exe by mistake.
                if filename in {"bandizip.exe", "bandizip"}:
                    sibling = candidate.with_name("bz.exe")
                    if sibling.is_file():
                        return str(sibling.resolve())
            found = shutil.which(raw)
            if found and Path(found).name.lower() in {"bz.exe", "bz"}:
                return found

    for name in ("bz.exe", "bz"):
        found = shutil.which(name)
        if found:
            return found

    roots: list[Path] = []
    for env_name in ("ProgramW6432", "PROGRAMFILES", "PROGRAMFILES(X86)"):
        value = os.environ.get(env_name)
        if value:
            roots.append(Path(value))
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        roots.append(Path(local_app_data) / "Programs")

    checked: set[str] = set()
    for root in roots:
        for relative in (Path("Bandizip") / "bz.exe", Path("Bandizip") / "Bin" / "bz.exe"):
            candidate = root / relative
            key = str(candidate).casefold()
            if key in checked:
                continue
            checked.add(key)
            if candidate.is_file():
                return str(candidate.resolve())
    return None


def _log(log: _LOG | None, message: str) -> None:
    if log:
        log(message)


def _check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event and cancel_event.is_set():
        raise ExtractionCancelled("用户已取消操作")


def _password_candidates(passwords: Iterable[str] | str | None) -> list[str | None]:
    if passwords is None:
        raw: list[str] = []
    elif isinstance(passwords, str):
        raw = passwords.splitlines()
    else:
        raw = [str(item) for item in passwords]
    result: list[str | None] = [None, ""]
    for password in raw:
        if "\x00" in password:
            raise ValueError("密码中不能包含 NUL 字符")
        if password not in result:
            result.append(password)
    return result


def _archive_stem(path: Path, extensions: Iterable[str]) -> str:
    lower_name = path.name.lower()
    known_suffixes = sorted(set(extensions) | {".tar.gz", ".tar.bz2", ".tar.xz"}, key=len, reverse=True)
    for suffix in known_suffixes:
        if lower_name.endswith(suffix) and len(path.name) > len(suffix):
            name = path.name[: -len(suffix)]
            break
    else:
        name = path.stem or path.name
    # Keep output folder names valid on Windows and prevent special directory names.
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if name in {"", ".", ".."}:
        name = "extracted"
    return name[:180]


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for number in range(2, 10_000):
        candidate = path.with_name(f"{path.name} ({number})")
        if not candidate.exists():
            return candidate
    raise ExtractionError(f"无法为输出目录分配唯一名称：{path.name}")


def _validate_limits(file_count: int, expanded_bytes: int) -> None:
    if file_count > MAX_MEMBER_COUNT:
        raise UnsafeArchiveError(f"压缩包文件数量超过安全上限（{MAX_MEMBER_COUNT:,}）")
    if expanded_bytes > MAX_EXPANDED_BYTES:
        raise UnsafeArchiveError("压缩包预计解压体积超过安全上限（20 GiB）")


def _validate_zip_infos(infos: list[zipfile.ZipInfo]) -> None:
    validate_member_names(info.filename for info in infos)
    _validate_limits(sum(not item.is_dir() for item in infos), sum(item.file_size for item in infos))
    for info in infos:
        mode = (info.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(mode):
            raise UnsafeArchiveError(f"压缩包包含符号链接，已拒绝解压：{info.filename}")


def _extract_zip(source: Path, stage: Path, password: str | None) -> None:
    try:
        import pyzipper  # type: ignore[import-not-found]

        archive_class = pyzipper.AESZipFile
    except ImportError:
        archive_class = zipfile.ZipFile
    with archive_class(source, "r") as archive:
        infos = archive.infolist()
        _validate_zip_infos(infos)
        pwd = password.encode("utf-8") if password is not None else None
        archive.extractall(path=stage, pwd=pwd)


def _extract_7z_python(source: Path, stage: Path, password: str | None) -> None:
    try:
        import py7zr  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("未安装 py7zr") from exc
    with py7zr.SevenZipFile(source, mode="r", password=password) as archive:
        names = archive.getnames()
        validate_member_names(names)
        if len(names) > MAX_MEMBER_COUNT:
            raise UnsafeArchiveError(f"压缩包文件数量超过安全上限（{MAX_MEMBER_COUNT:,}）")
        archive.extractall(path=stage)


def _extract_tar(source: Path, stage: Path) -> None:
    with tarfile.open(source, mode="r:*") as archive:
        members = archive.getmembers()
        validate_member_names(member.name for member in members)
        _validate_limits(sum(member.isfile() for member in members), sum(member.size for member in members if member.isfile()))
        for member in members:
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise UnsafeArchiveError(f"TAR 中包含链接或特殊文件，已拒绝解压：{member.name}")
            target = stage.joinpath(*PurePosixPath(member.name.replace("\\", "/")).parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                source_stream = archive.extractfile(member)
                if source_stream is None:
                    raise ExtractionError(f"无法读取压缩包成员：{member.name}")
                with source_stream, target.open("wb") as target_stream:
                    shutil.copyfileobj(source_stream, target_stream)


def _extract_single_file_compression(source: Path, stage: Path, kind: str) -> None:
    if kind == "gzip":
        opener = gzip.open
        suffix = ".gz"
    elif kind == "bzip2":
        opener = bz2.open
        suffix = ".bz2"
    else:
        opener = lzma.open
        suffix = ".xz"
    name = source.name[:-len(suffix)] if source.name.lower().endswith(suffix) else source.name + ".decompressed"
    name = Path(name).name
    if not name:
        name = "decompressed.bin"
    with opener(source, "rb") as src, (stage / name).open("wb") as dst:
        shutil.copyfileobj(src, dst)


def _validate_stage_tree(stage: Path) -> None:
    count = 0
    total = 0
    for path in stage.rglob("*"):
        if path.is_symlink():
            raise UnsafeArchiveError(f"解压结果包含符号链接，已拒绝：{path.name}")
        if path.is_file():
            count += 1
            try:
                total += path.stat().st_size
            except OSError:
                continue
    _validate_limits(count, total)


def _seven_zip_list_paths(executable: str, archive_path: Path, password: str | None) -> None:
    command = [executable, "l", "-slt"]
    if password:
        command.append(f"-p{password}")
    command.append(str(archive_path))
    completed = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=60,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    output = completed.stdout.decode("utf-8", errors="replace")
    if completed.returncode != 0:
        raise RuntimeError("7-Zip 无法读取压缩包目录")
    if "----------" not in output:
        raise RuntimeError("无法验证压缩包内部路径")
    listing = output.split("----------", 1)[1]
    names = [line[len("Path = "):].strip() for line in listing.splitlines() if line.startswith("Path = ")]
    if not names and "No files to list" not in output:
        raise RuntimeError("无法读取或验证压缩包内部路径")
    if names:
        validate_member_names(names)


def _extract_with_7zip(
    source: Path,
    stage: Path,
    password: str | None,
    executable: str,
    extension: str,
) -> None:
    candidate = stage.parent / ("candidate" + extension)
    shutil.copy2(source, candidate)
    _seven_zip_list_paths(executable, candidate, password)
    command = [executable, "x", "-y", f"-o{stage}"]
    if password:
        command.append(f"-p{password}")
    command.append(str(candidate))
    completed = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=300,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        raise RuntimeError(f"7-Zip 解压失败（退出码 {completed.returncode}）")


def _bandizip_list_paths(executable: str, archive_path: Path, password: str | None) -> None:
    """List archive members with bz.exe and reject unsafe paths before extraction."""
    command = [executable, "l", "-list:s", "-consolemode:utf8"]
    if password:
        command.append(f"-p:{password}")
    command.append(str(archive_path))
    completed = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=60,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    output = completed.stdout.decode("utf-8", errors="replace")
    if completed.returncode != 0:
        raise RuntimeError("Bandizip 无法读取压缩包目录（可能需要正确密码或文件已损坏）")

    names: list[str] = []
    for line in output.splitlines():
        name = line.strip().lstrip("\ufeff")
        if not name:
            continue
        lowered = name.lower()
        if lowered.startswith(("listing archive:", "archive:", "no files to list")):
            continue
        if name and set(name) == {"-"}:
            continue
        names.append(name)
    if names:
        validate_member_names(names)


def _extract_with_bandizip(
    source: Path,
    stage: Path,
    password: str | None,
    executable: str,
    extension: str,
) -> None:
    candidate = stage.parent / ("candidate" + extension)
    shutil.copy2(source, candidate)
    _bandizip_list_paths(executable, candidate, password)
    command = [executable, "x", "-y", f"-o:{stage}", "-consolemode:utf8"]
    if password:
        command.append(f"-p:{password}")
    command.append(str(candidate))
    completed = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=300,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        output = completed.stdout.decode("utf-8", errors="replace").strip()
        detail = f"：{output[-300:]}" if output else ""
        raise RuntimeError(f"Bandizip 解压失败（退出码 {completed.returncode}）{detail}")


def _attempt_python_backend(source: Path, stage: Path, kind: str, password: str | None) -> None:
    if kind == "zip":
        _extract_zip(source, stage, password)
    elif kind == "7z":
        _extract_7z_python(source, stage, password)
    elif kind == "tar":
        _extract_tar(source, stage)
    elif kind in {"gzip", "bzip2", "xz"}:
        _extract_single_file_compression(source, stage, kind)
    else:
        raise RuntimeError("没有适用于该格式的 Python 解压后端")


def _extract_one(
    source: Path,
    destination: Path,
    *,
    extensions: list[str],
    passwords: list[str | None],
    bandizip_path: str | None,
    seven_zip_path: str | None,
    log: _LOG | None,
    cancel_event: threading.Event | None,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    kind = detect_archive(source)
    bandizip = find_bandizip_executable(bandizip_path)
    seven_zip = find_7zip_executable(seven_zip_path)
    errors: list[str] = []

    # Prefer Bandizip for formats it supports. bz.exe provides the `l` command,
    # allowing member paths to be checked before extraction.
    if bandizip and kind != "bzip2":
        external_passwords = [None] + [password for password in passwords if password not in (None, "")]
        for extension in extensions:
            for password in external_passwords:
                _check_cancel(cancel_event)
                label = "无密码/空密码" if password is None else "用户提供的密码候选"
                _log(log, f"使用 Bandizip 尝试 {source.name} → {extension}（{label}）")
                with tempfile.TemporaryDirectory(prefix=".unpack-attempt-", dir=destination.parent) as temp_dir:
                    stage = Path(temp_dir) / "stage"
                    stage.mkdir()
                    try:
                        _extract_with_bandizip(source, stage, password, bandizip, extension)
                        _validate_stage_tree(stage)
                        if destination.exists():
                            raise ExtractionError(f"输出路径已存在：{destination}")
                        os.replace(stage, destination)
                        _log(log, f"Bandizip 解压成功：{source.name}")
                        return
                    except UnsafeArchiveError:
                        raise
                    except Exception as exc:
                        errors.append(type(exc).__name__)
                        _log(log, f"Bandizip 本轮未成功（{type(exc).__name__}）")

    # Keep native Python support as a fallback when Bandizip is unavailable or
    # does not support this format on the current installation.
    if kind in {"zip", "7z", "tar", "gzip", "bzip2", "xz"}:
        attempts = passwords if kind in {"zip", "7z"} else [None]
        for password in attempts:
            _check_cancel(cancel_event)
            label = "无密码/空密码" if password in (None, "") else "用户提供的密码候选"
            _log(log, f"尝试 {source.name}（{label}）")
            with tempfile.TemporaryDirectory(prefix=".unpack-attempt-", dir=destination.parent) as temp_dir:
                stage = Path(temp_dir) / "stage"
                stage.mkdir()
                try:
                    _attempt_python_backend(source, stage, kind, password)
                    _validate_stage_tree(stage)
                    if destination.exists():
                        raise ExtractionError(f"输出路径已存在：{destination}")
                    os.replace(stage, destination)
                    _log(log, f"解压成功：{source.name}")
                    return
                except UnsafeArchiveError:
                    raise
                except Exception as exc:
                    errors.append(type(exc).__name__)
                    _log(log, f"本轮未成功（{type(exc).__name__}）")

    # 7-Zip remains an optional fallback for formats not handled above or when
    # Bandizip is unavailable. Candidate suffixes are applied to temporary copies.
    if seven_zip:
        external_passwords = [None] + [password for password in passwords if password not in (None, "")]
        for extension in extensions:
            for password in external_passwords:
                _check_cancel(cancel_event)
                label = "无密码" if password is None else "用户提供的密码候选"
                _log(log, f"使用 7-Zip 尝试 {source.name} → {extension}（{label}）")
                with tempfile.TemporaryDirectory(prefix=".unpack-attempt-", dir=destination.parent) as temp_dir:
                    stage = Path(temp_dir) / "stage"
                    stage.mkdir()
                    try:
                        _extract_with_7zip(source, stage, password, seven_zip, extension)
                        _validate_stage_tree(stage)
                        if destination.exists():
                            raise ExtractionError(f"输出路径已存在：{destination}")
                        os.replace(stage, destination)
                        _log(log, f"7-Zip 解压成功：{source.name}")
                        return
                    except UnsafeArchiveError:
                        raise
                    except Exception as exc:
                        errors.append(type(exc).__name__)

    if kind == "rar" and not bandizip and not seven_zip:
        raise ExtractionError("检测到 RAR 文件，但未找到 Bandizip 的 bz.exe 或 7-Zip 的 7z.exe；请安装 Bandizip 并在界面中指定 bz.exe。")
    if kind is None and not bandizip and not seven_zip:
        raise ExtractionError("无法识别文件格式，且未找到 Bandizip bz.exe 或 7-Zip。请检查后缀候选或配置解压程序路径。")
    if not errors:
        errors.append("NoBackend")
    raise ExtractionError(f"无法解压 {source.name}；已尝试可用后端和候选密码。最后错误类型：{errors[-1]}")

def _is_archive_candidate(path: Path, extensions: list[str]) -> bool:
    return detect_archive(path) is not None or any(path.name.lower().endswith(ext) for ext in extensions)


def extract_recursive(
    source: str | Path,
    output_base: str | Path,
    *,
    extensions: str | Iterable[str] = (".zip", ".7z", ".rar"),
    passwords: Iterable[str] | str | None = None,
    max_depth: int = 5,
    bandizip_path: str | None = None,
    seven_zip_path: str | None = None,
    delete_nested: bool = True,
    log: _LOG | None = None,
    cancel_event: threading.Event | None = None,
) -> ExtractionReport:
    """Extract an archive and recursively unpack nested archives into a real folder.

    The original source is never modified. `max_depth=1` extracts only the first
    archive; nested archives are left in place and reported.
    """
    source_path = Path(source).expanduser().resolve()
    base_path = Path(output_base).expanduser().resolve()
    if not source_path.is_file():
        raise ExtractionError(f"找不到输入文件：{source_path}")
    if not isinstance(max_depth, int) or not 1 <= max_depth <= 20:
        raise ValueError("最大递归层数必须是 1 到 20 之间的整数")
    extension_list = parse_extensions(extensions)
    password_list = _password_candidates(passwords)
    base_path.mkdir(parents=True, exist_ok=True)
    output_dir = _unique_path(base_path / _archive_stem(source_path, extension_list))
    warnings: list[str] = []
    extracted_count = 0

    _check_cancel(cancel_event)
    _log(log, f"输入文件：{source_path.name}")
    detected = detect_archive(source_path)
    _log(log, f"文件头识别格式：{detected or '未知，将尝试候选后缀'}")
    _extract_one(
        source_path,
        output_dir,
        extensions=extension_list,
        passwords=password_list,
        bandizip_path=bandizip_path,
        seven_zip_path=seven_zip_path,
        log=log,
        cancel_event=cancel_event,
    )
    extracted_count += 1

    def walk_nested(folder: Path, parent_depth: int) -> None:
        nonlocal extracted_count
        _check_cancel(cancel_event)
        nested_archives = sorted(
            (path for path in folder.rglob("*") if path.is_file() and _is_archive_candidate(path, extension_list)),
            key=lambda item: str(item).lower(),
        )
        for archive_path in nested_archives:
            _check_cancel(cancel_event)
            if not archive_path.exists():
                continue
            if parent_depth >= max_depth:
                message = f"达到递归层数上限 {max_depth}，保留未处理压缩包：{archive_path.relative_to(output_dir)}"
                warnings.append(message)
                _log(log, message)
                continue
            nested_output = _unique_path(archive_path.parent / _archive_stem(archive_path, extension_list))
            try:
                _extract_one(
                    archive_path,
                    nested_output,
                    extensions=extension_list,
                    passwords=password_list,
                    bandizip_path=bandizip_path,
                    seven_zip_path=seven_zip_path,
                    log=log,
                    cancel_event=cancel_event,
                )
            except ExtractionCancelled:
                raise
            except Exception as exc:
                message = f"未能继续解压 {archive_path.relative_to(output_dir)}：{exc}"
                warnings.append(message)
                _log(log, message)
                continue
            extracted_count += 1
            if delete_nested:
                try:
                    archive_path.unlink()
                    _log(log, f"已移除成功解压的内层压缩包：{archive_path.relative_to(output_dir)}")
                except OSError as exc:
                    message = f"内容已解压，但无法删除内层压缩包 {archive_path.name}：{exc}"
                    warnings.append(message)
                    _log(log, message)
            walk_nested(nested_output, parent_depth + 1)

    walk_nested(output_dir, 1)
    remaining = sorted(
        (path for path in output_dir.rglob("*") if path.is_file() and _is_archive_candidate(path, extension_list)),
        key=lambda item: str(item).lower(),
    )
    if remaining:
        _log(log, f"处理结束，但仍有 {len(remaining)} 个压缩包或疑似压缩包未展开。")
    else:
        _log(log, "处理完成：输出目录中没有检测到剩余压缩包。")
    return ExtractionReport(
        output_dir=output_dir,
        extracted_archives=extracted_count,
        remaining_archives=remaining,
        warnings=warnings,
    )
