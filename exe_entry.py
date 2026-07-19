"""exe 入口：双击运行，进入交互式粘贴解析模式。

打包命令（在装有 tkinter/PyInstaller 的官方 Python 下）:
    pyinstaller --onefile --name ProtocolParser --add-data "product;product" --add-data "protocol_parser;protocol_parser" exe_entry.py

不带 GUI 的简化版本（当前环境可打包）:
    pyinstaller --onefile --name ProtocolParserCLI --add-data "product;product" --add-data "protocol_parser;protocol_parser" --exclude-module tkinter exe_entry.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def setup_console_utf8() -> None:
    """Windows 控制台默认 GBK，强制 UTF-8 防中文乱码。"""
    if sys.platform != "win32":
        return
    try:
        # 重新配置 stdout/stderr 为 UTF-8
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        os.system("chcp 65001 > nul 2>&1")
    except Exception:
        pass


def setup_paths() -> None:
    """让 PyInstaller 打包后的 exe 也能找到 protocol_parser 包和 product 目录。"""
    if hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS)
        sys.path.insert(0, str(base))
    else:
        base = Path(__file__).resolve().parent
        sys.path.insert(0, str(base.parent))

    if hasattr(sys, "executable"):
        exe_dir = Path(sys.executable).resolve().parent
        user_protos = exe_dir / "product"
        if user_protos.exists():
            os.environ["PROTOCOL_DIR"] = str(user_protos)


def main() -> int:
    setup_console_utf8()
    setup_paths()

    # 尝试用 GUI 启动；tkinter 不可用时降级到 CLI
    try:
        import tkinter  # noqa: F401
        from protocol_parser.gui import main as gui_main
        return gui_main()
    except ImportError:
        print("=" * 60)
        print("  协议解析工具 V3.0 (CLI 模式)")
        print("  当前 Python 没有 tkinter，无法启动 GUI。")
        print("  如需 GUI，请安装 python.org 官方 Python 后重新打包。")
        print("=" * 60)
        print()

        from protocol_parser.cli import main as cli_main

        # 默认进入 paste 模式
        argv = ["paste", "--product", "v3_serial"]
        if len(sys.argv) > 1:
            argv = sys.argv[1:]
        return cli_main(argv)


if __name__ == "__main__":
    sys.exit(main())
