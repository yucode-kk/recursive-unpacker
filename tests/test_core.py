from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from recursive_unpacker.core import detect_archive, parse_extensions, validate_member_names


def test_detects_zip_even_when_extension_is_wrong(tmp_path: Path) -> None:
    path = tmp_path / "download.data"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("hello.txt", "hello")

    assert detect_archive(path) == "zip"


def test_parse_extensions_normalizes_and_deduplicates() -> None:
    assert parse_extensions(".ZIP, rar, .7z, .zip") == [".zip", ".rar", ".7z"]


def test_parse_extensions_rejects_path_separators() -> None:
    with pytest.raises(ValueError, match="扩展名"):
        parse_extensions("../zip")


@pytest.mark.parametrize(
    "member",
    ["../escape.txt", "/absolute.txt", r"C:\\outside.txt", r"..\\escape.txt"],
)
def test_validate_member_names_rejects_paths_outside_destination(member: str) -> None:
    with pytest.raises(ValueError, match="不安全"):
        validate_member_names([member])


def test_validate_member_names_allows_normal_relative_paths() -> None:
    validate_member_names(["folder/file.txt", "another.txt", "folder/"])


def _zip_bytes(entries: dict[str, bytes | str]) -> bytes:
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    return buffer.getvalue()


def test_extracts_nested_zip_and_removes_inner_archive(tmp_path: Path) -> None:
    from recursive_unpacker.core import extract_recursive

    inner_bytes = _zip_bytes({"deep.txt": "inside"})
    source = tmp_path / "bundle.bin"
    source.write_bytes(_zip_bytes({"hello.txt": "hello", "nested.zip": inner_bytes}))
    output_base = tmp_path / "out"

    report = extract_recursive(
        source,
        output_base,
        extensions=[".zip", ".7z", ".rar"],
        passwords=[],
        max_depth=4,
    )

    assert (report.output_dir / "hello.txt").read_text() == "hello"
    assert (report.output_dir / "nested" / "deep.txt").read_text() == "inside"
    assert not (report.output_dir / "nested.zip").exists()
    assert source.exists(), "the original input must remain unchanged"
    assert report.extracted_archives == 2
    assert report.remaining_archives == []


def test_depth_limit_reports_nested_archives_without_deleting_them(tmp_path: Path) -> None:
    from recursive_unpacker.core import extract_recursive

    source = tmp_path / "bundle.zip"
    source.write_bytes(_zip_bytes({"nested.zip": _zip_bytes({"deep.txt": "inside"})}))

    report = extract_recursive(
        source,
        tmp_path / "out",
        extensions=[".zip", ".7z", ".rar"],
        passwords=[],
        max_depth=1,
    )

    assert report.extracted_archives == 1
    assert len(report.remaining_archives) == 1
    assert report.remaining_archives[0].name == "nested.zip"
    assert report.remaining_archives[0].exists()


def test_rejects_zip_traversal_before_writing_outside_output(tmp_path: Path) -> None:
    from recursive_unpacker.core import ExtractionError, extract_recursive

    source = tmp_path / "evil.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("../escaped.txt", "do not write")

    outside = tmp_path / "escaped.txt"
    with pytest.raises(ExtractionError, match="不安全"):
        extract_recursive(
            source,
            tmp_path / "out",
            extensions=[".zip", ".7z", ".rar"],
            passwords=[],
            max_depth=2,
        )

    assert not outside.exists()


def test_encrypted_zip_tries_candidate_passwords(tmp_path: Path) -> None:
    try:
        import pyzipper
    except ImportError:
        pyzipper = None
    from recursive_unpacker.core import ExtractionError, extract_recursive

    source = tmp_path / "locked.zip"
    if pyzipper is not None:
        with pyzipper.AESZipFile(
            source,
            "w",
            compression=pyzipper.ZIP_DEFLATED,
            encryption=pyzipper.WZ_AES,
        ) as archive:
            archive.setpassword(b"correct-password")
            archive.writestr("secret.txt", "unlocked")
    else:
        zip_executable = shutil.which("zip")
        if not zip_executable:
            pytest.skip("需要 pyzipper 或系统 zip 命令来创建加密 ZIP 测试样本")
        secret = tmp_path / "secret.txt"
        secret.write_text("unlocked", encoding="utf-8")
        subprocess.run(
            [zip_executable, "-q", "-j", "-P", "correct-password", str(source), str(secret)],
            check=True,
        )
        secret.unlink()

    with pytest.raises(ExtractionError):
        extract_recursive(
            source,
            tmp_path / "out",
            extensions=[".zip", ".7z", ".rar"],
            passwords=["wrong-password"],
            max_depth=2,
        )
    assert not (tmp_path / "out" / "locked").exists(), "failed attempts must not leave partial output"

    report = extract_recursive(
        source,
        tmp_path / "out",
        extensions=[".zip", ".7z", ".rar"],
        passwords=["wrong-password", "correct-password"],
        max_depth=2,
    )

    assert (report.output_dir / "secret.txt").read_text() == "unlocked"


