"""Console entry point for the Super Max Serial Tool build manager."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from build_tools.build_manager import cli_main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(cli_main())
