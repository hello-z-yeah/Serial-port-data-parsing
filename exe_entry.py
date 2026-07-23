"""exe 入口：双击运行，优先 GUI（Tkinter），不可用时降级 CLI。

打包命令（官方 Python 3.11+ + Tcl/Tk + PyInstaller）:
    pyinstaller --onefile --windowed --name "串口数据解析" ^
        --hidden-import docx --hidden-import docx.opc.constants ^
        --add-data "product;product" --add-data "protocol_parser;protocol_parser" ^
        exe_entry.py

--- 闪退说明 ------------------------------------------------------------------
PyInstaller --windowed（= --noconsole）模式下：
  * sys.stdout / sys.stderr 默认是 None，任何 print() / logger StreamHandler 写 stderr 都会
    AttributeError 闪退；
  * 启动期 ImportError/TclError/KeyError 等异常，堆栈默认写到无效 stderr，用户看到"弹窗瞬间没了"。
  因此本入口提供 3 层兜底：
    1) 启动立即替换 stdout/stderr 为 DummyFileIO，防止任何 print 崩溃；
    2) 所有异常捕获后写 exe 同级目录 crash_时间戳.log；
    3) Tk 若能初始化，弹 messagebox 提示 crash 路径。
"""
from __future__ import annotations

import io
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# 1. 防止 --windowed 下 stdout/stderr=None 导致任何 print 直接崩溃
# ---------------------------------------------------------------------------

class DummyFileIO(io.StringIO):
    """--windowed 模式下 stdout/stderr 的空实现：吞掉所有 write，避免 AttributeError。"""

    def write(self, s: str) -> int:  # noqa: D401
        try:
            return super().write(s)
        except Exception:
            return 0

    def flush(self) -> None:  # noqa: D401
        try:
            super().flush()
        except Exception:
            pass


def _patch_stdio_for_windowed() -> None:
    """PyInstaller --windowed 下把 None 的 stdout/stderr 替换成安全 Dummy。"""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None or not hasattr(stream, "write"):
            try:
                setattr(sys, name, DummyFileIO())
            except Exception:
                pass


_patch_stdio_for_windowed()


# ---------------------------------------------------------------------------
# 2. 崩溃日志：写到 exe 同级目录（开发模式下写项目根目录）
# ---------------------------------------------------------------------------


def _exe_crash_dir() -> Path:
    """崩溃日志目录：
    * sys.frozen=True（打包 exe）：exe 所在目录；
    * 开发模式：脚本同级目录。
    """
    try:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
        return Path(__file__).resolve().parent
    except Exception:
        return Path.cwd()


def _write_crash_log(exc: BaseException) -> Path | None:
    """把异常 + 完整 traceback 写到 crash_YYYYmmdd_HHMMSS.log。"""
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = _exe_crash_dir() / f"crash_{ts}.log"
        tb = traceback.format_exc()
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"Time:      {datetime.now().isoformat(timespec='seconds')}\n")
            f.write(f"Frozen:    {getattr(sys, 'frozen', False)}\n")
            f.write(f"Executable:{sys.executable}\n")
            f.write(f"MEIPASS:   {getattr(sys, '_MEIPASS', '')}\n")
            f.write(f"CWD:       {os.getcwd()}\n")
            f.write(f"Argv:      {sys.argv}\n")
            f.write("\n========== Exception Type ==========\n")
            f.write(f"{type(exc).__module__}.{type(exc).__name__}: {exc}\n")
            f.write("\n========== Traceback ==========\n")
            f.write(tb)
            f.write("\n========== sys.path ==========\n")
            for p in sys.path:
                f.write(p + "\n")
        return path
    except Exception:
        # 连写崩溃日志都失败就无能为力了
        return None


def _show_crash_dialog(title: str, message: str, log_path: Path | None) -> None:
    """尽力弹窗告诉用户崩溃了 + crash.log 路径。Tk 起不来就算了。"""
    try:
        import tkinter as tk
        from tkinter import messagebox

        try:
            tmp_root = tk.Tk()
            tmp_root.withdraw()
            tmp_root.attributes("-topmost", True)
        except Exception:
            tmp_root = None
        try:
            full = f"{message}"
            if log_path is not None:
                full += f"\n\n崩溃日志：{log_path}"
            messagebox.showerror(title, full)
        finally:
            if tmp_root is not None:
                try:
                    tmp_root.destroy()
                except Exception:
                    pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 3. 控制台编码 / 路径
# ---------------------------------------------------------------------------


