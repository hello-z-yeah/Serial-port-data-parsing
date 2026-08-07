from __future__ import annotations

import struct
from pathlib import Path

import build_tools.build_manager as bm


def test_build_manager_uses_real_console_python_and_supported_range():
    python_name = Path(bm.PYTHON_EXECUTABLE).name.lower()
    assert python_name in {"python", "python.exe", "python3"} or python_name.startswith("python3.")
    assert bm.SUPPORTED_MIN == (3, 11)
    assert bm.SUPPORTED_MAX_EXCLUSIVE == (3, 15)
    assert struct.calcsize("P") * 8 == 64


def test_environment_report_contains_actionable_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(bm, "LOG_DIR", tmp_path)
    logger = bm.TeeLogger()
    runner = bm.BuildRunner(logger)
    monkeypatch.setattr(runner, "dependency_status", lambda: ("PySide6",))
    monkeypatch.setattr(runner, "find_inno_setup", lambda: None)
    try:
        report = runner.environment_report()
        assert report.python_executable
        assert report.architecture_bits == 64
        assert report.missing_dependencies == ("PySide6",)
        assert report.inno_setup is None
    finally:
        logger.close()


def test_find_inno_setup_honors_override(tmp_path, monkeypatch):
    fake_iscc = tmp_path / "ISCC.exe"
    fake_iscc.write_bytes(b"")
    monkeypatch.setenv("ISCC_OVERRIDE", str(fake_iscc))
    logger = bm.TeeLogger()
    try:
        runner = bm.BuildRunner(logger)
        assert runner.find_inno_setup() == fake_iscc.resolve()
    finally:
        logger.close()


def test_cli_actions_include_installer_and_diagnostics():
    parser = bm.build_arg_parser()
    assert parser.parse_args(["diagnose"]).action == "diagnose"
    assert parser.parse_args(["build-installer"]).action == "build-installer"
