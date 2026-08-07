from __future__ import annotations

from pathlib import Path

import pytest

import build_tools.build_manager as bm


ROOT = Path(__file__).resolve().parents[1]


def test_inno_app_id_is_escaped_as_literal_guid() -> None:
    iss = (ROOT / "installer" / "serial_port_parser.iss").read_text(encoding="utf-8-sig")
    assert bm.INNO_APP_ID_DEFINE in iss
    assert bm.INNO_APP_ID_ASSIGNMENT in iss
    bm.validate_inno_setup_script(ROOT / "installer" / "serial_port_parser.iss")


def test_inno_preflight_rejects_single_opening_brace(tmp_path: Path) -> None:
    bad = tmp_path / "bad.iss"
    bad.write_text(
        '#define MyAppAssistedGUID  "{B1F3A7D8-6C9E-4F2B-9A8C-7D5E3F1A2B4C}"\n'
        'AppId={#MyAppAssistedGUID}\n',
        encoding="utf-8",
    )
    with pytest.raises(bm.BuildManagerError, match="Unknown constant"):
        bm.validate_inno_setup_script(bad)


def test_installer_does_not_require_optional_chinese_language_pack() -> None:
    iss = (ROOT / "installer" / "serial_port_parser.iss").read_text(encoding="utf-8-sig")
    assert "ChineseSimplified.isl" not in iss
    assert "compiler:Languages" not in iss
    bm.validate_inno_setup_script(ROOT / "installer" / "serial_port_parser.iss")


def test_inno_preflight_rejects_optional_language_dependency(tmp_path: Path) -> None:
    bad = tmp_path / "bad_language.iss"
    bad.write_text(
        bm.INNO_APP_ID_DEFINE
        + "\n"
        + bm.INNO_APP_ID_ASSIGNMENT
        + "\n[Languages]\n"
        + 'Name: "chinesesimp"; MessagesFile: "compiler:Languages\\ChineseSimplified.isl"\n',
        encoding="utf-8",
    )
    with pytest.raises(bm.BuildManagerError, match="ChineseSimplified.isl"):
        bm.validate_inno_setup_script(bad)
