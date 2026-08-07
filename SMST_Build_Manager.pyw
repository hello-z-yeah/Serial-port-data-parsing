"""Tkinter GUI entry point that does not depend on .bat/.cmd execution."""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import tkinter as tk
    from tkinter import messagebox, scrolledtext, ttk
except Exception as exc:  # pragma: no cover - Windows installation issue
    raise SystemExit(f"无法加载 Python tkinter：{exc}")

from build_tools.build_manager import BuildRunner, PYTHON_EXECUTABLE, TeeLogger  # noqa: E402


class BuildManagerWindow(tk.Tk):
    ACTIONS = (
        ("环境检查", "diagnose"),
        ("安装依赖", "install-deps"),
        ("运行自动测试", "test"),
        ("运行源码", "start"),
        ("构建文件夹版 EXE", "build-exe"),
        ("构建单文件便携版", "build-portable"),
        ("构建 Windows 安装包", "build-installer"),
    )

    def __init__(self) -> None:
        super().__init__()
        self.title("Super Max Serial Tool 3.1.0 - 构建管理器")
        self.geometry("920x680")
        self.minsize(780, 560)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._cancel_event = threading.Event()
        self._runner: BuildRunner | None = None
        self._logger: TeeLogger | None = None
        self._buttons: list[ttk.Button] = []

        self._build_ui()
        self.after(80, self._drain_messages)
        self.after(300, lambda: self._start_action("diagnose"))

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)

        title = ttk.Label(outer, text="Super Max Serial Tool 构建管理器", font=("Microsoft YaHei UI", 16, "bold"))
        title.pack(anchor=tk.W)
        ttk.Label(
            outer,
            text="此工具完全绕过 .bat/.cmd，可直接完成环境检查、依赖安装、EXE 和安装包构建。",
        ).pack(anchor=tk.W, pady=(4, 10))

        info = ttk.Frame(outer)
        info.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(info, text=f"Python：{PYTHON_EXECUTABLE}").pack(anchor=tk.W)
        ttk.Label(info, text=f"项目目录：{ROOT}").pack(anchor=tk.W)

        buttons = ttk.Frame(outer)
        buttons.pack(fill=tk.X, pady=(0, 8))
        for index, (label, action) in enumerate(self.ACTIONS):
            button = ttk.Button(buttons, text=label, command=lambda a=action: self._start_action(a))
            button.grid(row=index // 3, column=index % 3, padx=(0, 8), pady=(0, 7), sticky="ew")
            self._buttons.append(button)
        for column in range(3):
            buttons.columnconfigure(column, weight=1)

        utility = ttk.Frame(outer)
        utility.pack(fill=tk.X, pady=(0, 8))
        self.cancel_button = ttk.Button(utility, text="停止当前任务", command=self._cancel, state=tk.DISABLED)
        self.cancel_button.pack(side=tk.LEFT)
        ttk.Button(utility, text="打开构建日志目录", command=lambda: self._open_path(ROOT / "build_logs")).pack(side=tk.LEFT, padx=8)
        ttk.Button(utility, text="打开输出目录", command=lambda: self._open_path(ROOT / "release")).pack(side=tk.LEFT)

        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(outer, textvariable=self.status_var).pack(anchor=tk.W, pady=(0, 5))

        self.log_text = scrolledtext.ScrolledText(
            outer,
            wrap=tk.WORD,
            font=("Consolas", 10),
            state=tk.DISABLED,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def _append_log(self, text: str) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, text + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _start_action(self, action: str) -> None:
        if self._worker is not None and self._worker.is_alive():
            messagebox.showinfo("任务正在运行", "请等待当前任务完成，或先点击“停止当前任务”。")
            return

        if action == "build-installer":
            confirmed = messagebox.askyesno(
                "构建安装包",
                "该操作会先运行语法检查、全部自动测试和 PyInstaller 构建，可能需要较长时间。\n\n继续吗？",
            )
            if not confirmed:
                return

        self._cancel_event = threading.Event()
        self._set_busy(True)
        self.status_var.set(f"正在执行：{action}")
        self._append_log("\n" + "=" * 76)
        self._append_log(f"开始操作：{action}")

        self._worker = threading.Thread(target=self._worker_main, args=(action,), daemon=True)
        self._worker.start()

    def _worker_main(self, action: str) -> None:
        logger = TeeLogger(lambda line: self._messages.put(("log", line)))
        runner = BuildRunner(logger, self._cancel_event)
        self._logger = logger
        self._runner = runner
        code = 1
        try:
            runner.log(f"Super Max Serial Tool 构建工具 - 操作：{action}")
            runner.log(f"当前 Python：{PYTHON_EXECUTABLE}")
            runner.log(f"项目目录：{ROOT}")
            if action == "diagnose":
                runner.diagnose()
            elif action == "install-deps":
                runner.install_dependencies()
            elif action == "test":
                runner.run_checks()
            elif action == "start":
                runner.start_source()
            elif action == "build-exe":
                runner.build_app(install_if_missing=True)
            elif action == "build-portable":
                runner.build_portable(install_if_missing=True)
            elif action == "build-installer":
                runner.build_installer(install_if_missing=True)
            else:
                raise RuntimeError(f"未知操作：{action}")
            runner.log("\n操作完成。")
            code = 0
        except Exception as exc:
            import traceback

            runner.log("\n操作失败：")
            for line in traceback.format_exception(type(exc), exc, exc.__traceback__):
                for subline in line.rstrip().splitlines():
                    runner.log(subline)
            code = 2 if self._cancel_event.is_set() else 1
        finally:
            log_path = logger.path
            logger.close()
            self._runner = None
            self._logger = None
            self._messages.put(("done", (action, code, log_path)))

    def _cancel(self) -> None:
        self._cancel_event.set()
        runner = self._runner
        if runner is not None:
            runner.cancel()
        self.status_var.set("正在停止……")

    def _set_busy(self, busy: bool) -> None:
        state = tk.DISABLED if busy else tk.NORMAL
        for button in self._buttons:
            button.configure(state=state)
        self.cancel_button.configure(state=tk.NORMAL if busy else tk.DISABLED)

    def _drain_messages(self) -> None:
        try:
            while True:
                kind, payload = self._messages.get_nowait()
                if kind == "log":
                    self._append_log(str(payload))
                elif kind == "done":
                    action, code, log_path = payload  # type: ignore[misc]
                    self._set_busy(False)
                    if code == 0:
                        self.status_var.set(f"完成：{action}")
                        messagebox.showinfo("操作完成", f"操作已成功完成。\n\n日志：{log_path}")
                    elif code == 2:
                        self.status_var.set("任务已取消")
                        messagebox.showwarning("已取消", f"任务已停止。\n\n日志：{log_path}")
                    else:
                        self.status_var.set(f"失败：{action}")
                        messagebox.showerror("操作失败", f"请查看窗口底部日志。\n\n完整日志：{log_path}")
        except queue.Empty:
            pass
        self.after(80, self._drain_messages)

    @staticmethod
    def _open_path(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        try:
            if os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            messagebox.showerror("无法打开目录", str(exc))

    def _on_close(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            if not messagebox.askyesno("任务正在运行", "关闭窗口会停止当前任务，是否继续？"):
                return
            self._cancel()
        self.destroy()


if __name__ == "__main__":
    BuildManagerWindow().mainloop()
