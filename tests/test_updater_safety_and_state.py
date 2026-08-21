from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

import protocol_parser.updater as updater_module
from protocol_parser.app_info import APP_VERSION
from protocol_parser.updater import UpdatePhase, Updater


class _CheckResponse:
    status_code = 200
    content = b"{}"

    @staticmethod
    def json() -> dict:
        return {"tag_name": APP_VERSION, "assets": []}


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


def test_fallback_defaults_to_https_and_requires_explicit_override(monkeypatch):
    monkeypatch.delenv("SERIALX_UPDATE_FALLBACK_URL", raising=False)
    assert updater_module._resolve_fallback_url().startswith("https://")

    monkeypatch.setenv("SERIALX_UPDATE_FALLBACK_URL", "D:/dev/version.json")
    assert updater_module._resolve_fallback_url() == "D:/dev/version.json"


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


def test_bundled_version_json_is_used_when_remote_sources_fail(monkeypatch, tmp_path):
    root = Path(__file__).resolve().parents[1]
    bundled = root / "resources" / "version.json"
    assert bundled.is_file()

    def fail_remote(*args, **kwargs):
        raise RuntimeError("network blocked")

    monkeypatch.setattr(updater_module.requests, "get", fail_remote)
    updater = Updater()
    has_new, info = updater._check_via_fallback_sources()
    assert "__error" not in info
    assert info.get("tag_name") == APP_VERSION
    assert has_new is False


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
    assert metadata["download_url"].startswith("https://")
    assert installer.is_file()
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
    assert "…" in message


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
