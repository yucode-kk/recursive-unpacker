"""Batch orchestration: discover many archives and unpack each one independently."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from .core import ExtractionCancelled, check_cancel, extract_recursive, parse_extensions
from .volumes import is_volume_start

_LOG = Callable[[str], None]
_PROGRESS = Callable[[int, int, Path], None]
_CANCEL_CHECK_INTERVAL = 256


@dataclass
class BatchItemReport:
    """Outcome of a single archive. Deliberately carries no password field."""

    source: Path
    output_dir: Path | None = None
    extracted_archives: int = 0
    remaining_archives: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class BatchReport:
    output_base: Path
    items: list[BatchItemReport] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def succeeded(self) -> list[BatchItemReport]:
        return [item for item in self.items if item.ok]

    @property
    def failed(self) -> list[BatchItemReport]:
        return [item for item in self.items if not item.ok]

    @property
    def extracted_archives(self) -> int:
        return sum(item.extracted_archives for item in self.items)

    @property
    def remaining_archives(self) -> list[Path]:
        result: list[Path] = []
        for item in self.items:
            for path in item.remaining_archives:
                if path not in result:
                    result.append(path)
        return result

    @property
    def warnings(self) -> list[str]:
        return [f"{item.source.name}: {warning}" for item in self.items for warning in item.warnings]


def _log(log: _LOG | None, message: str) -> None:
    if log:
        log(message)


def _notify(callback: _PROGRESS | None, index: int, total: int, source: Path) -> None:
    if callback:
        callback(index, total, source)


def _cancel(scope: BatchReport, cancel_event: threading.Event | None) -> None:
    """Cancellation check that attaches the partial report before propagating."""
    try:
        check_cancel(cancel_event)
    except ExtractionCancelled as exc:
        exc.report = scope  # type: ignore[attr-defined]
        raise


def _matches(path: Path, extensions: list[str]) -> bool:
    lowered = path.name.lower()
    return any(lowered.endswith(extension) for extension in extensions)


def _resolve_all(paths: Iterable[str | Path]) -> list[Path]:
    resolved: list[Path] = []
    for item in paths:
        try:
            resolved.append(Path(item).expanduser().resolve())
        except OSError:
            continue
    return resolved


def _is_excluded(path: Path, excluded: list[Path]) -> bool:
    if not excluded:
        return False
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    for item in excluded:
        if resolved == item:
            return True
        try:
            if resolved.is_relative_to(item):
                return True
        except ValueError:
            continue
    return False


def _iter_candidates(
    root: Path,
    extensions: list[str],
    recursive: bool,
    excluded: list[Path],
    cancel_event: threading.Event | None,
):
    if not recursive:
        try:
            entries = sorted(root.iterdir(), key=lambda entry: str(entry).casefold())
        except OSError:
            return
        for candidate in entries:
            if not candidate.is_file():
                continue
            if _matches(candidate, extensions) or is_volume_start(candidate):
                yield candidate.resolve()
        return

    visited = 0
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        visited += 1
        if visited % _CANCEL_CHECK_INTERVAL == 0:
            check_cancel(cancel_event)
        current = Path(directory)
        # Prune excluded subtrees instead of descending into them.
        dirnames[:] = [
            name for name in dirnames if not _is_excluded(current / name, excluded)
        ]
        for name in sorted(filenames, key=str.casefold):
            candidate = current / name
            if not candidate.is_file():
                continue
            # A split set's first piece (`M.zip.001`) carries no recognisable suffix and
            # no complete signature, so it is recognised by its siblings instead.
            if _matches(candidate, extensions) or is_volume_start(candidate):
                yield candidate.resolve()


def discover_archives(
    roots: Iterable[str | Path],
    extensions: str | Iterable[str] = (".zip", ".7z", ".rar"),
    recursive: bool = True,
    *,
    exclude_dirs: Iterable[str | Path] = (),
    log: _LOG | None = None,
    cancel_event: threading.Event | None = None,
) -> list[Path]:
    """Expand a mix of files and folders into a de-duplicated list of archives.

    Explicitly listed files are always kept, even when their suffix is not in
    ``extensions``: the single-file workflow relies on header sniffing for files such as
    ``bundle.bin``. Files found while scanning a folder are filtered by suffix.
    """
    extension_list = parse_extensions(extensions)
    excluded = _resolve_all(exclude_dirs)
    found: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        key = os.path.normcase(str(path))
        if key in seen:
            return
        seen.add(key)
        found.append(path)

    for raw in roots:
        check_cancel(cancel_event)
        root = Path(raw).expanduser()
        if root.is_file():
            add(root.resolve())
            continue
        if not root.is_dir():
            _log(log, f"跳过不存在的路径：{raw}")
            continue
        for candidate in _iter_candidates(root, extension_list, recursive, excluded, cancel_event):
            add(candidate)

    check_cancel(cancel_event)
    if not found:
        _log(log, "未找到匹配后缀的压缩包；请检查后缀列表或是否勾选「包含子文件夹」。")
    return found


def _normalize_passwords(passwords: Iterable[str] | str | None) -> list[str]:
    if passwords is None:
        return []
    raw: list[object] = passwords.splitlines() if isinstance(passwords, str) else list(passwords)
    result: list[str] = []
    for item in raw:
        if item is None:
            continue
        value = str(item)
        if not value or value in result:
            continue
        result.append(value)
    return result


def _promote(passwords: list[str], preferred: str | None) -> list[str]:
    """Move the password that last succeeded to the front of the candidate list."""
    if not preferred:
        return list(passwords)
    return [preferred] + [item for item in passwords if item != preferred]


def extract_batch(
    sources: Iterable[str | Path],
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
    on_progress: _PROGRESS | None = None,
) -> BatchReport:
    """Unpack every archive in ``sources`` into its own subfolder of ``output_base``.

    One failing archive never aborts the batch. Cancellation propagates, with the partial
    report attached as ``ExtractionCancelled.report``.
    """
    items = [Path(source).expanduser() for source in sources]
    base_path = Path(output_base).expanduser().resolve()
    if not isinstance(max_depth, int) or not 1 <= max_depth <= 20:
        raise ValueError("最大递归层数必须是 1 到 20 之间的整数")
    extension_list = parse_extensions(extensions)
    user_passwords = _normalize_passwords(passwords)
    report = BatchReport(output_base=base_path)
    total = len(items)
    if total == 0:
        _log(log, "没有待处理文件。")
        return report

    base_path.mkdir(parents=True, exist_ok=True)
    _log(log, f"共 {total} 个待处理压缩包，输出基目录：{base_path}")

    preferred: str | None = None
    promotion_announced = False

    for index, source in enumerate(items, start=1):
        _cancel(report, cancel_event)
        if not source.is_file():
            reason = "文件不存在或不可访问"
            report.items.append(BatchItemReport(source=source, error=reason))
            _log(log, f"[{index}/{total}] 跳过 {source.name}：{reason}")
            _notify(on_progress, index, total, source)
            continue

        def item_log(line: str, _index: int = index) -> None:
            _log(log, f"[{_index}/{total}] {line}")

        try:
            extraction = extract_recursive(
                source,
                base_path,
                extensions=extension_list,
                passwords=_promote(user_passwords, preferred),
                max_depth=max_depth,
                bandizip_path=bandizip_path,
                seven_zip_path=seven_zip_path,
                delete_nested=delete_nested,
                log=item_log,
                cancel_event=cancel_event,
            )
        except ExtractionCancelled as exc:
            exc.report = report  # type: ignore[attr-defined]
            raise
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            report.items.append(BatchItemReport(source=source, error=reason))
            _log(log, f"[{index}/{total}] 失败：{source.name} — {reason}")
            _notify(on_progress, index, total, source)
            continue

        report.items.append(
            BatchItemReport(
                source=source,
                output_dir=extraction.output_dir,
                extracted_archives=extraction.extracted_archives,
                remaining_archives=list(extraction.remaining_archives),
                warnings=list(extraction.warnings),
            )
        )
        _log(
            log,
            f"[{index}/{total}] 成功：{source.name} → {extraction.output_dir.name}"
            f"（解压 {extraction.extracted_archives} 个压缩包）",
        )
        if extraction.used_password and extraction.used_password != preferred:
            preferred = extraction.used_password
            if not promotion_announced:
                promotion_announced = True
                _log(log, "提示：后续任务将优先尝试最近一次成功的密码（仅本次运行，不落盘）。")
        _notify(on_progress, index, total, source)

    _log(
        log,
        f"批量处理结束：成功 {len(report.succeeded)} / 失败 {len(report.failed)} / 共 {total}；"
        f"累计解压 {report.extracted_archives} 个压缩包，"
        f"仍有 {len(report.remaining_archives)} 个未展开。",
    )
    return report
