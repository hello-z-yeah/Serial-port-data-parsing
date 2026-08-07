"""Single source of truth for application identity and version."""
from __future__ import annotations

APP_NAME = "SST_串口工具"
APP_SHORT_NAME = "SST"
APP_VERSION = "3.1.0"
APP_ID = "SST.SerialTool.App.3"
APP_DATA_DIR_NAME = "SST_串口工具"
APP_EXE_BASENAME = "SST_串口工具"
APP_EXE_NAME = f"{APP_EXE_BASENAME}.exe"
APP_PUBLISHER = "SST"

# Increment when bundled protocol defaults require a one-time user-data refresh.
BUNDLED_PRODUCT_SEED_REVISION = "3.1.0-r1"

# Bundled defaults removed from the application.
RETIRED_BUNDLED_PRODUCT_HASHES = {
    "wise1.avamp.wise51.json": "6fdb912b9f298a846ef61fd4d2f42be8f48f2f58507284fedba3b8d4185d315e",
}
