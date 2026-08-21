"""Fail installer builds if identity/version constants drift apart."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from protocol_parser.app_info import APP_EXE_BASENAME, APP_EXE_NAME, APP_NAME, APP_VERSION  # noqa: E402

iss = (ROOT / "installer" / "serial_port_parser.iss").read_text(encoding="utf-8")
spec = (ROOT / "serial_port_parser_fast.spec").read_text(encoding="utf-8")
version_info = (ROOT / "resources" / "version_info.txt").read_text(encoding="utf-8")
version_json = json.loads((ROOT / "version.json").read_text(encoding="utf-8"))
checks = {
    "installer app name": f'#define MyAppName          "{APP_NAME}"' in iss,
    "installer version": f'#define MyAppVersion       "{APP_VERSION}"' in iss,
    "installer exe": f'#define MyAppExeName       "{APP_EXE_NAME}"' in iss,
    "installer base name": f'#define MyAppBaseName      "{APP_EXE_BASENAME}"' in iss,
    "installer OutputBaseFilename uses base name": f"OutputBaseFilename={{#MyAppBaseName}}Setup{{#MyAppVersion}}_x64" in iss,
    "installer stable AppId": "AppId={#MyAppAssistedGUID}" in iss,
    "PyInstaller exe name": f'name="{Path(APP_EXE_NAME).stem}"' in spec,
    "PE product version": f"StringStruct('ProductVersion', '{APP_VERSION}')" in version_info,
    "update metadata version": version_json.get("version") == APP_VERSION,
    "update metadata tag": version_json.get("tag_name") == APP_VERSION,
    "update metadata uses HTTPS": str(version_json.get("download_url") or "").startswith("https://"),
}
failed = [name for name, ok in checks.items() if not ok]
if failed:
    raise SystemExit("Identity mismatch: " + ", ".join(failed))
print(f"Identity OK: {APP_NAME} {APP_VERSION} / {APP_EXE_NAME}")
