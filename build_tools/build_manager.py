"""Windows build orchestration for SST_串口工具.

This module intentionally avoids cmd.exe batch parsing.  It can be invoked from
``SST_Build_Manager.py`` (console) or ``SST_Build_Manager.pyw`` (GUI).
"""
from __future__ import annotations

import argparse
import compileall
import importlib.util
import os
import platform
import queue
import shutil
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from protocol_parser.app_info import (
    APP_EXE_BASENAME,
    APP_EXE_NAME,
    APP_NAME,
    APP_VERSION,
)
from typing import Callable, Iterable, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUILD_TOOLS_DIR = PROJECT_ROOT / "build_tools"
LOG_DIR = PROJECT_ROOT / "build_logs"
DIST_DIR = PROJECT_ROOT / "dist"
RELEASE_DIR = PROJECT_ROOT / "release"
SPEC_FILE = PROJECT_ROOT / "serial_port_parser_fast.spec"
ISS_FILE = PROJECT_ROOT / "installer" / "serial_port_parser.iss"
EXPECTED_APP_EXE = DIST_DIR / APP_EXE_BASENAME / APP_EXE_NAME
EXPECTED_PORTABLE_EXE = DIST_DIR / f"{APP_EXE_BASENAME}_Portable.exe"
EXPECTED_INSTALLER = RELEASE_DIR / f"{APP_EXE_BASENAME}_Setup_{APP_VERSION}_x64.exe"
REQUIREMENTS_FILE = PROJECT_ROOT / "requirements.txt"

SUPPORTED_MIN = (3, 10)
SUPPORTED_MAX_EXCLUSIVE = (3, 15)

INNO_APP_ID_GUID = "B1F3A7D8-6C9E-4F2B-9A8C-7D5E3F1A2B4C"
INNO_APP_ID_DEFINE = f'#define MyAppAssistedGUID  "{{{{{INNO_APP_ID_GUID}}}"'
INNO_APP_ID_ASSIGNMENT = "AppId={#MyAppAssistedGUID}"
INNO_OPTIONAL_LANGUAGE_INCLUDE = r"compiler:Languages\ChineseSimplified.isl"


