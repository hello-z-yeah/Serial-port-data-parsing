"""程序入口。

优化重点：
- 打包后的 EXE 不再做运行时依赖扫描；
- 启动阶段不提前导入 subprocess、traceback、importlib 等较重模块；
- 源码模式仅在确实缺少依赖时才加载 pip 相关模块。
"""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path

from protocol_parser.app_info import APP_NAME


class DummyFileIO(io.StringIO):
    def write(self, s: str) -> int:
        try:
            return super().write(s)
        except Exception:
            return 0

    def flush(self) -> None:
        try:
            super().flush()
        except Exception:
            pass


def _patch_stdio_for_windowed() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None or not hasattr(stream, "write"):
            try:
                setattr(sys, name, DummyFileIO())
            except Exception:
                pass


_patch_stdio_for_windowed()

# 减少 Qt 在正常启动时的无关日志输出。
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.*=false;qt.text.*=false")

_RUNTIME_DEPENDENCIES = (
    ("PySide6", "PySide6>=6.6.0"),
    ("qfluentwidgets", "PySide6-Fluent-Widgets>=1.5.0"),
    ("serial", "pyserial>=3.5"),
    ("docx", "python-docx>=0.8.11"),
)


def _missing_runtime_packages() -> list[str]:
    import importlib.util

    missing: list[str] = []
    for import_name, package_name in _RUNTIME_DEPENDENCIES:
        try:
            available = importlib.util.find_spec(import_name) is not None
        except (ImportError, AttributeError, ValueError):
            available = False
        if not available:
            missing.append(package_name)
    return missing


def _ensure_runtime_dependencies() -> tuple[bool, str]:
    # PyInstaller 包中的依赖在构建阶段已经固定收集。跳过 find_spec 可缩短启动路径。
    if getattr(sys, "frozen", False):
        return True, ""

    missing = _missing_runtime_packages()
    if not missing:
        return True, ""

    if os.environ.get("SPT_DISABLE_AUTO_INSTALL", "").strip() == "1":
        return False, (
            "当前 Python 环境缺少：" + "、".join(missing)
            + "。请执行：\n"
            + f'"{sys.executable}" -m pip install -r requirements.txt'
        )

    import importlib
    import subprocess

    print("[依赖检查] 缺少：" + "、".join(missing))
    print("[依赖安装] 正在为当前 Python 自动安装，请稍候……")
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        *missing,
    ]
    try:
        completed = subprocess.run(command, check=False)
    except OSError as exc:
        return False, f"无法启动 pip：{exc}"

    if completed.returncode != 0:
        return False, (
            "依赖自动安装失败。请在项目目录手动执行：\n"
            + f'"{sys.executable}" -m pip install -r requirements.txt'
        )

    importlib.invalidate_caches()
    still_missing = _missing_runtime_packages()
    if still_missing:
        return False, "pip 已执行，但仍无法导入：" + "、".join(still_missing)
    return True, ""


def _exe_crash_dir() -> Path:
    """Return the writable LocalAppData log directory."""
    try:
        from protocol_parser.paths import logs_dir

        return logs_dir()
    except Exception:
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
        path = root / "SuperMaxSerialTool" / "logs"
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            return Path.cwd()
        return path


def _write_crash_log(exc: BaseException) -> Path | None:
    from datetime import datetime
    import traceback

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
        return path
    except Exception:
        return None


def setup_console_utf8() -> None:
    if sys.platform != "win32":
        return
    try:
        for stream_name in ("stdout", "stderr"):
            stream = getattr(sys, stream_name, None)
            if stream is not None and hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")
    except Exception:
        pass


def setup_paths() -> None:
    if hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).resolve().parent
    base_text = str(base)
    if base_text not in sys.path:
        sys.path.insert(0, base_text)

    # Seed/migrate mutable products and data under LocalAppData.  The install
    # directory remains read-only and contains bundled defaults only.
    try:
        from protocol_parser.paths import app_data_root, get_protocol_dir

        app_data_root()
        get_protocol_dir()
    except Exception:
        pass


def main() -> int:
    setup_console_utf8()
    setup_paths()

    dependencies_ok, dependency_error = _ensure_runtime_dependencies()
    if not dependencies_ok:
        log_path = _write_crash_log(RuntimeError(dependency_error))
        print(f"[启动失败] {dependency_error}", file=sys.stderr)
        if log_path:
            print(f"           日志: {log_path}", file=sys.stderr)
        return 2

    try:
        from protocol_parser.gui import main as gui_main
        return gui_main()
    except (SystemExit, KeyboardInterrupt):
        raise
    except BaseException as exc:
        log_path = _write_crash_log(exc)
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox

            app = QApplication.instance() or QApplication(sys.argv)
            msg = f"{type(exc).__name__}: {exc}"
            if log_path:
                msg += f"\n\n崩溃日志：{log_path}"
            QMessageBox.critical(None, f"{APP_NAME} 启动失败", msg)
        except Exception:
            print(f"[启动失败] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
