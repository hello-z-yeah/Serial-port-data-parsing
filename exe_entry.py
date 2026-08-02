"""exe 入口：双击运行，优先 GUI（PySide6 + qfluentwidgets），不可用时降级 CLI。"""
from __future__ import annotations

import io
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path


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


def _exe_crash_dir() -> Path:
    try:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
        return Path(__file__).resolve().parent
    except Exception:
        return Path.cwd()


def _write_crash_log(exc: BaseException) -> Path | None:
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
        stdout = getattr(sys, "stdout", None)
        stderr = getattr(sys, "stderr", None)
        if stdout is not None and hasattr(stdout, "reconfigure"):
            stdout.reconfigure(encoding="utf-8")
        if stderr is not None and hasattr(stderr, "reconfigure"):
            stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def setup_paths() -> None:
    if hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS)
        sys.path.insert(0, str(base))
    else:
        base = Path(__file__).resolve().parent
        sys.path.insert(0, str(base))
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


def main() -> int:
    setup_console_utf8()
    setup_paths()

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
            QMessageBox.critical(None, "GUI 启动失败", msg)
        except Exception:
            print(f"[启动失败] {exc}", file=sys.stderr)
            if log_path:
                print(f"           日志: {log_path}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (SystemExit, KeyboardInterrupt):
        raise
    except BaseException as exc:
        _log = _write_crash_log(exc)
        print(f"[终极兜底] {exc}", file=sys.stderr)
        sys.exit(99)