def validate_inno_setup_script(path: Path = ISS_FILE) -> None:
    """Fail early when the installer AppId is not escaped for Inno Setup.

    Inno Setup treats a single opening brace as the start of a built-in
    constant. A literal GUID therefore has to reach the compiler as
    ``{{GUID}``. Keeping this check in the Python build manager avoids doing a
    full PyInstaller build before discovering a malformed installer script.
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise BuildManagerError(f"无法读取 Inno Setup 脚本：{path}：{exc}") from exc

    missing: list[str] = []
    if INNO_APP_ID_DEFINE not in text:
        missing.append(INNO_APP_ID_DEFINE)
    if INNO_APP_ID_ASSIGNMENT not in text:
        missing.append(INNO_APP_ID_ASSIGNMENT)
    if missing:
        expected = "\n".join(missing)
        raise BuildManagerError(
            "Inno Setup 的 AppId 配置无效。GUID 左花括号必须写成双花括号，"
            "否则会报 Unknown constant。期望配置：\n" + expected
        )

    if INNO_OPTIONAL_LANGUAGE_INCLUDE.casefold() in text.casefold():
        raise BuildManagerError(
            "Inno Setup 脚本引用了可选语言文件 ChineseSimplified.isl。"
            "该文件并非所有 Inno Setup 安装都自带，会导致 Couldn't open include file。"
            "请使用内置默认语言或把语言文件随项目一起提供。"
        )


def _resolve_console_python() -> str:
    """Return python.exe when the GUI was launched through pythonw.exe."""
    executable = Path(sys.executable).resolve()
    if executable.name.casefold() == "pythonw.exe":
        console = executable.with_name("python.exe")
        if console.is_file():
            return str(console)
    return str(executable)


PYTHON_EXECUTABLE = _resolve_console_python()

LogCallback = Callable[[str], None]


class BuildManagerError(RuntimeError):
    """Expected build failure that should be shown as an actionable message."""


class BuildCancelled(BuildManagerError):
    """Raised when the user cancels the active operation."""


@dataclass(frozen=True)
class EnvironmentReport:
    python_executable: str
    python_version: str
    architecture_bits: int
    project_root: str
    pip_version: str
    missing_dependencies: tuple[str, ...]
    inno_setup: str | None


class TeeLogger:
    """Write lines to a timestamped UTF-8 log and optionally to a callback."""

    def __init__(self, callback: LogCallback | None = None) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = LOG_DIR / f"build_{stamp}.log"
        self._stream = self.path.open("w", encoding="utf-8", newline="\n")
        self._callback = callback
        self._lock = threading.Lock()

    def write(self, text: object = "") -> None:
        line = str(text).rstrip("\r\n")
        with self._lock:
            self._stream.write(line + "\n")
            self._stream.flush()
        if self._callback is not None:
            self._callback(line)

    def close(self) -> None:
        with self._lock:
            if not self._stream.closed:
                self._stream.flush()
                self._stream.close()


class BuildRunner:
    def __init__(
        self,
        logger: TeeLogger,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self.logger = logger
        self.cancel_event = cancel_event or threading.Event()
        self._process_lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None

    def log(self, text: object = "") -> None:
        self.logger.write(text)

    def cancel(self) -> None:
        self.cancel_event.set()
        with self._process_lock:
            process = self._process
        if process is not None and process.poll() is None:
            self.log("正在停止当前子进程……")
            try:
                process.terminate()
                process.wait(timeout=5)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass

    def _check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise BuildCancelled("任务已取消。")

    def run_command(
        self,
        command: Sequence[str | os.PathLike[str]],
        *,
        cwd: Path = PROJECT_ROOT,
        env: dict[str, str] | None = None,
        description: str | None = None,
    ) -> None:
        self._check_cancelled()
        cmd = [str(part) for part in command]
        if description:
            self.log(f"\n=== {description} ===")
        self.log("执行：" + subprocess.list2cmdline(cmd))

        process_env = os.environ.copy()
        process_env.update(
            {
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
                "PYTHONPATH": str(PROJECT_ROOT),
            }
        )
        if env:
            process_env.update(env)

        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        try:
            process = subprocess.Popen(
                cmd,
                cwd=str(cwd),
                env=process_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
        except OSError as exc:
            raise BuildManagerError(f"无法启动命令：{exc}") from exc

        with self._process_lock:
            self._process = process
        try:
            assert process.stdout is not None
            while True:
                self._check_cancelled()
                line = process.stdout.readline()
                if line:
                    self.log(line.rstrip("\r\n"))
                    continue
                if process.poll() is not None:
                    break
                time.sleep(0.03)
            return_code = process.wait()
        finally:
            with self._process_lock:
                self._process = None

        if return_code != 0:
            raise BuildManagerError(
                f"命令执行失败，退出代码 {return_code}：{subprocess.list2cmdline(cmd)}"
            )

    def check_python(self) -> None:
        version = sys.version_info[:2]
        bits = struct.calcsize("P") * 8
        if not (SUPPORTED_MIN <= version < SUPPORTED_MAX_EXCLUSIVE):
            raise BuildManagerError(
                "需要 Python 3.11、3.12、3.13 或 3.14；"
                f"当前是 Python {platform.python_version()}。"
            )
        if bits != 64:
            raise BuildManagerError(f"需要 64 位 Python；当前为 {bits} 位。")

    @staticmethod
    def dependency_status() -> tuple[str, ...]:
        modules = {
            "PySide6": "PySide6",
            "qfluentwidgets": "PySide6-Fluent-Widgets",
            "serial": "pyserial",
            "docx": "python-docx",
            "PyInstaller": "PyInstaller",
            "pytest": "pytest",
        }
        missing: list[str] = []
        for module_name, package_name in modules.items():
            if importlib.util.find_spec(module_name) is None:
                missing.append(package_name)
        return tuple(missing)

    def find_inno_setup(self) -> Path | None:
        override = os.environ.get("ISCC_OVERRIDE", "").strip().strip('"')
        candidates: list[Path] = []
        if override:
            candidates.append(Path(override))
        for env_name in ("ProgramFiles(x86)", "ProgramFiles"):
            base = os.environ.get(env_name)
            if base:
                candidates.append(Path(base) / "Inno Setup 6" / "ISCC.exe")
        found = shutil.which("ISCC.exe") or shutil.which("iscc")
        if found:
            candidates.append(Path(found))

        if os.name == "nt":
            try:
                import winreg

                registry_locations = (
                    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1"),
                    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1"),
                    (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1"),
                )
                for hive, key_name in registry_locations:
                    try:
                        with winreg.OpenKey(hive, key_name) as key:
                            install_location, _ = winreg.QueryValueEx(key, "InstallLocation")
                        if install_location:
                            candidates.append(Path(install_location) / "ISCC.exe")
                    except OSError:
                        continue
            except ImportError:
                pass

        seen: set[str] = set()
        for candidate in candidates:
            normalized = str(candidate).casefold()
            if normalized in seen:
                continue
            seen.add(normalized)
            if candidate.is_file():
                return candidate.resolve()
        return None

    def environment_report(self) -> EnvironmentReport:
        self.check_python()
        pip_version = "不可用"
        try:
            completed = subprocess.run(
                [PYTHON_EXECUTABLE, "-m", "pip", "--version"],
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            if completed.returncode == 0:
                pip_version = completed.stdout.strip()
        except Exception as exc:
            pip_version = f"检测失败：{exc}"

        inno = self.find_inno_setup()
        return EnvironmentReport(
            python_executable=PYTHON_EXECUTABLE,
            python_version=platform.python_version(),
            architecture_bits=struct.calcsize("P") * 8,
            project_root=str(PROJECT_ROOT),
            pip_version=pip_version,
            missing_dependencies=self.dependency_status(),
            inno_setup=str(inno) if inno else None,
        )

    def diagnose(self) -> EnvironmentReport:
        report = self.environment_report()
        self.log(f"{APP_NAME} 构建环境检查")
        self.log(f"项目目录：{report.project_root}")
        self.log(f"Python：{report.python_executable}")
        self.log(f"版本：{report.python_version}")
        self.log(f"位数：{report.architecture_bits} bit")
        self.log(f"pip：{report.pip_version}")
        if report.missing_dependencies:
            self.log("缺少依赖：" + "、".join(report.missing_dependencies))
        else:
            self.log("构建依赖：已安装")
        if report.inno_setup:
            self.log(f"Inno Setup：{report.inno_setup}")
        else:
            self.log("Inno Setup：未找到（仅影响安装包构建）")
        self.log(f"日志：{self.logger.path}")
        return report

    def install_dependencies(self) -> None:
        self.check_python()
        if not REQUIREMENTS_FILE.is_file():
            raise BuildManagerError(f"缺少依赖文件：{REQUIREMENTS_FILE}")
        self.run_command(
            [PYTHON_EXECUTABLE, "-m", "pip", "install", "--disable-pip-version-check", "--upgrade", "pip", "setuptools", "wheel"],
            description="升级 pip/setuptools/wheel",
        )
        try:
            self.run_command(
                [PYTHON_EXECUTABLE, "-m", "pip", "install", "--disable-pip-version-check", "-r", str(REQUIREMENTS_FILE)],
                description="安装项目依赖",
            )
        except BuildManagerError:
            self.log("默认软件源失败，使用官方 PyPI 重试。")
            self.run_command(
                [PYTHON_EXECUTABLE, "-m", "pip", "install", "--disable-pip-version-check", "--index-url", "https://pypi.org/simple", "-r", str(REQUIREMENTS_FILE)],
                description="使用官方 PyPI 安装项目依赖",
            )

    def verify_dependencies(self, *, install_if_missing: bool = False) -> None:
        missing = self.dependency_status()
        if not missing:
            return
        if install_if_missing:
            self.log("发现缺少依赖：" + "、".join(missing))
            self.install_dependencies()
            missing = self.dependency_status()
        if missing:
            raise BuildManagerError(
                "缺少构建依赖：" + "、".join(missing) + "。请先点击“安装依赖”。"
            )

    def run_checks(self) -> None:
        self.check_python()
        self.verify_dependencies()
        self.log("\n=== Python 源码语法检查 ===")
        ok = compileall.compile_dir(
            str(PROJECT_ROOT / "protocol_parser"),
            quiet=1,
            force=True,
        )
        ok = compileall.compile_file(str(PROJECT_ROOT / "exe_entry.py"), quiet=1, force=True) and ok
        if not ok:
            raise BuildManagerError("Python 源码语法检查失败。")
        self.run_command(
            [PYTHON_EXECUTABLE, "-m", "pytest", "-q"],
            description="自动测试",
        )

    def clean_build_output(self) -> None:
        self.log("\n=== 清理旧构建目录 ===")
        for path in (PROJECT_ROOT / "build", DIST_DIR / APP_EXE_BASENAME):
            if path.exists():
                self.log(f"删除：{path}")
                shutil.rmtree(path)

    def build_app(self, *, install_if_missing: bool = False) -> Path:
        self.check_python()
        self.verify_dependencies(install_if_missing=install_if_missing)
        self.run_checks()
        if not SPEC_FILE.is_file():
            raise BuildManagerError(f"缺少 PyInstaller 配置：{SPEC_FILE}")
        self.clean_build_output()
        self.run_command(
            [PYTHON_EXECUTABLE, "-m", "PyInstaller", "--noconfirm", "--clean", str(SPEC_FILE)],
            description="构建文件夹版程序",
        )
        if not EXPECTED_APP_EXE.is_file():
            raise BuildManagerError(f"构建结束但未找到：{EXPECTED_APP_EXE}")
        self.run_command(
            [PYTHON_EXECUTABLE, str(PROJECT_ROOT / "installer" / "verify_build_identity.py")],
            description="校验产品名称和版本",
        )
        self.log(f"构建成功：{EXPECTED_APP_EXE}")
        self.log(f"请保留 dist\\{APP_EXE_BASENAME} 整个目录，不要只复制 EXE。")
        return EXPECTED_APP_EXE

    def build_portable(self, *, install_if_missing: bool = False) -> Path:
        self.check_python()
        self.verify_dependencies(install_if_missing=install_if_missing)
        self.run_checks()
        DIST_DIR.mkdir(parents=True, exist_ok=True)
        if EXPECTED_PORTABLE_EXE.exists():
            EXPECTED_PORTABLE_EXE.unlink()
        command = [
            PYTHON_EXECUTABLE,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onefile",
            "--windowed",
            "--noupx",
            "--optimize",
            "1",
            "--name",
            f"{APP_EXE_BASENAME}_Portable",
            "--hidden-import",
            "docx",
            "--hidden-import",
            "docx.opc.constants",
            "--collect-all",
            "qfluentwidgets",
            "--icon",
            str(PROJECT_ROOT / "resources" / "lkl.ico"),
            "--add-data",
            f"{PROJECT_ROOT / 'resources'}{os.pathsep}resources",
            "--add-data",
            f"{PROJECT_ROOT / 'product'}{os.pathsep}product",
            "--add-data",
            f"{PROJECT_ROOT / 'data'}{os.pathsep}defaults/data",
            str(PROJECT_ROOT / "exe_entry.py"),
        ]
        self.run_command(command, description="构建单文件便携版")
        if not EXPECTED_PORTABLE_EXE.is_file():
            raise BuildManagerError(f"构建结束但未找到：{EXPECTED_PORTABLE_EXE}")
        self.log(f"构建成功：{EXPECTED_PORTABLE_EXE}")
        return EXPECTED_PORTABLE_EXE

    def build_installer(self, *, install_if_missing: bool = False) -> Path:
        if os.name != "nt":
            raise BuildManagerError("Windows 安装包只能在 Windows 系统上构建。")
        if not ISS_FILE.is_file():
            raise BuildManagerError(f"缺少 Inno Setup 脚本：{ISS_FILE}")
        validate_inno_setup_script(ISS_FILE)
        iscc = self.find_inno_setup()
        if iscc is None:
            raise BuildManagerError(
                "未找到 Inno Setup 6 的 ISCC.exe。请安装 Inno Setup 6，"
                "或设置环境变量 ISCC_OVERRIDE 为 ISCC.exe 的完整路径。"
            )
        self.build_app(install_if_missing=install_if_missing)
        RELEASE_DIR.mkdir(parents=True, exist_ok=True)
        self.run_command([str(iscc), "/Qp", str(ISS_FILE)], description="构建 Windows 安装包")
        if not EXPECTED_INSTALLER.is_file():
            raise BuildManagerError(f"构建结束但未找到：{EXPECTED_INSTALLER}")
        self.log(f"安装包构建成功：{EXPECTED_INSTALLER}")
        return EXPECTED_INSTALLER

    def start_source(self) -> None:
        self.check_python()
        missing = self.dependency_status()
        runtime_missing = tuple(
            item for item in missing if item in {"PySide6", "PySide6-Fluent-Widgets", "pyserial", "python-docx"}
        )
        if runtime_missing:
            raise BuildManagerError(
                "缺少运行依赖：" + "、".join(runtime_missing) + "。请先安装依赖。"
            )
        self.run_command([PYTHON_EXECUTABLE, str(PROJECT_ROOT / "exe_entry.py")], description="运行源码")


def run_action(
    action: str,
    *,
    callback: LogCallback | None = None,
    cancel_event: threading.Event | None = None,
    install_if_missing: bool = False,
) -> tuple[int, Path]:
    logger = TeeLogger(callback)
    runner = BuildRunner(logger, cancel_event)
    try:
        runner.log(f"{APP_NAME} 构建工具 - 操作：{action}")
        runner.log(f"当前 Python：{PYTHON_EXECUTABLE}")
        runner.log(f"项目目录：{PROJECT_ROOT}")
        actions = {
            "diagnose": runner.diagnose,
            "install-deps": runner.install_dependencies,
            "test": runner.run_checks,
            "build-exe": lambda: runner.build_app(install_if_missing=install_if_missing),
            "build-portable": lambda: runner.build_portable(install_if_missing=install_if_missing),
            "build-installer": lambda: runner.build_installer(install_if_missing=install_if_missing),
            "start": runner.start_source,
        }
        try:
            operation = actions[action]
        except KeyError as exc:
            raise BuildManagerError(f"未知操作：{action}") from exc
        operation()
        runner.log("\n操作完成。")
        return 0, logger.path
    except BuildCancelled as exc:
        runner.log(f"\n已取消：{exc}")
        return 2, logger.path
    except BuildManagerError as exc:
        runner.log(f"\n失败：{exc}")
        return 1, logger.path
    except Exception as exc:
        import traceback

        runner.log("\n发生未预期错误：")
        for line in traceback.format_exception(type(exc), exc, exc.__traceback__):
            for subline in line.rstrip().splitlines():
                runner.log(subline)
        return 1, logger.path
    finally:
        logger.close()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} Windows build manager")
    parser.add_argument(
        "action",
        choices=("diagnose", "install-deps", "test", "build-exe", "build-portable", "build-installer", "start"),
    )
    parser.add_argument(
        "--install-missing",
        action="store_true",
        help="Build actions may install missing dependencies automatically.",
    )
    return parser


def cli_main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    code, log_path = run_action(
        args.action,
        callback=print,
        install_if_missing=args.install_missing,
    )
    print(f"\n日志文件：{log_path}")
    return code


if __name__ == "__main__":
    raise SystemExit(cli_main())
