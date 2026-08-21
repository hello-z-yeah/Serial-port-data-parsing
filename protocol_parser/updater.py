"""SerialX 在线更新模块(基于 GitHub Releases)。

独立模块, 便于整体移除: 删除本文件, 并删除 gui.py 中
_update_feature 相关代码即可, 不影响程序其他功能。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from enum import Enum
from pathlib import Path
from urllib.parse import unquote

import requests
from PySide6.QtCore import QObject, Signal

from protocol_parser.app_info import APP_VERSION
from protocol_parser.paths import resource_path

_log = logging.getLogger(__name__)

# ---- 发布仓库配置 ----
GITHUB_OWNER = "hello-z-yeah"
GITHUB_REPO = "Serial-port-data-parsing"
CHECK_URL = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
)
# 备用静态版本信息源。开发环境只能通过显式环境变量覆盖，避免本机路径进入发布包。
DEFAULT_FALLBACK_VERSION_URL = (
    f"https://{GITHUB_OWNER}.github.io/{GITHUB_REPO}/version.json"
)
MAX_INSTALLER_BYTES = 1024 * 1024 * 1024
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_CHECK_BACKOFF_BASE_SEC = 300.0
_CHECK_BACKOFF_MAX_SEC = 6 * 3600.0
_UPDATE_NOTES_MAX_LINES = 10
_UPDATE_NOTES_MAX_CHARS = 600


def format_update_prompt_message(
    version: str,
    notes: str,
    *,
    max_lines: int = _UPDATE_NOTES_MAX_LINES,
    max_chars: int = _UPDATE_NOTES_MAX_CHARS,
) -> str:
    """Keep native update prompts readable when GitHub release notes are very long."""
    body = str(notes or "").strip()
    if body:
        lines = body.splitlines()
        if len(lines) > max_lines:
            body = "\n".join(lines[:max_lines]) + "\n…"
        if len(body) > max_chars:
            body = body[: max(1, max_chars - 1)].rstrip() + "…"
    else:
        body = "（无发布说明）"
    tag = str(version or "").strip() or "?"
    return f"发现新版本 {tag}\n\n{body}\n\n是否立即下载并更新?"


def _resolve_fallback_url() -> str:
    return (
        os.environ.get("SERIALX_UPDATE_FALLBACK_URL", "").strip()
        or DEFAULT_FALLBACK_VERSION_URL
    )


FALLBACK_VERSION_URL = _resolve_fallback_url()


def _bundled_version_json_paths() -> list[Path]:
    """Shipped updater metadata used when GitHub/Pages are unreachable."""
    candidates = (
        resource_path("resources/version.json"),
        Path(__file__).resolve().parents[1] / "version.json",
    )
    seen: set[str] = set()
    paths: list[Path] = []
    for candidate in candidates:
        key = str(candidate.resolve()) if candidate.exists() else str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_file():
            paths.append(candidate)
    return paths


def _iter_fallback_sources() -> list[str]:
    """Return fallback metadata sources in priority order."""
    sources: list[str] = []
    env_url = os.environ.get("SERIALX_UPDATE_FALLBACK_URL", "").strip()
    if env_url:
        sources.append(env_url)
    if DEFAULT_FALLBACK_VERSION_URL not in sources:
        sources.append(DEFAULT_FALLBACK_VERSION_URL)
    for bundled in _bundled_version_json_paths():
        text = str(bundled)
        if text not in sources:
            sources.append(text)
    return sources


class UpdatePhase(str, Enum):
    IDLE = "idle"
    CHECKING = "checking"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    LAUNCHING = "launching"


class _DownloadCancelled(RuntimeError):
    pass


class Updater(QObject):
    """检查 GitHub Releases 最新版本并下载安装包。"""

    check_finished = Signal(bool, dict)      # (是否发现新版本, 发布信息)
    download_progress = Signal(int, int)     # (已下载字节, 总字节)
    download_finished = Signal(bool, str)    # (是否成功, 提示文本)
    phase_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._info: dict = {}
        self._target = Path(tempfile.gettempdir()) / "SerialX_setup_latest.exe"
        self._phase = UpdatePhase.IDLE
        self._state_lock = threading.RLock()
        self._check_thread: threading.Thread | None = None
        self._cancel_event: threading.Event | None = None
        self._download_thread: threading.Thread | None = None
        self._check_backoff_until = 0.0
        self._consecutive_check_failures = 0

    def can_check_now(self, *, manual: bool = False) -> bool:
        with self._state_lock:
            if self._phase is not UpdatePhase.IDLE:
                return False
            if manual:
                return True
            return time.monotonic() >= self._check_backoff_until

    def seconds_until_next_check(self) -> float:
        with self._state_lock:
            return max(0.0, self._check_backoff_until - time.monotonic())

    def _record_check_outcome(self, *, failed: bool) -> None:
        with self._state_lock:
            if failed:
                self._consecutive_check_failures += 1
                exponent = min(max(self._consecutive_check_failures - 1, 0), 4)
                delay = min(
                    _CHECK_BACKOFF_MAX_SEC,
                    _CHECK_BACKOFF_BASE_SEC * (2**exponent),
                )
                self._check_backoff_until = time.monotonic() + delay
            else:
                self._consecutive_check_failures = 0
                self._check_backoff_until = 0.0

    @staticmethod
    def _parse_version(tag: str) -> tuple:
        tag = str(tag or "").lstrip("vV").strip()
        parts = []
        for seg in tag.split("."):
            try:
                parts.append(int(seg))
            except ValueError:
                break
        return tuple(parts)

    @staticmethod
    def _fetch_fallback(url: str) -> dict:
        """读取备用版本信息源, 支持 http/https 与本地文件路径/file://。"""
        text = str(url or "")
        if text.startswith("http://") and os.environ.get(
            "SERIALX_ALLOW_INSECURE_UPDATE_URL", ""
        ).strip() != "1":
            raise RuntimeError("更新元数据地址必须使用 HTTPS")
        if text.startswith(("http://", "https://")):
            resp = requests.get(text, timeout=10)
            raw = resp.content
            if resp.status_code != 200:
                raise RuntimeError(f"备用源返回 {resp.status_code}")
        else:
            if text.startswith("file:///"):
                path_text = unquote(text[8:])
            elif text.startswith("file://"):
                path_text = unquote(text[7:])
            else:
                path_text = text
            raw = Path(path_text).read_bytes()
        return json.loads(raw)

    @staticmethod
    def _fetch_fallback_from_sources(sources: list[str]) -> dict:
        errors: list[str] = []
        for source in sources:
            try:
                return Updater._fetch_fallback(source)
            except Exception as exc:
                errors.append(f"{source}: {exc}")
        raise RuntimeError("; ".join(errors))

    @classmethod
    def _check_via_fallback_sources(cls) -> tuple[bool, dict]:
        data = cls._fetch_fallback_from_sources(_iter_fallback_sources())
        info = cls._fallback_release_info(data)
        tag = info.get("tag_name", "")
        newest = cls._parse_version(tag)
        current = cls._parse_version(APP_VERSION)
        has_new = bool(newest and newest > current)
        _log.info(
            "检查更新(备用): 本地=%s, 远程=%s(%s), has_new=%s",
            APP_VERSION,
            tag,
            newest,
            has_new,
        )
        return has_new, info

    @property
    def phase(self) -> UpdatePhase:
        with self._state_lock:
            return self._phase

    def is_busy(self) -> bool:
        return self.phase is not UpdatePhase.IDLE

    def is_checking(self) -> bool:
        return self.phase is UpdatePhase.CHECKING

    def _set_phase(self, phase: UpdatePhase) -> None:
        changed = False
        with self._state_lock:
            if self._phase is not phase:
                self._phase = phase
                changed = True
        if changed:
            self.phase_changed.emit(phase.value)

    @staticmethod
    def _fallback_release_info(data: dict) -> dict:
        tag = data.get("tag_name") or data.get("version", "")
        sha256 = str(data.get("sha256") or "").strip().lower()
        asset = {
            "name": data.get("asset_name") or "SerialXSetup.exe",
            "browser_download_url": data.get("download_url", ""),
        }
        if _SHA256_RE.fullmatch(sha256):
            asset["sha256"] = sha256
            asset["digest"] = f"sha256:{sha256}"
        return {
            "tag_name": tag,
            "body": data.get("notes", ""),
            "sha256": sha256,
            "assets": [asset],
        }

    @staticmethod
    def _expected_sha256(info: dict, asset: dict) -> str:
        for candidate in (
            asset.get("sha256"),
            asset.get("digest"),
            info.get("sha256"),
        ):
            text = str(candidate or "").strip()
            if text.lower().startswith("sha256:"):
                text = text.split(":", 1)[1].strip()
            if _SHA256_RE.fullmatch(text):
                return text.lower()
        return ""

    @classmethod
    def _enrich_release_integrity(cls, info: dict) -> dict:
        """Fill a missing GitHub digest from matching HTTPS fallback metadata."""
        asset = cls._find_installer_asset(info)
        if asset is None or cls._expected_sha256(info, asset):
            return info
        try:
            fallback = cls._fallback_release_info(
                cls._fetch_fallback_from_sources(_iter_fallback_sources())
            )
            if cls._parse_version(fallback.get("tag_name", "")) != cls._parse_version(
                info.get("tag_name", "")
            ):
                return info
            fallback_asset = cls._find_installer_asset(fallback)
            if fallback_asset is None:
                return info
            expected = cls._expected_sha256(fallback, fallback_asset)
            if expected:
                asset["sha256"] = expected
                asset["digest"] = f"sha256:{expected}"
                info["sha256"] = expected
        except Exception as exc:
            _log.warning("无法从备用源补充安装包摘要: %s", exc)
        return info

    def check_update(self, *, manual: bool = False) -> bool:
        """启动一次后台检查；并发调用会被合并，静默检查失败会退避。"""
        if not self.can_check_now(manual=manual):
            if manual:
                _log.info("更新器正处于忙碌或退避期，忽略手动检查")
            else:
                remaining = self.seconds_until_next_check()
                _log.info("静默更新检查仍在退避期，剩余 %.0f 秒", remaining)
            return False
        with self._state_lock:
            if self._phase is not UpdatePhase.IDLE:
                _log.info("更新器正处于 %s，忽略重复检查", self._phase.value)
                return False
            self._phase = UpdatePhase.CHECKING

        def _run():
            errors = []
            result: tuple[bool, dict] | None = None
            failed = False

            try:
                # 1) 尝试 GitHub API 主源。
                try:
                    headers = {
                        "Accept": "application/vnd.github+json",
                        "User-Agent": "SerialX-Updater",
                    }
                    resp = requests.get(CHECK_URL, headers=headers, timeout=10)
                    _log.info(
                        "检查更新响应: http_status=%s, bytes=%d",
                        resp.status_code,
                        len(resp.content),
                    )
                    if resp.status_code == 200:
                        info = resp.json()
                        tag = info.get("tag_name", "")
                        newest = self._parse_version(tag)
                        current = self._parse_version(APP_VERSION)
                        has_new = bool(newest and newest > current)
                        if has_new:
                            info = self._enrich_release_integrity(info)
                        _log.info(
                            "检查更新: 本地=%s, 远程=%s(%s), has_new=%s",
                            APP_VERSION,
                            tag,
                            newest,
                            has_new,
                        )
                        result = (has_new, info)
                    else:
                        errors.append(f"GitHub API 返回 {resp.status_code}")
                except Exception as exc:
                    errors.append(f"GitHub API 请求失败: {exc}")

                # 2) 主源失败，尝试 Pages / 环境变量 / 内置 version.json。
                if result is None:
                    try:
                        result = self._check_via_fallback_sources()
                    except Exception as exc:
                        errors.append(f"备用源请求失败: {exc}")

                if result is None:
                    err = "检查更新失败: " + "; ".join(errors)
                    _log.warning(err)
                    result = (False, {"__error": err})
                    failed = True
            except Exception as exc:
                _log.exception("检查更新线程异常")
                result = (False, {"__error": f"检查更新失败: {exc}"})
                failed = True
            finally:
                self._record_check_outcome(failed=failed)
                with self._state_lock:
                    if threading.current_thread() is self._check_thread:
                        self._check_thread = None
                    self._phase = UpdatePhase.IDLE
                self.phase_changed.emit(UpdatePhase.IDLE.value)
                self.check_finished.emit(*result)

        thread = threading.Thread(
            target=_run,
            daemon=True,
            name="SerialXUpdateCheck",
        )
        with self._state_lock:
            self._check_thread = thread
        self.phase_changed.emit(UpdatePhase.CHECKING.value)
        try:
            thread.start()
        except Exception:
            with self._state_lock:
                self._check_thread = None
                self._phase = UpdatePhase.IDLE
            self.phase_changed.emit(UpdatePhase.IDLE.value)
            raise
        return True

    @staticmethod
    def _validate_download_url(url: str) -> None:
        if str(url or "").lower().startswith("https://"):
            return
        if os.environ.get("SERIALX_ALLOW_INSECURE_UPDATE_URL", "").strip() == "1":
            return
        raise RuntimeError("安装包下载地址必须使用 HTTPS")

    def download_and_install(self, info: dict) -> bool:
        """流式下载安装包，校验 SHA-256 后再启动安装器。"""
        asset = self._find_installer_asset(info)
        if asset is None:
            self.download_finished.emit(False, "发布中没有找到安装包")
            return False
        url = str(asset.get("browser_download_url") or "").strip()
        try:
            self._validate_download_url(url)
        except RuntimeError as exc:
            self.download_finished.emit(False, str(exc))
            return False
        expected_sha256 = self._expected_sha256(info, asset)
        if not expected_sha256:
            self.download_finished.emit(
                False,
                "发布信息缺少安装包 SHA-256 摘要，已拒绝执行不受校验的更新。",
            )
            return False

        with self._state_lock:
            if self._phase is not UpdatePhase.IDLE:
                _log.warning("更新器正处于 %s，忽略重复下载", self._phase.value)
                return False
            self._info = dict(info)
            self._cancel_event = threading.Event()
            self._phase = UpdatePhase.DOWNLOADING

        def _run():
            partial = self._target.with_suffix(self._target.suffix + ".part")
            ok = False
            message = ""
            try:
                partial.unlink(missing_ok=True)
                received = 0
                hasher = hashlib.sha256()
                with requests.get(url, stream=True, timeout=30) as resp:
                    resp.raise_for_status()
                    total = int(resp.headers.get("Content-Length", 0))
                    if total > MAX_INSTALLER_BYTES:
                        raise RuntimeError("安装包大小超过 1 GiB 安全上限")
                    _log.info("开始下载更新: url=%s, total=%d", url, total)
                    with partial.open("wb") as output:
                        for chunk in resp.iter_content(chunk_size=65536):
                            cancel_event = self._cancel_event
                            if cancel_event is not None and cancel_event.is_set():
                                raise _DownloadCancelled("下载已取消")
                            if not chunk:
                                continue
                            received += len(chunk)
                            if received > MAX_INSTALLER_BYTES:
                                raise RuntimeError("安装包大小超过 1 GiB 安全上限")
                            output.write(chunk)
                            hasher.update(chunk)
                            self.download_progress.emit(received, total)
                        output.flush()
                        os.fsync(output.fileno())

                if total > 0 and received != total:
                    raise RuntimeError(
                        f"安装包下载不完整：预期 {total} 字节，实际 {received} 字节"
                    )
                self._set_phase(UpdatePhase.VERIFYING)
                actual_sha256 = hasher.hexdigest()
                if actual_sha256 != expected_sha256:
                    raise RuntimeError("安装包 SHA-256 校验失败，文件可能损坏或被篡改")
                cancel_event = self._cancel_event
                if cancel_event is not None and cancel_event.is_set():
                    raise _DownloadCancelled("下载已取消")
                os.replace(partial, self._target)
                _log.info(
                    "下载与校验完成: received=%d, sha256=%s",
                    received,
                    actual_sha256,
                )
                self._set_phase(UpdatePhase.LAUNCHING)
                subprocess.Popen([str(self._target)], shell=False)
                ok = True
                message = "更新程序已启动, 请按向导完成安装"
            except _DownloadCancelled:
                _log.info("下载已取消")
                message = "下载已取消"
            except Exception as exc:
                _log.exception("下载更新失败")
                message = f"下载失败: {exc}"
            finally:
                try:
                    partial.unlink(missing_ok=True)
                except OSError:
                    pass
                with self._state_lock:
                    self._cancel_event = None
                    if threading.current_thread() is self._download_thread:
                        self._download_thread = None
                    self._phase = UpdatePhase.IDLE
                self.phase_changed.emit(UpdatePhase.IDLE.value)
                self.download_finished.emit(ok, message)

        thread = threading.Thread(
            target=_run,
            daemon=True,
            name="SerialXUpdateDownload",
        )
        with self._state_lock:
            self._download_thread = thread
        self.phase_changed.emit(UpdatePhase.DOWNLOADING.value)
        try:
            thread.start()
        except Exception:
            with self._state_lock:
                self._download_thread = None
                self._cancel_event = None
                self._phase = UpdatePhase.IDLE
            self.phase_changed.emit(UpdatePhase.IDLE.value)
            raise
        return True

    def cancel_download(self) -> None:
        """请求取消当前下载任务。"""
        with self._state_lock:
            cancel_event = self._cancel_event
        if cancel_event is not None:
            _log.info("用户取消下载")
            cancel_event.set()

    @staticmethod
    def _find_installer_asset(info: dict) -> dict | None:
        for asset in info.get("assets", []) or []:
            name = str(asset.get("name") or "")
            if name.lower().endswith(".exe") and "setup" in name.lower():
                return asset
        return None
