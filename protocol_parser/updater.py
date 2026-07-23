"""程序在线更新（基于 GitHub Releases + 国内 mirror.ghproxy.com 加速）。

不引入第三方库，全部用标准库：
- urllib.request 做 HTTP GET / 下载
- hashlib.sha256 做整包哈希校验
- tempfile / os 路径处理
- json 解析 GitHub Releases 返回结构

对外主函数：
    check_update(current_version: str, repo: str, timeout: float = 10.0)
        -> (has_new, latest_version, release_page, download_url, sha256_url?, release_notes)

    download_exe(download_url, dst_path, progress_cb=None, timeout=60.0)
        progress_cb(downloaded_bytes, total_bytes) 可用于 UI 进度条

    verify_sha256(path: str, expected: str) -> bool

    prepare_update_and_quit(new_exe_path: str) -> None
        生成 updater.bat，立刻退出主程序，bat 会覆盖旧 EXE 后重新启动。

更新流程（PyInstaller EXE 运行时不能自己覆盖自己）：
    1. 主程序下载 new.exe.tmp 到 EXE 同目录
    2. 校验 sha256 通过
    3. 写 updater.bat（隐藏运行，延迟 2 秒）
    4. 主程序 os._exit(0) 退出释放文件锁
    5. bat 替换文件 -> 重新打开程序 -> 删除自身 bat
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Callable
from urllib import parse, request
from urllib.error import URLError, HTTPError


GHPROXY_PREFIX = "https://mirror.ghproxy.com/"

# 允许的下载文件扩展名：只接受 exe
ALLOWED_EXE_EXTS = (".exe",)


# 避免循环 import：运行时才从 protocol_parser.parser 拿 UpdaterError
def _UpdaterError(message: str, friendly_msg: str | None = None) -> Exception:
    from protocol_parser.parser import UpdaterError
    return UpdaterError(message=message, friendly_msg=friendly_msg)


@dataclass
class UpdateInfo:
    """检查更新结果。has_new=False 时后面字段可能为空。"""

    has_new: bool
    current_version: str
    latest_version: str = ""
    release_page: str = ""
    download_url: str = ""
    download_size: int = 0  # 字节；API 不保证返回，可能为 0
    sha256_expected: str = ""  # 如果 Release assets 里带 SHA256SUMS 或 body 写了；否则空字符串
    release_notes: str = ""


# ---------------------------------------------------------------------------
# 版本比较：严格三位语义化 major.minor.patch，忽略前缀 'v'。非数字位按 0 兜底。
# ---------------------------------------------------------------------------

_RE_VERSION = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def parse_version(v: str) -> tuple[int, int, int]:
    """把 v1.0.1 / 1.0.1 / 1.0 / v1 全部解析成 (major, minor, patch) 三元组。
    解析失败返回 (0,0,0)。"""
    if not v:
        return (0, 0, 0)
    m = _RE_VERSION.match(v.strip())
    if not m:
        return (0, 0, 0)
    a = int(m.group(1) or 0)
    b = int(m.group(2) or 0)
    c = int(m.group(3) or 0)
    return (a, b, c)


def version_newer(latest: str, current: str) -> bool:
    return parse_version(latest) > parse_version(current)


# ---------------------------------------------------------------------------
# HTTP / GitHub Releases
# ---------------------------------------------------------------------------

def _urlopen_json(url: str, timeout: float):
    """简单请求并解析 JSON（自动带 UA）。抛出 URLError / JSONDecodeError。"""
    req = request.Request(
        url,
        headers={
            "User-Agent": "Serial-port-data-parsing-updater/1.0",
            "Accept": "application/json",
        },
    )
    with request.urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        raw = resp.read().decode(charset, errors="replace")
    return json.loads(raw)


def _find_exe_asset(assets: list[dict]) -> dict | None:
    """在 Release assets 里找第一个 .exe（体积最大的优先，避免找错 sha256sums.txt 之类）。"""
    best: dict | None = None
    best_size = -1
    for a in assets or []:
        name = (a.get("name") or "").lower()
        if not name.endswith(ALLOWED_EXE_EXTS):
            continue
        size = int(a.get("size") or 0)
        if size > best_size:
            best = a
            best_size = size
    return best


def check_update(
    current_version: str,
    repo: str,
    timeout: float = 12.0,
    *,
    raise_on_error: bool = False,
) -> UpdateInfo:
    """调用 GitHub /repos/{repo}/releases/latest，比较版本号。

    默认网络/解析失败当作无更新，返回 has_new=False；
    若 raise_on_error=True 则统一抛 UpdaterError（GUI 可据此分类 friendly_msg）。
    """
    api_url = f"https://api.github.com/repos/{repo}/releases/latest"
    latest_json: dict | None = None
    last_exc: Exception | None = None

    # 1) 直接连 GitHub
    try:
        latest_json = _urlopen_json(api_url, timeout=timeout)
    except (URLError, HTTPError, TimeoutError, OSError, ValueError) as e:
        last_exc = e
        latest_json = None

    # 2) GitHub 不通，尝试用 ghproxy 代理解析同一页
    proxied_url = GHPROXY_PREFIX + api_url if api_url.startswith("https://") else api_url
    if not isinstance(latest_json, dict) and proxied_url != api_url:
        try:
            latest_json = _urlopen_json(proxied_url, timeout=timeout)
            last_exc = None
        except (URLError, HTTPError, TimeoutError, OSError, ValueError) as e:
            last_exc = e
            latest_json = None

    info = UpdateInfo(has_new=False, current_version=current_version)
    if not isinstance(latest_json, dict):
        if raise_on_error and last_exc is not None:
            e = last_exc
            if isinstance(e, HTTPError):
                raise _UpdaterError(
                    message=f"check_update HTTP {e.code}: {api_url}",
                    friendly_msg=f"检查更新失败，服务器返回 {e.code}，请稍后重试。",
                ) from e
            if isinstance(e, TimeoutError):
                raise _UpdaterError(
                    message=f"check_update timeout: {api_url}",
                    friendly_msg="检查更新超时，请检查网络后重试。",
                ) from e
            # URLError / OSError / ValueError
            raise _UpdaterError(
                message=f"check_update failed: {type(e).__name__}: {e}",
                friendly_msg="无法连接到更新服务器，请检查网络或稍后重试。",
            ) from e
        return info

    tag: str = str(latest_json.get("tag_name") or "")
    body: str = str(latest_json.get("body") or "")
    html_url: str = str(latest_json.get("html_url") or "")
    info.latest_version = tag
    info.release_page = html_url
    info.release_notes = body.strip()

    if not version_newer(tag, current_version):
        return info

    asset = _find_exe_asset(latest_json.get("assets") or [])
    if not asset:
        # 只有 tag 没有 exe，暂时不给更新（无法下载）
        return info
    direct_url: str = asset.get("browser_download_url") or ""
    info.download_size = int(asset.get("size") or 0)
    if not direct_url:
        return info

    # 国内下载加速：直接用 mirror.ghproxy.com 前缀
    proxied = GHPROXY_PREFIX + direct_url if direct_url.startswith("https://") else direct_url
    info.download_url = proxied

    # 尝试从 assets 找 SHA256SUMS / sha256sums.txt 或 release body 里的 sha256
    sha_from_asset = _fetch_sha256_from_assets(latest_json.get("assets") or [], timeout=timeout)
    sha_from_body = _parse_sha256_from_body(body, asset.get("name") or "")
    info.sha256_expected = sha_from_asset or sha_from_body

    info.has_new = True
    return info


def _fetch_sha256_from_assets(assets: list[dict], timeout: float) -> str:
    for a in assets or []:
        name = (a.get("name") or "").lower()
        if name.endswith((".sha256", ".sha256sum", ".sha256sums", "sha256sums.txt", "sha256sum.txt")):
            url = a.get("browser_download_url")
            if not url:
                continue
            if url.startswith("https://"):
                url = GHPROXY_PREFIX + url
            try:
                req = request.Request(url, headers={"User-Agent": "Serial-port-data-parsing-updater/1.0"})
                with request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read().decode("utf-8", errors="replace").strip()
                # 每行格式：<sha256hex>  文件名
                for line in raw.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split(None, 1)
                    if len(parts) == 2:
                        return parts[0].lower()
                    if len(parts) == 1 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
                        return parts[0].lower()
            except Exception:
                continue
    return ""


def _parse_sha256_from_body(body: str, asset_name: str) -> str:
    """从 Release body 里抠 SHA256：常见写法
        SHA256: 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08
        App.exe: 9f86d08...
    若带文件名就严格匹配。"""
    if not body:
        return ""
    sha_lines = re.findall(r"[0-9a-fA-F]{64}", body)
    if not sha_lines:
        return ""
    if asset_name:
        # 先找带文件名那行：<name>\s*[:=]\s*<sha64>
        mm = re.search(
            re.escape(asset_name) + r"\s*[:=]\s*([0-9a-fA-F]{64})",
            body,
            flags=re.IGNORECASE,
        )
        if mm:
            return mm.group(1).lower()
        # 再找 SHASUM / SHA256: <sha64>
        mm2 = re.search(r"(?i)(?:sha256|sha-?256)\s*[:=]\s*([0-9a-fA-F]{64})", body)
        if mm2:
            return mm2.group(1).lower()
    return sha_lines[0].lower()


# ---------------------------------------------------------------------------
# 下载 + 校验
# ---------------------------------------------------------------------------

def download_exe(
    download_url: str,
    dst_path: str,
    progress_cb: Callable[[int, int], None] | None = None,
    timeout: float = 60.0,
    chunk_size: int = 64 * 1024,
) -> int:
    """分块下载 EXE 到 dst_path，返回下载字节数。

    网络/IO 异常统一转 UpdaterError，确保上层 classify_protocol_error 能拿到友好消息。
    """
    tmp_path = dst_path + ".part"
    req = request.Request(download_url, headers={"User-Agent": "Serial-port-data-parsing-updater/1.0"})
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            length_header = resp.headers.get("Content-Length")
            total = int(length_header) if length_header and length_header.isdigit() else 0
            downloaded = 0
            with open(tmp_path, "wb") as fp:
                while True:
                    data = resp.read(chunk_size)
                    if not data:
                        break
                    fp.write(data)
                    downloaded += len(data)
                    if progress_cb is not None:
                        try:
                            progress_cb(downloaded, total)
                        except Exception:
                            pass
    except (HTTPError, URLError, TimeoutError, OSError) as e:
        # 清理临时文件
        try:
            if os.path.isfile(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        if isinstance(e, HTTPError):
            raise _UpdaterError(
                message=f"download HTTP {e.code}: {download_url}",
                friendly_msg=f"下载失败，服务器返回错误 {e.code}，请检查网络或稍后重试。",
            ) from e
        if isinstance(e, TimeoutError):
            raise _UpdaterError(
                message=f"download timeout: {download_url}",
                friendly_msg="下载超时，请检查网络后重试。",
            ) from e
        if isinstance(e, URLError):
            reason = str(e.reason) if getattr(e, "reason", None) else str(e)
            raise _UpdaterError(
                message=f"download URLError: {reason}",
                friendly_msg="无法连接到下载服务器，请检查网络或稍后重试。",
            ) from e
        # OSError (磁盘满 / 权限等)
        raise _UpdaterError(
            message=f"download IO error: {e}",
            friendly_msg="下载时写入文件失败，请检查磁盘空间或文件权限。",
        ) from e
    try:
        os.replace(tmp_path, dst_path)
    except OSError as e:
        raise _UpdaterError(
            message=f"rename tmp to dst failed: {e}",
            friendly_msg="下载完成但无法写入目标文件，请关闭占用该程序的进程后重试。",
        ) from e
    return downloaded


def verify_sha256(path: str, expected: str) -> bool:
    if not expected:
        return True  # 没提供就跳过校验，不拦
    if not os.path.isfile(path):
        return False
    expected = expected.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        return True
    h = hashlib.sha256()
    with open(path, "rb") as fp:
        while True:
            chunk = fp.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest().lower() == expected


def compute_sha256(path: str) -> str:
    if not os.path.isfile(path):
        return ""
    h = hashlib.sha256()
    with open(path, "rb") as fp:
        while True:
            chunk = fp.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest().lower()


# ---------------------------------------------------------------------------
# 生成 bat 更新器 + 退出主程序
# ---------------------------------------------------------------------------

def _exe_path() -> str:
    """PyInstaller 打包模式返回 sys.executable；开发模式返回当前脚本路径（更新器对 dev 模式会降级失败）。"""
    if getattr(sys, "frozen", False):
        return os.path.abspath(sys.executable)
    return os.path.abspath(sys.argv[0])


def prepare_update_and_quit(
    new_exe_path: str,
    wait_ms_before_replace: int = 2000,
    *,
    snapshot_path: str | None = None,
) -> None:
    """主程序更新最后一步：写快照 → 启动 bat 替换 → 主动 os._exit(0)。

    流程严格顺序（关键：释放串口/文件锁，才能让替换/重启不出错）：
      1. 快照 snapshot_path 已经由 GUI 在调本函数之前：停止串口、flush 磁盘、save_snapshot 完成；
         这里只负责把快照路径告诉 bat（bat 不会动它，新版程序起来后自己 load/clear）。
      2. 生成隐藏 vbs → bat，延迟 N 秒等旧 EXE 完全关闭，再：
         - 重命名旧 EXE → .old
         - move 新 EXE → 原名
         - start 新 EXE
         - 删 .old / 临时 / bat 自删
      3. 本函数 os._exit(0) 立刻退出，不跑 atexit（避免残留句柄锁文件/串口）。

    新参数：
      snapshot_path: 会话快照 JSON 绝对路径。新版 GUI 启动时会从 default_session_path() 读，
                     这里仅做存在性校验，便于定位"快照没写上"的问题。
    """
    new_exe_path = os.path.abspath(new_exe_path)
    if not os.path.isfile(new_exe_path):
        raise _UpdaterError(
            message=f"new exe not found: {new_exe_path}",
            friendly_msg="找不到要更新的安装包文件，请重新下载更新。",
        )

    old_exe = _exe_path()
    if not old_exe.lower().endswith(".exe"):
        # 开发模式：脚本不是 exe，直接告诉用户手动替换
        raise _UpdaterError(
            message="dev mode cannot self-update",
            friendly_msg="开发模式（脚本运行）不支持在线自更新，请手动打包为 EXE 再更新。",
        )

    # 快照可选但一旦提供就必须能读取（debug 用：防止"更新完没恢复串口"定位到是快照没写上）
    if snapshot_path:
        snapshot_path = os.path.abspath(snapshot_path)
        if not os.path.isfile(snapshot_path):
            raise _UpdaterError(
                message=f"snapshot file not found before update: {snapshot_path}",
                friendly_msg="更新前保存会话失败，已中止更新。请重试或手动重启。",
            )

    old_dir = os.path.dirname(old_exe)
    old_name = os.path.basename(old_exe)
    tmp_old = old_name + ".old"
    bat_path = os.path.join(old_dir, "_updater.bat")
    vbs_path = os.path.join(old_dir, "_updater_run.vbs")

    wait_sec = max(1, int(wait_ms_before_replace // 1000))

    # 注意：bat 不应该删除 _update_session.json，由新程序启动成功后自己清。
    bat_content = (
        "@echo off\r\n"
        f"timeout /t {wait_sec} /nobreak >nul\r\n"
        f'cd /d "{old_dir}"\r\n'
        f'if exist "{old_name}" ren "{old_name}" "{tmp_old}"\r\n'
        f'move /y "{new_exe_path}" "{old_dir}\\{old_name}"\r\n'
        f'start "" "{old_dir}\\{old_name}"\r\n'
        f'if exist "{tmp_old}" del /f /q "{tmp_old}"\r\n'
        f'if exist "{os.path.basename(new_exe_path)}" del /f /q "{os.path.basename(new_exe_path)}"\r\n'
        # 顺手清理旧 bat/vbs 自己，和已超过 1 天的 session（防止长期残留）
        f'forfiles /p "{old_dir}" /m "_update_session.json" /d -7 /c "cmd /c del /f /q @path" 2>nul\r\n'
        f'(goto) 2>nul & del "%~f0"\r\n'
    )
    with open(bat_path, "w", encoding="gbk", errors="ignore") as fp:
        fp.write(bat_content)

    vbs_content = (
        'Set ws = CreateObject("Wscript.Shell")\r\n'
        + f'ws.Run """{bat_path}""", 0, False\r\n'
    )
    with open(vbs_path, "w", encoding="gbk", errors="ignore") as fp:
        fp.write(vbs_content)

    # 启动 VBS（不阻塞）
    try:
        import subprocess
        DETACHED = 0x00000008
        subprocess.Popen(
            ["wscript.exe", "//B", vbs_path],
            cwd=old_dir,
            creationflags=DETACHED,
            close_fds=True,
        )
    except Exception:
        try:
            import subprocess
            os.startfile(bat_path)  # type: ignore[attr-defined]
        except Exception:
            pass
    # 立刻退出（必须是 _exit 避免 atexit 关不掉串口/残留句柄锁文件）
    os._exit(0)
