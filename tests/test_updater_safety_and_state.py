from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

import pytest

import protocol_parser.updater as updater_module
from protocol_parser.app_info import APP_VERSION
from protocol_parser.updater import UpdatePhase, Updater


class _CheckResponse:
    status_code = 200
    content = b"{}"

    @staticmethod
    def json() -> dict:
        return {
            "tag_name": APP_VERSION,
            "assets": [{
                "name": f"SerialXSetup{APP_VERSION}_x64.exe",
                "browser_download_url": "https://example.invalid/setup.exe",
            }],
        }


class _DownloadResponse:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.headers = {"Content-Length": str(sum(map(len, chunks)))}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    @staticmethod
    def raise_for_status() -> None:
        return None

    def iter_content(self, chunk_size: int):
        assert chunk_size == 65536
        yield from self._chunks


def _release_info(payload: bytes) -> dict:
    digest = hashlib.sha256(payload).hexdigest()
    return {
        "tag_name": "99.0.0",
        "sha256": digest,
        "assets": [
            {
                "name": "SerialXSetup99.0.0_x64.exe",
                "browser_download_url": "https://example.invalid/SerialXSetup.exe",
                "sha256": digest,
            }
        ],
    }


def test_fallback_defaults_to_gitee_version_json(monkeypatch):
    monkeypatch.delenv("SERIALX_UPDATE_FALLBACK_URL", raising=False)
    assert updater_module._resolve_fallback_url() == updater_module.DEFAULT_GITEE_VERSION_URL
    assert "gitee.com" in updater_module._resolve_fallback_url()
    assert "v3.1.0" in updater_module.DEFAULT_GITEE_VERSION_URL

    monkeypatch.setenv("SERIALX_UPDATE_FALLBACK_URL", "D:/dev/version.json")
    assert updater_module._resolve_fallback_url() == "D:/dev/version.json"


def test_enrich_release_ignores_mismatched_version_json(monkeypatch):
    release = {
        "tag_name": "3.4.1",
        "assets": [{
            "name": "SerialXSetup3.4.1_x64.exe",
            "browser_download_url": "https://example.invalid/setup.exe",
        }],
    }

    def fake_fetch(tag, *, allow_bundled=False):
        assert tag == "3.4.1"
        return None

    monkeypatch.setattr(Updater, "_metadata_from_release_assets", lambda info: None)
    monkeypatch.setattr(Updater, "_fetch_metadata_for_tag", fake_fetch)
    enriched = Updater._enrich_release_integrity(dict(release))
    assert Updater._expected_sha256(enriched, Updater._find_installer_asset(enriched)) == ""


def test_enrich_release_applies_matching_version_json(monkeypatch):
    release = {
        "tag_name": "3.4.1",
        "assets": [{
            "name": "SerialXSetup3.4.1_x64.exe",
            "browser_download_url": "https://example.invalid/setup.exe",
        }],
    }
    digest = "a" * 64
    metadata = {
        "tag_name": "3.4.1",
        "sha256": digest,
        "assets": [{
            "name": "SerialXSetup3.4.1_x64.exe",
            "browser_download_url": "https://example.invalid/setup.exe",
            "sha256": digest,
        }],
    }
    monkeypatch.setattr(Updater, "_metadata_from_release_assets", lambda info: None)
    monkeypatch.setattr(
        Updater,
        "_fetch_metadata_for_tag",
        lambda tag, *, allow_bundled=False: metadata if tag == "3.4.1" else None,
    )
    enriched = Updater._enrich_release_integrity(dict(release))
    assert Updater._expected_sha256(enriched, Updater._find_installer_asset(enriched)) == digest


def test_placeholder_sha256_is_rejected():
    assert not Updater._is_usable_sha256("0" * 64)
    assert Updater._is_usable_sha256("f" * 64)


def test_plain_http_update_urls_are_rejected(monkeypatch):
    monkeypatch.delenv("SERIALX_ALLOW_INSECURE_UPDATE_URL", raising=False)
    try:
        Updater._validate_download_url("http://example.invalid/setup.exe")
    except RuntimeError as exc:
        assert "HTTPS" in str(exc)
    else:
        raise AssertionError("plain HTTP update URL was accepted")


