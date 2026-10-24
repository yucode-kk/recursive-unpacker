<div align="center">

# 递归解压助手 · Recursive Unpacker

**Windows 友好的 Python GUI 工具：按文件头识别压缩格式、轮询候选密码，递归展开内层压缩包。**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows-blue.svg)](#环境要求)
[![Version](https://img.shields.io/badge/Version-0.1.0-blue.svg)](recursive_unpacker/__init__.py)

</div>

一个“选文件 → 填后缀候选与密码 → 一键递归展开”的桌面小工具。它不依赖扩展名判断格式，而是读取文件头；找不到正确后缀或密码时不会假装成功，会明确告诉你哪些压缩包没被解开。

---

## 目录

- [核心特性](#核心特性)
- [技术栈](#技术栈)
- [环境要求](#环境要求)
- [安装](#安装)
- [快速上手](#快速上手)
- [命令行 / API 用法](#命令行--api-用法)
- [目录结构](#目录结构)
- [FAQ](#faq)
- [贡献指南](#贡献指南)
- [许可证](#许可证)

---

## 核心特性

- **按文件头识别格式**：通过魔数（magic signature）识别 `ZIP`、`7z`、`RAR`、`TAR`、`GZIP`、`BZIP2`、`XZ`，不轻信文件后缀。
- **多后端解压**：优先调用 [Bandizip](https://www.bandisoft.com/bandizip/) 的 `bz.exe`；不可用时回退到 7-Zip（`7z.exe`），再回退到纯 Python 后端（`zipfile`/`pyzipper`、`py7zr`、`tarfile`、`gzip`/`bz2`/`lzma`）。
- **候选密码轮询**：先尝试无密码/空密码，再按顺序尝试你提供的候选密码；密码只在当前运行中使用，不写日志、不落盘。
- **递归展开**：自动识别并继续解压内层压缩包，默认最大深度 `5`（可设 `1–20`）。
- **安全校验（内置）**：
  - 拒绝路径穿越（`../`、绝对路径、盘符）与符号链接/特殊文件；
  - 对 ZIP/TAR 解压前预估体积，并对解压结果做文件数/体积复查（上限 100,000 文件 / 20 GiB）；
  - 原始输入文件**永不修改、永不删除**。
- **可视化解压过程**：后台线程执行、实时日志、可随时取消。
- **可打包为 EXE**：一键 `PyInstaller` 打包，脱离 Python 环境分发。

---

## 技术栈

| 类别 | 技术 |
| --- | --- |
| 语言 | Python 3.11+ |
| GUI | Tkinter（Python 标准库自带） |
| 解压后端 | Bandizip `bz.exe`、7-Zip `7z.exe`、`zipfile`/`pyzipper`、`py7zr`、`tarfile` |
| 测试 | pytest |
| 打包 | PyInstaller（可选） |

---

## 环境要求

- **操作系统**：Windows（GUI 面向 Windows；核心解压逻辑为纯 Python，可在其他平台运行）。
- **Python**：3.11 或更高，且包含 Tcl/Tk（Windows 官方安装包默认包含）。
- **可选外部程序**：
  - [Bandizip](https://www.bandisoft.com/bandizip/)（推荐，提供 `bz.exe`，用于 RAR 及常见格式，并支持解压前校验成员路径）；
  - [7-Zip](https://www.7-zip.org/)（可选回退，提供 `7z.exe`）。

---

## 安装

```bash
git clone https://github.com/yucode-kk/recursive-unpacker.git
cd recursive-unpacker
pip install -r requirements.txt
```

`requirements.txt` 只包含两个**可选**依赖（普通 ZIP 无需它们也能工作）：

- `py7zr`：直接以 Python 处理 7z；
- `pyzipper`：支持 AES 加密的 ZIP。

> 提示：在 Windows 上也可以直接双击 `run.bat`，首次运行会自动安装上述可选依赖。

---

## 快速上手

### 图形界面

1. 双击 `run.bat`（或执行 `python main.py`）启动。
2. 选择**待处理文件**与**输出目录**。
3. 在“尝试后缀”中填写候选后缀，例如 `.zip, .7z, .rar`。
4. 在“候选密码”中每行填一个可能的密码（可为空）。
5. （可选）在“Bandizip 控制台路径”中选择 `bz.exe`，或在“7-Zip 路径”中选择 `7z.exe`。
6. 点击“开始递归解压”，完成后点击“打开输出目录”。

### 命令行 / API 用法

核心逻辑可直接以 Python API 调用：

```python
from recursive_unpacker.core import extract_recursive

report = extract_recursive(
    "bundle.zip",                     # 输入文件
    "output",                         # 输出根目录
    extensions=[".zip", ".7z", ".rar"],
    passwords=["password1", "password2"],
    max_depth=5,
    delete_nested=True,
)

print(f"已解压 {report.extracted_archives} 个压缩包")
print(f"输出目录：{report.output_dir}")
print(f"仍未展开：{report.remaining_archives}")
print(f"警告：{report.warnings}")
```

`extract_recursive` 返回一个 `ExtractionReport`，包含 `output_dir`、`extracted_archives`、`remaining_archives` 与 `warnings`。达到递归上限或某个压缩包无法展开时，会将其保留在 `remaining_archives` 并明确报告，而不会假装全部完成。

---

## 目录结构

```
recursive-unpacker/
├── main.py                     # 应用入口
├── recursive_unpacker/
│   ├── __init__.py             # 版本号
│   ├── core.py                 # 格式识别、安全校验、多后端解压、递归逻辑
│   └── gui.py                  # Tkinter 图形界面
├── tests/
│   └── test_core.py            # 单元测试（真实临时归档样本）
├── requirements.txt            # 可选运行时依赖
├── requirements-dev.txt        # 开发/测试依赖
├── run.bat                     # Windows 一键启动
├── build_exe.bat               # PyInstaller 打包脚本
├── LICENSE                     # MIT 许可证
└── README.md
```

---

## FAQ

**Q1：为什么某些压缩包没有被解开？**
通常是以下两种原因之一：达到了最大递归深度，或该格式在当前后端不可用（例如 RAR 需要安装 Bandizip 或 7-Zip）。程序会把这些压缩包保留并列出，而不是静默跳过。

**Q2：我的密码会被保存或泄露吗？**
不会。密码仅作为当前进程的参数使用，不写入日志，也不写入任何配置文件。不过，使用外部 Bandizip / 7-Zip 处理加密归档时，密码会作为命令行参数传入该子进程，在共享设备上请注意本机其他用户的进程可见性。

**Q3：这是密码破解工具吗？**
不是。它只会按顺序尝试你提供的候选密码，不会进行任何暴力破解。

**Q4：支持 macOS / Linux 吗？**
核心解压逻辑（`core.py`）是跨平台的，可直接在任意平台以 API 方式调用；GUI 使用 Tkinter，理论上可跨平台运行，但 `bz.exe` / `7z.exe` 的自动路径查找主要面向 Windows。

**Q5：解压会不会损坏我的原始文件？**
不会。原始输入文件始终保留，结果写入输出目录下以源文件名命名的子文件夹；同名目录已存在时会自动生成新名称。

---

## 贡献指南

欢迎提交 Issue 与 Pull Request。请遵循以下约定：

1. 理解项目的全局约束：**永不修改原始文件、不记录密码、拒绝不安全路径、未完全展开时必须如实报告**。
2. 提交信息遵循 [Conventional Commits](https://www.conventionalcommits.org/)（如 `feat:`、`fix:`、`test:`、`docs:`）。
3. 修改后运行测试：

```bash
pip install -r requirements-dev.txt
pytest -q
python -m compileall -q main.py recursive_unpacker tests
```

4. 保持代码小步、可测试；涉及解压/安全逻辑的改动请补充对应测试用例。

---

## 许可证

本项目采用 [MIT License](LICENSE) 开源。Copyright © 2026 Recursive Unpacker contributors。
