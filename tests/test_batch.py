from __future__ import annotations

import threading
from pathlib import Path

import pytest

import recursive_unpacker.batch as batch
import recursive_unpacker.core as core


@pytest.fixture(autouse=True)
def _hide_external_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep this module independent of any Bandizip / 7-Zip installed on the machine."""
    monkeypatch.setattr(core, "find_bandizip_executable", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(core, "find_7zip_executable", lambda *_args, **_kwargs: None)


def _make_zip(tmp_path: Path, name: str, payload: bytes) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


# --------------------------------------------------------------------------- discovery


def test_discover_recursive_finds_nested_archives(tmp_path: Path) -> None:
    (tmp_path / "sub" / "deep").mkdir(parents=True)
    (tmp_path / "a.zip").write_bytes(b"")
    (tmp_path / "sub" / "b.7z").write_bytes(b"")
    (tmp_path / "sub" / "deep" / "c.rar").write_bytes(b"")
    (tmp_path / "readme.txt").write_text("not an archive", encoding="utf-8")
    (tmp_path / "note.bin").write_bytes(b"")

    found = batch.discover_archives([tmp_path], [".zip", ".7z", ".rar"], recursive=True)

    assert sorted(path.name for path in found) == ["a.zip", "b.7z", "c.rar"]


def test_discover_non_recursive_stays_on_top_level(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.zip").write_bytes(b"")
    (tmp_path / "sub" / "b.zip").write_bytes(b"")

    found = batch.discover_archives([tmp_path], [".zip"], recursive=False)

    assert [path.name for path in found] == ["a.zip"]


def test_discover_mixes_explicit_files_and_folders_without_duplicates(tmp_path: Path) -> None:
    (tmp_path / "a.zip").write_bytes(b"")
    explicit = tmp_path / "b.zip"
    explicit.write_bytes(b"")

    found = batch.discover_archives([explicit, tmp_path], [".zip"])

    assert [path.name for path in found] == ["b.zip", "a.zip"]


def test_discover_skips_excluded_directories(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    (tmp_path / "a.zip").write_bytes(b"")
    (output / "old.zip").write_bytes(b"")

    found = batch.discover_archives([tmp_path], [".zip"], exclude_dirs=[output])

    assert [path.name for path in found] == ["a.zip"]


def test_discover_keeps_explicit_file_with_unexpected_suffix(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.bin"
    bundle.write_bytes(b"")

    found = batch.discover_archives([bundle], [".zip"])

    assert found == [bundle.resolve()]


def test_discover_skips_missing_roots_and_logs(tmp_path: Path) -> None:
    lines: list[str] = []

    found = batch.discover_archives([tmp_path / "nope.zip"], [".zip"], log=lines.append)

    assert found == []
    assert any("跳过不存在的路径" in line for line in lines)


def test_discover_reports_empty_result(tmp_path: Path) -> None:
    lines: list[str] = []

    assert batch.discover_archives([tmp_path], [".zip"], log=lines.append) == []
    assert any("未找到" in line for line in lines)


def test_discover_respects_cancel_event(tmp_path: Path) -> None:
    event = threading.Event()
    event.set()

    with pytest.raises(core.ExtractionCancelled):
        batch.discover_archives([tmp_path], [".zip"], cancel_event=event)


# --------------------------------------------------------------------------- batch run


def test_extract_batch_continues_after_single_failure(tmp_path: Path, zip_bytes) -> None:
    payload = zip_bytes({"hello.txt": "hi"})
    sources = [
        _make_zip(tmp_path, "ok1.zip", payload),
        _make_zip(tmp_path, "broken.zip", b"not an archive at all"),
        _make_zip(tmp_path, "ok2.zip", payload),
    ]

    report = batch.extract_batch(sources, tmp_path / "out")

    assert report.total == 3
    assert [item.source.name for item in report.succeeded] == ["ok1.zip", "ok2.zip"]
    assert [item.source.name for item in report.failed] == ["broken.zip"]
    assert report.failed[0].error
    assert all(item.output_dir is not None and item.output_dir.is_dir() for item in report.succeeded)
    assert report.extracted_archives == 2


def test_extract_batch_creates_one_subfolder_per_archive(tmp_path: Path, zip_bytes) -> None:
    payload = zip_bytes({"hello.txt": "hi"})
    first = _make_zip(tmp_path, "a/pack.zip", payload)
    second = _make_zip(tmp_path, "b/pack.zip", payload)

    report = batch.extract_batch([first, second], tmp_path / "out")

    assert (tmp_path / "out" / "pack").is_dir()
    assert (tmp_path / "out" / "pack (2)").is_dir()
    assert len(report.succeeded) == 2


def test_extract_batch_logs_progress_prefix(tmp_path: Path, zip_bytes) -> None:
    payload = zip_bytes({"f.txt": "x"})
    sources = [
        _make_zip(tmp_path, "one.zip", payload),
        _make_zip(tmp_path, "two.zip", payload),
    ]
    lines: list[str] = []

    batch.extract_batch(sources, tmp_path / "out", log=lines.append)

    assert any(line.startswith("[1/2]") for line in lines)
    assert any(line.startswith("[2/2]") for line in lines)


def test_extract_batch_invokes_progress_callback(tmp_path: Path, zip_bytes) -> None:
    payload = zip_bytes({"f.txt": "x"})
    sources = [
        _make_zip(tmp_path, "one.zip", payload),
        _make_zip(tmp_path, "two.zip", payload),
    ]
    seen: list[tuple[int, int, str]] = []

    batch.extract_batch(
        sources,
        tmp_path / "out",
        on_progress=lambda index, total, source: seen.append((index, total, source.name)),
    )

    assert seen == [(1, 2, "one.zip"), (2, 2, "two.zip")]


def test_extract_batch_cancel_raises_with_partial_report(tmp_path: Path, zip_bytes) -> None:
    payload = zip_bytes({"f.txt": "x"})
    sources = [
        _make_zip(tmp_path, "one.zip", payload),
        _make_zip(tmp_path, "two.zip", payload),
    ]
    event = threading.Event()

    def progress(index: int, total: int, source: Path) -> None:
        event.set()

    with pytest.raises(core.ExtractionCancelled) as info:
        batch.extract_batch(sources, tmp_path / "out", cancel_event=event, on_progress=progress)

    partial = getattr(info.value, "report")
    assert partial.total == 1
    assert not (tmp_path / "out" / "two").exists()


def test_extract_batch_missing_file_recorded_as_error(tmp_path: Path) -> None:
    report = batch.extract_batch([tmp_path / "missing.zip"], tmp_path / "out")

    assert report.total == 1
    assert report.failed[0].error is not None
    assert "不存在" in report.failed[0].error


def test_extract_batch_empty_sources_returns_empty_report(tmp_path: Path) -> None:
    report = batch.extract_batch([], tmp_path / "out")

    assert report.total == 0
    assert report.succeeded == []
    assert report.failed == []


def test_extract_batch_rejects_out_of_range_depth(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="递归层数"):
        batch.extract_batch([tmp_path / "a.zip"], tmp_path / "out", max_depth=0)


def test_extract_batch_passes_options_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_extract(source: Path, output_base: Path, **kwargs: object) -> core.ExtractionReport:
        captured.update(kwargs)
        captured["source"] = source
        captured["output_base"] = output_base
        return core.ExtractionReport(output_dir=tmp_path / "out" / "x", extracted_archives=1)

    monkeypatch.setattr(batch, "extract_recursive", fake_extract)
    source = _make_zip(tmp_path, "a.zip", b"")

    batch.extract_batch(
        [source],
        tmp_path / "out",
        extensions=[".zip"],
        max_depth=3,
        delete_nested=False,
        bandizip_path="D:/tools/bz.exe",
        seven_zip_path="D:/tools/7z.exe",
    )

    assert captured["max_depth"] == 3
    assert captured["delete_nested"] is False
    assert captured["extensions"] == [".zip"]
    assert captured["bandizip_path"] == "D:/tools/bz.exe"
    assert captured["seven_zip_path"] == "D:/tools/7z.exe"
    assert captured["source"] == source


def test_extract_batch_promotes_password_without_logging_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    received: list[list[str]] = []

    def fake_extract(source: Path, output_base: Path, **kwargs: object) -> core.ExtractionReport:
        received.append(list(kwargs["passwords"]))  # type: ignore[arg-type]
        used = "right" if len(received) == 1 else None
        return core.ExtractionReport(
            output_dir=tmp_path / "out" / source.stem, extracted_archives=1, used_password=used
        )

    monkeypatch.setattr(batch, "extract_recursive", fake_extract)
    sources = [
        _make_zip(tmp_path, "a.zip", b""),
        _make_zip(tmp_path, "b.zip", b""),
    ]
    lines: list[str] = []

    batch.extract_batch(
        sources, tmp_path / "out", passwords=["wrong", "right"], log=lines.append
    )

    assert received[0] == ["wrong", "right"]
    assert received[1] == ["right", "wrong"]
    assert all("right" not in line for line in lines)


def test_discover_finds_the_first_piece_of_a_split_set(tmp_path: Path) -> None:
    for index in (1, 2, 3):
        (tmp_path / f"M.7z.{index:03d}").write_bytes(b"x")

    found = batch.discover_archives([tmp_path], [".zip", ".7z", ".rar"])

    assert [path.name for path in found] == ["M.7z.001"]


def test_batch_report_aggregates_warnings(tmp_path: Path) -> None:
    report = batch.BatchReport(
        output_base=tmp_path,
        items=[
            batch.BatchItemReport(source=Path("a.zip"), warnings=["层数上限"]),
            batch.BatchItemReport(source=Path("b.zip"), error="boom"),
        ],
    )

    assert report.total == 2
    assert len(report.succeeded) == 1
    assert len(report.failed) == 1
    assert report.warnings == ["a.zip: 层数上限"]
