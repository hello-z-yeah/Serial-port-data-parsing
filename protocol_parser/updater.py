"""SerialX 在线更新模块(基于 GitHub Releases)。

独立模块, 便于整体移除: 删除本文件, 并删除 gui.py 中
_update_feature 相关代码即可, 不影响程序其他功能。
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from protocol_parser.app_info import APP_VERSION

# ---- 发布仓库配置 ----
GITHUB_OWNER = "hello-z-yeah"
GITHUB_REPO = "Serial-port-data-parsing"
CHECK_URL = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
)


class Updater(QObject):
    """检查 GitHub Releases 最新版本并下载安装包。"""

    check_finished = Signal(bool, dict)      # (是否发现新版本, 发布信息)
    download_progress = Signal(int, int)     # (已下载字节, 总字节)
    download_finished = Signal(bool, str)    # (是否成功, 提示文本)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._manager = QNetworkAccessManager(self)
        self._reply: QNetworkReply | None = None
        self._info: dict = {}
        self._target = Path(tempfile.gettempdir()) / "SerialX_setup_latest.exe"

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

    def check_update(self) -> None:
        """后台请求 latest release, 不阻塞 UI。"""
        request = QNetworkRequest(QUrl(CHECK_URL))
        request.setTransferTimeout(10000)
        request.setRawHeader(b"Accept", b"application/vnd.github+json")
        request.setRawHeader(b"User-Agent", b"SerialX-Updater")
        self._reply = self._manager.get(request)
        self._reply.finished.connect(self._on_check_done)

    def _on_check_done(self) -> None:
        data = bytes(self._reply.readAll())
        if self._reply.error() != QNetworkReply.NetworkError.NoError:
            self.check_finished.emit(False, {})
            return
        try:
            info = json.loads(data.decode("utf-8"))
            newest = self._parse_version(info.get("tag_name", ""))
        except Exception:
            self.check_finished.emit(False, {})
            return
        current = self._parse_version(APP_VERSION)
        self.check_finished.emit(bool(newest and newest > current), info)

    def download_and_install(self, info: dict) -> None:
        """下载安装包 → 启动安装器。"""
        self._info = info
        asset = self._find_installer_asset(info)
        if asset is None:
            self.download_finished.emit(False, "发布中没有找到安装包")
            return
        url = asset["browser_download_url"]
        self._reply = self._manager.get(QNetworkRequest(QUrl(url)))
        self._reply.downloadProgress.connect(self._on_progress)
        self._reply.finished.connect(self._on_download_done)

    @staticmethod
    def _find_installer_asset(info: dict) -> dict | None:
        for asset in info.get("assets", []) or []:
            name = str(asset.get("name") or "")
            if name.lower().endswith(".exe") and "setup" in name.lower():
                return asset
        return None

    def _on_progress(self, received: int, total: int) -> None:
        self.download_progress.emit(int(received), int(total))

    def _on_download_done(self) -> None:
        data = bytes(self._reply.readAll())
        if self._reply.error() != QNetworkReply.NetworkError.NoError or not data:
            self.download_finished.emit(False, "下载失败, 请检查网络后重试")
            return
        try:
            self._target.write_bytes(data)
        except OSError:
            self.download_finished.emit(False, "无法写入安装包文件")
            return
        # Inno Setup 安装包自带覆盖旧版逻辑, 直接启动安装向导。
        subprocess.Popen([str(self._target)], shell=False)
        self.download_finished.emit(True, "更新程序已启动, 请按向导完成安装")