def setup_console_utf8() -> None:
    """Windows 控制台默认 GBK，强制 UTF-8 防中文乱码（对 --windowed 无副作用）。"""
    if sys.platform != "win32":
        return
    try:
        stdout = getattr(sys, "stdout", None)
        stderr = getattr(sys, "stderr", None)
        if stdout is not None and hasattr(stdout, "reconfigure"):
            stdout.reconfigure(encoding="utf-8")
        if stderr is not None and hasattr(stderr, "reconfigure"):
            stderr.reconfigure(encoding="utf-8")
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
        # 若 exe_entry.py 直接放在项目根，则 protocol_parser 在项目根里，
        # 把项目根自己加进 sys.path，保证开发模式与打包后行为一致
        sys.path.insert(0, str(base))

    # user product 目录优先级：exe 同级 product > _MEIPASS/product
    try:
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).resolve().parent
        else:
            exe_dir = Path(__file__).resolve().parent
        user_protos = exe_dir / "product"
        if user_protos.exists():
            os.environ["PROTOCOL_DIR"] = str(user_protos)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 4. main：两层 try/except 把所有启动期异常 → 写 crash.log + 弹窗
# ---------------------------------------------------------------------------


def main() -> int:
    setup_console_utf8()
    setup_paths()

    # --- 第一层：尝试 GUI（任何时候都优先 GUI） ---
    gui_exc: BaseException | None = None
    try:
        import tkinter  # noqa: F401
        from protocol_parser.gui import main as gui_main

        return gui_main()
    except (SystemExit, KeyboardInterrupt):
        raise
    except BaseException as exc:  # noqa: BLE001 - 真·顶层任何错误都兜
        gui_exc = exc
        log_path = _write_crash_log(exc)

    # --- 第二层：GUI 启动失败后处理 ---
    #
    #   关键判断：
    #   1. 打包 EXE + 无 stdin：绝不能进 CLI（input() 会 RuntimeError: lost sys.stdin，闪退 traceback 就是本次）
    #   2. 有 stdin + 用户显式传了 CLI 参数（非空 argv 且不是简单的无参调用）：允许降级进入 CLI
    #   3. 其余情况 → 直接弹 messagebox 提示「GUI 启动失败 + crash.log 路径」，返回 1，不进 input()
    def _has_usable_stdin() -> bool:
        stdin = getattr(sys, "stdin", None)
        if stdin is None:
            return False
        # 开发模式/从 cmd 手动启动：sys.stdin 为 None 或 fileno() 不可用时直接 False
        try:
            if not stdin.readable():
                return False
        except Exception:
            return False
        try:
            _ = stdin.fileno()
        except Exception:
            # StringIO / io.StringIO 等无 fileno 的对象一律判定"不可交互"
            return False
        # tty / 管道 / < 重定向 都认为可用，input() 不会抛 lost sys.stdin
        return True

    user_passed_cli_args: bool = bool(sys.argv and len(sys.argv) > 1)
    can_use_cli: bool = _has_usable_stdin() and user_passed_cli_args

    if not can_use_cli:
        # --- 不进 CLI：弹 messagebox，把 GUI 的崩溃原因 + log 路径展示给用户 ---
        title = "GUI 启动失败"
        if gui_exc is None:
            message = "程序初始化失败，无法启动图形界面。"
        else:
            try:
                friendly_name = f"{type(gui_exc).__module__}.{type(gui_exc).__name__}"
            except Exception:
                friendly_name = type(gui_exc).__name__
            exc_str = str(gui_exc)
            message = f"{friendly_name}: {exc_str}" if exc_str else friendly_name
        log_ref = _write_crash_log(gui_exc) if gui_exc is not None else None
        _show_crash_dialog(title, message, log_ref)
        return 1

    # --- CLI 降级分支（仅当：有有效 stdin + 用户显式传了参数） ---
    try:
        from protocol_parser.cli import main as cli_main
    except BaseException as exc:  # noqa: BLE001
        log_path = _write_crash_log(exc)
        _show_crash_dialog("启动失败", "导入 CLI 模块失败，请检查文件是否完整。", log_path)
        return 2

    try:
        print("=" * 60)
        print("  协议解析工具 V3.0 (CLI 降级模式)")
        print("  GUI 启动失败，但检测到 stdin+参数，自动切到命令行模式。")
        print("=" * 60)
        print()
    except Exception:
        pass

    try:
        return cli_main(sys.argv[1:])
    except (SystemExit, KeyboardInterrupt):
        raise
    except BaseException as exc:  # noqa: BLE001
        log_path = _write_crash_log(exc)
        _show_crash_dialog("CLI 运行失败", "命令行模式遇到错误，请查看崩溃日志。", log_path)
        return 3


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (SystemExit, KeyboardInterrupt):
        raise
    except BaseException as exc:  # noqa: BLE001
        # 终极兜底：任何没兜住的都写日志，绝不白屏闪退
        _log = _write_crash_log(exc)
        _show_crash_dialog("启动失败", "程序启动失败，详情见崩溃日志。", _log)
        sys.exit(99)
