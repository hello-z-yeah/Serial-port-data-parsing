"""程序入口：先实例化 QApplication，再导入并实例化 MainWindow。

红线：
- 严禁在 QApplication 创建前实例化任何 QWidget / QFont / qfluentwidgets 控件。
- 主题设置必须在 QApplication 已创建之后、MainWindow 实例化之前完成。
"""
from __future__ import annotations

import sys
import traceback
from datetime import datetime
from pathlib import Path


def _write_crash_log(exc: BaseException) -> Path | None:
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        if getattr(sys, "frozen", False):
            d = Path(sys.executable).resolve().parent
        else:
            d = Path(__file__).resolve().parent.parent
        path = d / f"crash_{ts}.log"
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"Time:      {datetime.now().isoformat(timespec='seconds')}\n")
            f.write(f"Frozen:    {getattr(sys, 'frozen', False)}\n")
            f.write(f"Executable:{sys.executable}\n")
            f.write(f"Argv:      {sys.argv}\n\n")
            f.write(traceback.format_exc())
        return path
    except Exception:
        return None


def main() -> int:
    # 1) 先建 QApplication —— 任何 QWidget / QFont / Fluent 主题都必须在此之后
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt
    app = QApplication.instance() or QApplication(sys.argv)
    # 高 DPI 适配（PySide6 默认已开启，这里显式设一下确保）
    try:
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception:
        pass

    # 2) 应用 Fluent 主题（必须在 QApplication 之后、MainWindow 之前）
    try:
        from qfluentwidgets import Theme, setTheme, setThemeColor
        setTheme(Theme.LIGHT)
        setThemeColor("#0078D4")
    except Exception as e:
        print(f"[warn] 应用主题失败: {e}", file=sys.stderr)

    # 3) 在 QApplication 已就绪后才 import 并实例化 MainWindow
    try:
        from .main_window import MainWindow
        window = MainWindow()
        window.show()
        return app.exec()
    except (SystemExit, KeyboardInterrupt):
        raise
    except BaseException as exc:
        log_path = _write_crash_log(exc)
        try:
            from PySide6.QtWidgets import QMessageBox
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
    sys.exit(main())
