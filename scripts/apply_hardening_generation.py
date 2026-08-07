#!/usr/bin/env python3
"""Apply generation / auto_reply / ui_helpers surgical hardening (BuildFix generation).

Run from repo root on fix/hardening-p0-p1 after pulling latest:

    python scripts/apply_hardening_generation.py

Restores attr_center / auto_reply / gui / ui_helpers from the unified patch
(relative to the commit that still had the pre-generation sources).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATCH = Path(__file__).resolve().parent / "apply_hardening_generation.patch"


def main() -> int:
    if not PATCH.exists():
        print(f"missing patch: {PATCH}", file=sys.stderr)
        return 1
    # Prefer git apply for correctness
    r = subprocess.run(
        ["git", "apply", "--check", str(PATCH)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        print("git apply --check failed:", r.stderr or r.stdout, file=sys.stderr)
        print("If files are already patched, ignore. Otherwise restore from d288572 first.", file=sys.stderr)
        return 1
    r = subprocess.run(
        ["git", "apply", str(PATCH)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        print("git apply failed:", r.stderr or r.stdout, file=sys.stderr)
        return 1
    print("Applied generation hardening patch successfully.")
    print("Verify with: pytest tests/test_generation_and_error_oneshot.py -v")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
