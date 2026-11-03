"""把 version_info.txt 与 recursive_unpacker.__version__ 钉在一起，防止版本号漂移。

Windows 版本资源里有四组独立数据（filevers / prodvers / FileVersion / ProductVersion），
任何一组漏改都会让 exe 属性页显示旧版本，所以这里全部覆盖。

刻意不 import PyInstaller：它会连带拉 pefile / pywin32-ctypes，导致只装
requirements-dev.txt 的环境直接崩。改用 ast 解析，顺带验证「整份文件是一个表达式」
这条 PyInstaller 的硬性契约。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from recursive_unpacker import __version__

VERSION_FILE = Path(__file__).resolve().parents[1] / "version_info.txt"


def _padded(version: str) -> tuple[int, int, int, int]:
    """把 "0.2.0" 补成 FixedFileInfo 要求的四元组 (0, 2, 0, 0)。"""
    parts = version.strip().split(".")
    try:
        numbers = [int(part) for part in parts]
    except ValueError:
        pytest.fail(
            f"__version__ = {version!r} 含非数字段，无法写入 Windows 版本资源；"
            "请使用形如 '0.2.0' 的纯数字版本号（预发布后缀不能进 PE 资源）。"
        )
    assert 1 <= len(numbers) <= 4, f"__version__ = {version!r} 段数必须在 1..4 之间"
    numbers += [0] * (4 - len(numbers))
    assert all(0 <= value <= 0xFFFF for value in numbers), (
        f"__version__ = {version!r} 每段必须落在 0..65535（PE 中按 16 位存储）"
    )
    return tuple(numbers)  # type: ignore[return-value]


@pytest.fixture(scope="module")
def version_tree() -> ast.Expression:
    assert VERSION_FILE.is_file(), f"缺少版本资源文件：{VERSION_FILE}"
    text = VERSION_FILE.read_text(encoding="utf-8")
    try:
        return ast.parse(text, filename=str(VERSION_FILE), mode="eval")
    except SyntaxError as exc:
        pytest.fail(
            f"{VERSION_FILE.name} 必须是单个 Python 表达式（PyInstaller 用 eval 加载），"
            f"当前无法解析：{exc}"
        )


def _calls(tree: ast.AST, name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name
    ]


def _string_values(tree: ast.AST) -> dict[str, str]:
    values: dict[str, str] = {}
    for call in _calls(tree, "StringStruct"):
        if len(call.args) < 2:
            continue
        key = ast.literal_eval(call.args[0])
        value = ast.literal_eval(call.args[1])
        if isinstance(key, str) and isinstance(value, str):
            values[key] = value
    return values


def test_version_file_is_a_single_expression() -> None:
    assert VERSION_FILE.is_file(), f"缺少版本资源文件：{VERSION_FILE}"
    text = VERSION_FILE.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text, filename=VERSION_FILE.name, mode="eval")
    except SyntaxError as exc:
        pytest.fail(
            f"{VERSION_FILE.name} 必须是单个 Python 表达式（PyInstaller 用 eval 加载）：{exc}"
        )
    assert isinstance(tree.body, ast.Call), "顶层必须是 VSVersionInfo(...) 调用"
    assert isinstance(tree.body.func, ast.Name) and tree.body.func.id == "VSVersionInfo"


def test_fixed_file_info_matches_package_version(version_tree: ast.Expression) -> None:
    expected = _padded(__version__)
    calls = _calls(version_tree, "FixedFileInfo")
    assert len(calls) == 1, f"version_info.txt 应恰好有 1 个 FixedFileInfo，实际 {len(calls)} 个"

    keywords = {keyword.arg: keyword.value for keyword in calls[0].keywords}
    for field in ("filevers", "prodvers"):
        node = keywords.get(field)
        assert node is not None, f"FixedFileInfo 缺少参数 {field}"
        actual = tuple(ast.literal_eval(node))
        assert actual == expected, (
            f"version_info.txt 的 {field}={actual} 与 "
            f"recursive_unpacker.__version__={__version__!r} 不一致，应为 {expected}"
        )


def test_string_versions_match_package_version(version_tree: ast.Expression) -> None:
    values = _string_values(version_tree)

    for field in ("FileVersion", "ProductVersion"):
        assert field in values, f"version_info.txt 缺少 StringStruct('{field}', ...)"
        assert values[field].strip() == __version__.strip(), (
            f"version_info.txt 的 {field}={values[field]!r} 与 "
            f"recursive_unpacker.__version__={__version__!r} 不一致"
        )


def test_product_identity_is_present(version_tree: ast.Expression) -> None:
    values = _string_values(version_tree)

    for field in ("ProductName", "FileDescription", "InternalName", "OriginalFilename"):
        assert values.get(field, "").strip(), f"version_info.txt 的 {field} 为空"
    assert values["OriginalFilename"] == "RecursiveUnpacker.exe"


def test_language_and_codepage_are_consistent(version_tree: ast.Expression) -> None:
    """StringTable 的键必须与 VarStruct('Translation') 一致，否则属性页取不到字符串。"""
    tables = _calls(version_tree, "StringTable")
    assert len(tables) == 1, "应恰好有一个 StringTable"
    key = ast.literal_eval(tables[0].args[0])
    assert isinstance(key, str) and len(key) == 8, f"StringTable 键必须是 8 位十六进制，当前 {key!r}"

    translation: list[int] | None = None
    for call in _calls(version_tree, "VarStruct"):
        if len(call.args) < 2:
            continue
        if ast.literal_eval(call.args[0]) == "Translation":
            translation = ast.literal_eval(call.args[1])
            break
    assert translation, "缺少 VarStruct('Translation', [...])"

    langid, codepage = translation
    assert key.upper() == f"{langid:04X}{codepage:04X}", (
        f"StringTable 键 {key!r} 与 Translation=[{langid}, {codepage}] 不一致，"
        f"应写成 '{langid:04X}{codepage:04X}'"
    )
