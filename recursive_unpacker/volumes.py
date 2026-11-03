"""Split-volume archive helpers (`.7z.001`, `.zip.001`, `.001`).

A split set is a single archive cut into fixed-size pieces. The volume chain is resolved
from the file name plus the neighbouring pieces, which is why external tools must be
handed the *original* `.001` path: renaming or copying one piece breaks the chain.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

VOLUME_SUFFIX = re.compile(r"^(?P<base>.+)\.(?P<index>\d{3})$")


@dataclass(frozen=True)
class VolumeSet:
    """Every piece that makes up one split archive, in ascending order."""

    first: Path
    volumes: tuple[Path, ...]
    missing: tuple[int, ...] = ()

    @property
    def count(self) -> int:
        return len(self.volumes)

    @property
    def is_complete(self) -> bool:
        return not self.missing

    def describe_missing(self) -> str:
        return "、".join(f".{index:03d}" for index in self.missing)


def _sibling_indices(folder: Path, base: str) -> list[int]:
    try:
        entries = list(folder.iterdir())
    except OSError:
        return []
    indices: list[int] = []
    for entry in entries:
        match = VOLUME_SUFFIX.match(entry.name)
        if not match or match.group("base") != base:
            continue
        try:
            if entry.is_file():
                indices.append(int(match.group("index")))
        except OSError:
            continue
    indices.sort()
    return indices


def find_volume_set(path: str | Path) -> VolumeSet | None:
    """Return the whole split set for a `.NNN` piece, or ``None`` for any other name."""
    candidate = Path(path)
    match = VOLUME_SUFFIX.match(candidate.name)
    if not match:
        return None
    base = match.group("base")
    indices = _sibling_indices(candidate.parent, base)
    if not indices:
        return None
    volumes = tuple(candidate.parent / f"{base}.{index:03d}" for index in indices)
    present = set(indices)
    missing = tuple(index for index in range(1, indices[-1] + 1) if index not in present)
    return VolumeSet(first=volumes[0], volumes=volumes, missing=missing)


def is_volume_start(path: str | Path) -> bool:
    """True when ``path`` is a `.001` piece that has at least one following piece."""
    candidate = Path(path)
    match = VOLUME_SUFFIX.match(candidate.name)
    if not match or int(match.group("index")) != 1:
        return False
    return any(index > 1 for index in _sibling_indices(candidate.parent, match.group("base")))


def volume_files(path: str | Path) -> tuple[Path, ...]:
    """Every file belonging to the same split set, or just ``path`` when it is not one."""
    found = find_volume_set(path)
    return found.volumes if found else (Path(path),)
