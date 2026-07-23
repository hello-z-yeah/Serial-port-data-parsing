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
) -> UpdateInfo:
    """调用 GitHub /repos/{repo}/releases/latest，比较版本号。
    网络/解析失败不会崩溃，直接返回 has_new=False（失败当作无更新，不阻塞用户）。"""
    api_url = f"https://api.github.com/repos/{repo}/releases/latest"
    latest_json: dict | None = None
    last_exc: Exception | None = None

    # 1) 直接连 GitHub
    try:
        latest_json = _urlopen_json(api_url, timeout=timeout)
    except (URLError, HTTPError, TimeoutError, OSError, ValueError) as e:
        last_exc = e
        latest_json = None

    # 2) GitHub 不通，尝试用 ghproxy 代理解析同一页（ghproxy 也能转发 API，但它主要加速文件。
    #    这里 API 不行就作罢，至少下载阶段会加速。）
    info = UpdateInfo(has_new=False, current_version=current_version)
    if not isinstance(latest_json, dict):
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
    progress_cb(downloaded, total)  total 可能为 0（服务器没给 Content-Length）。"""
    tmp_path = dst_path + ".part"
    req = request.Request(download_url, headers={"User-Agent": "Serial-port-data-parsing-updater/1.0"})
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
    os.replace(tmp_path, dst_path)
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


def prepare_update_and_quit(new_exe_path: str, wait_ms_before_replace: int = 2000) -> None:
    """主程序更新最后一步：用 bat 替换 EXE。
    - 新 EXE 要与旧 EXE 同一分区（保证 os.replace 原子）。
    - 调用后本函数会直接 os._exit(0)，不再返回。
    """
    new_exe_path = os.path.abspath(new_exe_path)
    if not os.path.isfile(new_exe_path):
        raise FileNotFoundError(new_exe_path)

    old_exe = _exe_path()
    if not old_exe.lower().endswith(".exe"):
        # 开发模式：脚本不是 exe，直接告诉用户手动替换
        raise RuntimeError("开发模式（脚本运行）不支持在线自更新，请手动打包为 EXE 再更新。")

    old_dir = os.path.dirname(old_exe)
    old_name = os.path.basename(old_exe)
    tmp_old = old_name + ".old"
    bat_path = os.path.join(old_dir, "_updater.bat")
    vbs_path = os.path.join(old_dir, "_updater_run.vbs")

    # 把更新器脚本和 VBS 隐藏启动都准备好
    wait_sec = max(1, int(wait_ms_before_replace // 1000))
    # 1) bat 主体：延时 -> 重命名旧 exe -> 新 exe 覆盖原名 -> 启动 -> 删除旧 -> 删 bat 自己
    bat_content = (
        "@echo off\r\n"
        f"timeout /t {wait_sec} /nobreak >nul\r\n"
        f'cd /d "{old_dir}"\r\n'
        f'if exist "{old_name}" ren "{old_name}" "{tmp_old}"\r\n'
        f'move /y "{new_exe_path}" "{old_dir}\\{old_name}"\r\n'
        f'start "" "{old_dir}\\{old_name}"\r\n'
        f'if exist "{tmp_old}" del /f /q "{tmp_old}"\r\n'
        f'if exist "{os.path.basename(new_exe_path)}" del /f /q "{os.path.basename(new_exe_path)}"\r\n'
        f'(goto) 2>nul & del "%~f0"\r\n'
    )
    with open(bat_path, "w", encoding="gbk", errors="ignore") as fp:
        fp.write(bat_content)

    # 2) 用 VBS 隐藏运行 bat（避免黑框一闪而过）
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
        # VBS 失败也兜底：直接 start bat
        try:
            import subprocess
            os.startfile(bat_path)  # type: ignore[attr-defined]
        except Exception:
            pass
    # 立刻退出（必须是 _exit 避免 atexit 关不掉串口/残留句柄锁文件）
    os._exit(0)
