from __future__ import annotations

from pathlib import Path

from recursive_unpacker.volumes import find_volume_set, is_volume_start, volume_files


def _touch(folder: Path, name: str) -> Path:
    path = folder / name
    path.write_bytes(b"piece")
    return path


def test_find_volume_set_collects_every_piece(tmp_path: Path) -> None:
    first = _touch(tmp_path, "M.7z.001")
    _touch(tmp_path, "M.7z.002")
    _touch(tmp_path, "M.7z.003")

    found = find_volume_set(first)

    assert found is not None
    assert found.first == first
    assert [piece.name for piece in found.volumes] == ["M.7z.001", "M.7z.002", "M.7z.003"]
    assert found.count == 3
    assert found.is_complete
    assert found.missing == ()


def test_find_volume_set_reports_gaps(tmp_path: Path) -> None:
    first = _touch(tmp_path, "M.7z.001")
    _touch(tmp_path, "M.7z.003")

    found = find_volume_set(first)

    assert found is not None
    assert found.count == 2
    assert not found.is_complete
    assert found.missing == (2,)
    assert found.describe_missing() == ".002"


def test_find_volume_set_resolves_from_a_later_piece(tmp_path: Path) -> None:
    first = _touch(tmp_path, "M.7z.001")
    _touch(tmp_path, "M.7z.002")
    third = _touch(tmp_path, "M.7z.003")

    found = find_volume_set(third)

    assert found is not None
    assert found.first == first
    assert found.count == 3


def test_find_volume_set_ignores_unrelated_names(tmp_path: Path) -> None:
    plain = _touch(tmp_path, "M.7z")

    assert find_volume_set(plain) is None


def test_find_volume_set_does_not_mix_different_bases(tmp_path: Path) -> None:
    first = _touch(tmp_path, "A.7z.001")
    _touch(tmp_path, "A.7z.002")
    _touch(tmp_path, "B.7z.001")

    found = find_volume_set(first)

    assert found is not None
    assert [piece.name for piece in found.volumes] == ["A.7z.001", "A.7z.002"]


def test_is_volume_start_requires_a_following_piece(tmp_path: Path) -> None:
    lonely = _touch(tmp_path, "Solo.7z.001")

    assert is_volume_start(lonely) is False

    _touch(tmp_path, "Solo.7z.002")

    assert is_volume_start(lonely) is True


def test_is_volume_start_is_false_for_later_pieces(tmp_path: Path) -> None:
    _touch(tmp_path, "M.7z.001")
    second = _touch(tmp_path, "M.7z.002")

    assert is_volume_start(second) is False


def test_volume_files_falls_back_to_the_single_path(tmp_path: Path) -> None:
    plain = _touch(tmp_path, "single.zip")

    assert volume_files(plain) == (plain,)


def test_volume_files_returns_the_whole_set(tmp_path: Path) -> None:
    first = _touch(tmp_path, "M.7z.001")
    second = _touch(tmp_path, "M.7z.002")

    assert volume_files(first) == (first, second)