def test_concurrent_update_checks_are_single_flight(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def fake_get(*args, **kwargs):
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(2)
        return _CheckResponse()

    monkeypatch.setattr(updater_module.requests, "get", fake_get)
    updater = Updater()
    assert updater.check_update(manual=True) is True
    assert entered.wait(2)
    assert updater.check_update(manual=True) is False
    assert updater.phase is UpdatePhase.CHECKING
    release.set()
    thread = updater._check_thread
    assert thread is not None
    thread.join(2)
    assert not thread.is_alive()
    assert updater.phase is UpdatePhase.IDLE
    assert calls == 1


def test_github_release_without_installer_is_skipped():
    empty = {"tag_name": "3.3.6", "draft": False, "prerelease": False, "assets": []}
    ready = {
        "tag_name": "3.3.5",
        "draft": False,
        "prerelease": False,
        "assets": [{"name": "SerialXSetup3.3.5_x64.exe", "browser_download_url": "https://example/x.exe"}],
    }
    picked = Updater._pick_usable_release([empty, ready])
    assert picked is ready


def test_gitee_api_is_tried_before_github(monkeypatch):
    calls: list[str] = []
    entered = threading.Event()
    release = threading.Event()

    def fake_get(url, *args, **kwargs):
        calls.append(str(url))
        entered.set()
        assert release.wait(2)
        if "gitee.com" in str(url):
            return _CheckResponse()
        raise AssertionError(f"GitHub should not be queried when Gitee succeeds: {url}")

    monkeypatch.setattr(updater_module.requests, "get", fake_get)
    updater = Updater()
    assert updater.check_update(manual=True) is True
    assert entered.wait(2)
    assert calls
    assert "gitee.com" in calls[0]
    release.set()
    thread = updater._check_thread
    if thread is not None:
        thread.join(2)
    deadline = time.time() + 2
    while updater.phase is not UpdatePhase.IDLE and time.time() < deadline:
        time.sleep(0.02)
    assert updater.phase is UpdatePhase.IDLE
    assert not any("api.github.com" in item for item in calls)


def test_github_is_used_when_gitee_fails(monkeypatch):
    calls: list[str] = []

    class _GithubResponse(_CheckResponse):
        pass

    def fake_get(url, *args, **kwargs):
        calls.append(str(url))
        if "gitee.com" in str(url):
            raise RuntimeError("gitee blocked")
        if "api.github.com" in str(url):
            return _GithubResponse()
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(updater_module.requests, "get", fake_get)
    updater = Updater()
    assert updater.check_update(manual=True) is True
    deadline = time.time() + 2
    while time.time() < deadline:
        if updater.phase is UpdatePhase.IDLE and any("api.github.com" in item for item in calls):
            break
        time.sleep(0.02)
    assert updater.phase is UpdatePhase.IDLE
    assert any("gitee.com" in item for item in calls)
    assert any("api.github.com" in item for item in calls)


def test_bundled_version_json_is_not_treated_as_remote_latest(monkeypatch, tmp_path):
    root = Path(__file__).resolve().parents[1]
    bundled = root / "resources" / "version.json"
    assert bundled.is_file()

    def fail_remote(*args, **kwargs):
        raise RuntimeError("network blocked")

    monkeypatch.setattr(updater_module.requests, "get", fail_remote)
    updater = Updater()
    try:
        updater._check_via_fallback_sources()
    except RuntimeError as exc:
        assert "network blocked" in str(exc)
    else:
        raise AssertionError("bundled version.json must not hide a failed remote check")


def test_failed_checks_enter_backoff_for_silent_requests(monkeypatch):
    monkeypatch.setattr(
        updater_module.requests,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("blocked")),
    )
    monkeypatch.setattr(
        updater_module,
        "_iter_fallback_sources",
        lambda: ["https://example.invalid/version.json"],
    )
    updater = Updater()
    assert updater.check_update(manual=False) is True
    time.sleep(0.5)
    assert updater.seconds_until_next_check() > 0
    assert updater.check_update(manual=False) is False
    assert updater.check_update(manual=True) is True
    time.sleep(0.5)
    assert updater.phase is UpdatePhase.IDLE


def test_download_is_streamed_and_sha256_verified(tmp_path, monkeypatch):
    chunks = [b"MZ", b"\x00" * 1024, b"installer"]
    payload = b"".join(chunks)
    launched: list[list[str]] = []
    monkeypatch.setattr(
        updater_module.requests,
        "get",
        lambda *args, **kwargs: _DownloadResponse(chunks),
    )
    monkeypatch.setattr(
        updater_module.subprocess,
        "Popen",
        lambda argv, shell=False: launched.append(list(argv)),
    )

    updater = Updater()
    updater._target = tmp_path / "SerialX_setup_latest.exe"
    assert updater.download_and_install(_release_info(payload)) is True
    thread = updater._download_thread
    assert thread is not None
    thread.join(2)

    assert updater._target.read_bytes() == payload
    assert launched == [[str(updater._target)]]
    assert not updater._target.with_suffix(".exe.part").exists()
    assert updater.phase is UpdatePhase.IDLE


