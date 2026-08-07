#!/usr/bin/env python3
"""Apply BuildFix33 remaining source patches (parser + mcu_page).

Run from repository root on branch fix/hardening-p0-p1:

    python scripts/apply_buildfix33_patches.py

Then commit the modified protocol_parser/parser.py and protocol_parser/mcu_page.py.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCHES = [
    ROOT / "patches" / "parser_strict_hex_and_cache.patch",
    ROOT / "patches" / "mcu_page_qtimer_context.patch",
]


def main() -> int:
    for patch in PATCHES:
        if not patch.exists():
            print(f"missing patch: {patch}", file=sys.stderr)
            return 1
        print(f"applying {patch.relative_to(ROOT)} ...")
        r = subprocess.run(
            ["patch", "-p1", "--forward", "--reject-file=-"],
            cwd=ROOT,
            input=patch.read_text(encoding="utf-8"),
            text=True,
            capture_output=True,
        )
        if r.returncode not in (0, 1):
            # 1 may mean already applied partially; print output for diagnosis
            print(r.stdout)
            print(r.stderr, file=sys.stderr)
            if r.returncode > 1:
                return r.returncode
        else:
            print(r.stdout or "ok")
            if r.stderr:
                print(r.stderr, file=sys.stderr)
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
