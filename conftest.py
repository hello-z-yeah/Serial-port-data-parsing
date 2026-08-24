"""pytest 根配置：规避 Qt 测试在解释器退出阶段的偶发挂起/原生崩溃。

全部测试通过并输出汇总后，解释器关闭阶段可能因 Qt 底层 C++ 对象析构、
残留线程 join 或 ExitProcess 的 DLL detach 触碰已释放内存，偶发访问冲突
（退出码 0xC0000005 / 3221225477）或无限挂起，使构建管理器把"测试全绿"
误判为失败。

pytest 的结果汇总与缓存写入均在 pytest_unconfigure 之前完成，因此在
最后一个 unconfigure 钩子中用真实测试退出码终止进程。使用
TerminateProcess 而非 os._exit/ExitProcess：前者立即终止进程且不触发
DLL_PROCESS_DETACH，绕开 detach 阶段的访问冲突与挂起。
"""
from __future__ import annotations

import os
import sys

import pytest

_PYTEST_EXIT_CODE = 0


def pytest_configure(config) -> None:
    """Headless Qt for all tests so build-installer does not flash the main window."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def ensure_qt_app():
    """Create or reuse QApplication with SerialX UI patches installed."""
    from PySide6.QtWidgets import QApplication

    from protocol_parser.ui_corners import install_corner_radius_controller

    app = QApplication.instance() or QApplication([])
    install_corner_radius_controller(app)
    return app


def pytest_sessionfinish(session, exitstatus) -> None:
    global _PYTEST_EXIT_CODE
    try:
        _PYTEST_EXIT_CODE = int(exitstatus)
    except (TypeError, ValueError):
        _PYTEST_EXIT_CODE = 1


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config) -> None:
    # 汇总行在 unconfigure 前已写入 stdout 缓冲区，必须先 flush，
    # 否则 TerminateProcess 会丢弃汇总输出。
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    # 直接 TerminateProcess：立即终止且不触发 DLL_PROCESS_DETACH，
    # 绕开退出阶段访问冲突/挂起；退出码与真实测试结果一致。
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        # 必须显式声明 64 位句柄类型：默认 c_int 会截断伪句柄 -1，
        # 使 TerminateProcess 静默失败并回退到 os._exit（仍会崩溃）。
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.TerminateProcess.restype = ctypes.c_int
        handle = kernel32.GetCurrentProcess()
        kernel32.TerminateProcess(handle, _PYTEST_EXIT_CODE & 0xFFFFFFFF)
    except Exception:
        pass
    import os

    os._exit(_PYTEST_EXIT_CODE)
