"""资源路径 / 用户数据路径 / 崩溃日志（兼容 PyInstaller）。"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path


def resource_path(relative: str) -> Path:
    """获取资源路径（只读/内置资源）：优先 _MEIPASS。不要用来写文件。"""
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / relative
    base = Path(__file__).resolve().parent
    candidate = base / relative
    if candidate.exists():
        return candidate
    return base.parent / relative


def user_data_path(relative: str = "") -> Path:
    """用户可写数据目录。"""
    try:
        if getattr(sys, "frozen", False) and hasattr(sys, "executable"):
            exe_dir = Path(sys.executable).resolve().parent
            try:
                write_probe = exe_dir / ".write_probe"
                write_probe.write_text("probe", encoding="utf-8")
                write_probe.unlink(missing_ok=True)
                root = exe_dir
            except (OSError, PermissionError):
                doc_dir = Path.home() / "Documents"
                if not doc_dir.exists():
                    doc_dir = Path.home()
                root = doc_dir / "串口解析工具"
            data_dir = root / "data"
        else:
            project_root = Path(__file__).resolve().parent.parent
            data_dir = project_root / "data"
        if relative:
            data_dir = data_dir / relative
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir
    except Exception:
        fb = Path.home() / "串口解析工具" / "data"
        if relative:
            fb = fb / relative
        fb.mkdir(parents=True, exist_ok=True)
        return fb


def get_protocol_dir() -> Path:
    """用户可见的 product/ 目录。"""
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        proto_dir = exe_dir / "product"
        proto_dir.mkdir(parents=True, exist_ok=True)
        return proto_dir
    dev = Path(__file__).resolve().parent.parent / "product"
    dev.mkdir(parents=True, exist_ok=True)
    return dev


def crash_log_dir() -> Path:
    try:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
        return Path(__file__).resolve().parent.parent
    except Exception:
        return Path.cwd()


def write_crash_log(exc: BaseException) -> Path | None:
    """启动/运行期崩溃 → 写 crash_时间戳.log。"""
    import traceback as _tb

    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = crash_log_dir() / f"crash_{ts}.log"
        tb_s = _tb.format_exc()
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"Time:       {datetime.now().isoformat(timespec='seconds')}\n")
            f.write(f"Frozen:     {getattr(sys, 'frozen', False)}\n")
            f.write(f"Executable: {sys.executable}\n")
            f.write(f"MEIPASS:    {getattr(sys, '_MEIPASS', '')}\n")
            f.write(f"CWD:        {os.getcwd()}\n")
            f.write(f"Argv:       {sys.argv}\n")
            f.write("\n========== Exception ==========\n")
            f.write(f"{type(exc).__module__}.{type(exc).__name__}: {exc}\n")
            f.write("\n========== Traceback ==========\n")
            f.write(tb_s)
            f.write("\n========== sys.path ==========\n")
            for p in sys.path:
                f.write(p + "\n")
        return path
    except Exception:
        return None