def test_gui_module_imports_without_creating_a_window() -> None:
    import importlib

    module = importlib.import_module("recursive_unpacker.gui")
    assert callable(module.launch_app)


def test_find_bandizip_executable_accepts_configured_bz_path(tmp_path: Path) -> None:
    from recursive_unpacker.core import find_bandizip_executable

    executable = tmp_path / "bz.exe"
    executable.write_bytes(b"fake executable")

    assert find_bandizip_executable(executable) == str(executable)


def test_extracts_rar_with_bandizip_without_7zip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    import recursive_unpacker.core as core

    source = tmp_path / "world.rar"
    source.write_bytes(b"Rar!\x1a\x07\x00" + b"\x00" * 20)
    bandizip = tmp_path / "bz.exe"
    bandizip.write_bytes(b"fake executable")
    calls: list[list[str]] = []
    inner_bytes = _zip_bytes({"deep.txt": "nested"})

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(command)
        candidate = Path(command[-1])
        is_nested_zip = zipfile.is_zipfile(candidate)
        if command[1] == "l":
            if "-p:secret" not in command:
                return SimpleNamespace(returncode=1, stdout=b"wrong password\n")
            names = b"deep.txt\n" if is_nested_zip else b"hello.txt\nnested.zip\n"
            return SimpleNamespace(returncode=0, stdout=names)
        if command[1] == "x":
            assert "-p:secret" in command
            output_arg = next(item for item in command if item.startswith("-o:"))
            output_dir = Path(output_arg[3:])
            if is_nested_zip:
                (output_dir / "deep.txt").write_text("nested", encoding="utf-8")
            else:
                (output_dir / "hello.txt").write_text("unpacked", encoding="utf-8")
                (output_dir / "nested.zip").write_bytes(inner_bytes)
            return SimpleNamespace(returncode=0, stdout=b"")
        raise AssertionError(f"unexpected Bandizip command: {command}")

    monkeypatch.setattr(core.subprocess, "run", fake_run)
    monkeypatch.setattr(core, "find_7zip_executable", lambda configured_path=None: None)

    report = core.extract_recursive(
        source,
        tmp_path / "out",
        passwords=["wrong", "secret"],
        bandizip_path=str(bandizip),
    )

    assert (report.output_dir / "hello.txt").read_text(encoding="utf-8") == "unpacked"
    assert (report.output_dir / "nested" / "deep.txt").read_text(encoding="utf-8") == "nested"
    assert not (report.output_dir / "nested.zip").exists()
    assert any(command[1] == "x" and "-p:secret" in command for command in calls)
    assert any(command[1] == "l" for command in calls)
    assert report.extracted_archives == 2
    assert not report.remaining_archives


def test_bandizip_rejects_path_traversal_before_extracting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    import recursive_unpacker.core as core

    source = tmp_path / "unsafe.rar"
    source.write_bytes(b"Rar!\x1a\x07\x00" + b"\x00" * 20)
    bandizip = tmp_path / "bz.exe"
    bandizip.write_bytes(b"fake executable")
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout=b"../escaped.txt\n")

    monkeypatch.setattr(core.subprocess, "run", fake_run)
    monkeypatch.setattr(core, "find_7zip_executable", lambda configured_path=None: None)

    with pytest.raises(core.ExtractionError, match="不安全"):
        core.extract_recursive(
            source,
            tmp_path / "out",
            bandizip_path=str(bandizip),
        )

    assert not any(command[1] == "x" for command in commands)
    assert not (tmp_path / "escaped.txt").exists()


def test_find_bandizip_executable_discovers_program_files_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import recursive_unpacker.core as core

    executable = tmp_path / "Bandizip" / "bz.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"fake executable")
    monkeypatch.setenv("ProgramW6432", str(tmp_path))
    monkeypatch.delenv("PROGRAMFILES", raising=False)
    monkeypatch.delenv("PROGRAMFILES(X86)", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(core.shutil, "which", lambda _name: None)

    assert core.find_bandizip_executable() == str(executable.resolve())


def test_bandizip_can_try_configured_suffix_when_signature_is_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    import recursive_unpacker.core as core

    source = tmp_path / "unknown.download"
    source.write_bytes(b"not a recognized signature")
    bandizip = tmp_path / "bz.exe"
    bandizip.write_bytes(b"fake executable")
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        commands.append(command)
        if command[1] == "l":
            return SimpleNamespace(returncode=0, stdout=b"payload.txt\n")
        if command[1] == "x":
            output_arg = next(item for item in command if item.startswith("-o:"))
            Path(output_arg[3:], "payload.txt").write_text("payload", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout=b"")
        raise AssertionError(f"unexpected Bandizip command: {command}")

    monkeypatch.setattr(core.subprocess, "run", fake_run)
    monkeypatch.setattr(core, "find_7zip_executable", lambda configured_path=None: None)

    report = core.extract_recursive(
        source,
        tmp_path / "out",
        extensions=[".lzh"],
        bandizip_path=str(bandizip),
    )

    assert (report.output_dir / "payload.txt").read_text(encoding="utf-8") == "payload"
    assert any(str(item).endswith("candidate.lzh") for command in commands for item in command)