def test_bad_sha256_never_launches_installer(tmp_path, monkeypatch):
    chunks = [b"not-the-expected-installer"]
    launched: list[list[str]] = []
    monkeypatch.setattr(
        updater_module.requests,
        "get",
        lambda *args, **kwargs: _DownloadResponse(chunks),
    )
    monkeypatch.setattr(
        updater_module.subprocess,
        "Popen",
        lambda argv, shell=False: launched.append(list(argv)),
    )
    info = _release_info(b"different")

    updater = Updater()
    updater._target = tmp_path / "SerialX_setup_latest.exe"
    assert updater.download_and_install(info) is True
    thread = updater._download_thread
    assert thread is not None
    thread.join(2)

    assert launched == []
    assert not updater._target.exists()
    assert not updater._target.with_suffix(".exe.part").exists()
    assert updater.phase is UpdatePhase.IDLE


def test_version_json_has_matching_version_and_installer_digest():
    root = Path(__file__).resolve().parents[1]
    metadata = json.loads((root / "version.json").read_text(encoding="utf-8"))
    installer = root / "release" / metadata["asset_name"]
    assert metadata["version"] == APP_VERSION
    assert metadata["tag_name"] == APP_VERSION
    assert metadata["download_url"].startswith("https://gitee.com/")
    # 构建流程先跑测试、后生成安装包；安装包尚不存在时仅校验元数据一致性，
    # 构建完成后（write_release_version_json 重写 version.json）digest 校验自然生效。
    if not installer.is_file():
        pytest.skip("安装包尚未构建，跳过 digest 校验")
    assert hashlib.sha256(installer.read_bytes()).hexdigest() == metadata["sha256"]


def test_gui_update_prompt_is_non_reentrant_and_single_instance():
    root = Path(__file__).resolve().parents[1]
    gui = (root / "protocol_parser" / "gui.py").read_text(encoding="utf-8")
    update_region = gui[gui.index("def _setup_update_feature") : gui.index(
        "# ================= 在线更新区域结束"
    )]
    assert "_update_ui_phase" in update_region
    assert "if self._update_prompt is not None:" in update_region
    assert "prompt.open()" in update_region
    assert "StyledMessageBox.build_question" in update_region
    assert "_StyledMessageDialog" in (root / "protocol_parser" / "widgets.py").read_text(
        encoding="utf-8"
    )
    assert "_update_result_prompt" in update_region
    assert "_on_update_result_prompt_finished" in update_region
    assert '_set_update_ui_phase("result")' in update_region
    assert "_QtMessageBox.question(" not in update_region
    assert "QMessageBox.information(self, \"更新\"" not in update_region
    assert "setMinimumDuration(0)" in update_region
    assert "dialog.canceled.connect(self._updater.cancel_download)" in update_region
    assert "_update_check_manual" in update_region


def test_gui_repeated_update_result_keeps_one_prompt(monkeypatch, tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from protocol_parser import gui

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(gui, "UPDATE_ENABLED", False)
    monkeypatch.setattr(gui, "load_snapshot", lambda: None)
    monkeypatch.setattr(gui, "save_snapshot", lambda snapshot: tmp_path / "session.json")
    window = gui.ProtocolParserApp()
    try:
        window._update_ui_phase = "checking"
        release = {
            "tag_name": "99.0.0",
            "body": "test",
            "assets": [],
        }
        window._on_update_check_finished(True, release)
        first_prompt = window._update_prompt
        assert first_prompt is not None
        assert window._update_ui_phase == "prompt"

        window._on_update_check_finished(True, release)
        assert window._update_prompt is first_prompt

        first_prompt.reject()
        app.processEvents()
        assert window._update_prompt is None
        assert window._update_ui_phase == "idle"
    finally:
        window.close()
        app.processEvents()


def test_update_prompt_notes_are_truncated():
    from protocol_parser.updater import format_update_prompt_message

    long_notes = "\n".join(f"line-{index}" for index in range(30))
    message = format_update_prompt_message("99.0.0", long_notes, max_lines=5, max_chars=80)
    assert "line-0" in message
    assert "line-29" not in message
    assert message.endswith("是否立即下载并更新?")
    assert "版本 99.0.0" in message
    assert "…" in message

    single_long = "3.3.6 更新 " + ("优化性能 " * 20)
    wrapped = format_update_prompt_message("3.3.6", single_long, max_lines=10, max_chars=400)
    assert wrapped.count("\n") >= 2


def test_gui_download_without_sha256_closes_progress_dialog(monkeypatch, tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from protocol_parser import gui

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(gui, "UPDATE_ENABLED", False)
    monkeypatch.setattr(gui, "load_snapshot", lambda: None)
    monkeypatch.setattr(gui, "save_snapshot", lambda snapshot: tmp_path / "session.json")
    window = gui.ProtocolParserApp()
    try:
        from protocol_parser.updater import Updater

        window._updater = Updater(window)
        window._update_prompt = None
        release = {"tag_name": "99.0.0", "assets": []}
        window._on_update_prompt_finished(release)
        app.processEvents()
        assert window._update_ui_phase == "idle"
        assert window._update_dialog is None
    finally:
        window.close()
        app.processEvents()
