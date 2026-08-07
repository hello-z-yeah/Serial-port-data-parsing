"""Resource and writable user-data paths (PyInstaller compatible).

Installed program files are treated as read-only.  All mutable state lives
under ``%LOCALAPPDATA%\\SuperMaxSerialTool`` on Windows.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path

from .app_info import (
    APP_DATA_DIR_NAME,
    BUNDLED_PRODUCT_SEED_REVISION,
    RETIRED_BUNDLED_PRODUCT_HASHES,
)


_APP_DATA_INIT_LOCK = threading.Lock()
_APP_DATA_INITIALIZED_ROOTS: set[Path] = set()


def resource_path(relative: str) -> Path:
    """Return a bundled read-only resource path."""
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / relative
    package_dir = Path(__file__).resolve().parent
    candidate = package_dir / relative
    if candidate.exists():
        return candidate
    return package_dir.parent / relative


def _resolve_app_data_root(
    *,
    platform_name: str | None = None,
    environ: dict[str, str] | os._Environ[str] | None = None,
    home: Path | None = None,
) -> Path:
    """Pure path resolver used by runtime code and platform-independent tests."""
    platform_name = os.name if platform_name is None else platform_name
    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else Path(home)
    if platform_name == "nt":
        base = environ.get("LOCALAPPDATA")
        return (Path(base) if base else home / "AppData" / "Local") / APP_DATA_DIR_NAME
    base = environ.get("XDG_DATA_HOME")
    return (Path(base) if base else home / ".local" / "share") / APP_DATA_DIR_NAME


def app_data_root() -> Path:
    """Return the writable application root, creating it if necessary."""
    root = _resolve_app_data_root()
    root.mkdir(parents=True, exist_ok=True)
    resolved = root.resolve()
    if resolved not in _APP_DATA_INITIALIZED_ROOTS:
        with _APP_DATA_INIT_LOCK:
            if resolved not in _APP_DATA_INITIALIZED_ROOTS:
                _migrate_legacy_data(root)
                _seed_bundled_user_defaults(root)
                _APP_DATA_INITIALIZED_ROOTS.add(resolved)
    return root


def user_data_path(relative: str = "") -> Path:
    """Return the raw-data directory or a child path within it."""
    path = app_data_root() / "data"
    if relative:
        path /= relative
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_dir() -> Path:
    path = app_data_root() / "config"
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    path = app_data_root() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _read_bundled_products_manifest(root: Path) -> dict:
    """Read the bundled-product manifest without making startup depend on it."""
    manifest_path = root / "config" / "bundled_products_manifest.json"
    try:
        loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _deleted_bundled_product_names(manifest: dict) -> set[str]:
    raw_deleted = manifest.get("deleted")
    if not isinstance(raw_deleted, (list, tuple, set)):
        return set()
    return {
        Path(str(item)).name
        for item in raw_deleted
        if str(item or "").strip()
    }


def mark_product_json_deleted(filename: str) -> None:
    """Remember a user deletion so a bundled seed is not copied back.

    Bundled products are copied from the read-only installation into the
    writable LocalAppData product directory.  Without a deletion marker, the
    next protocol refresh would treat a deliberately deleted bundled JSON as a
    missing default and recreate it immediately.
    """
    clean_name = Path(str(filename or "").strip()).name
    if not clean_name or clean_name in {".", ".."}:
        return
    root = app_data_root()
    manifest_path = root / "config" / "bundled_products_manifest.json"
    manifest = _read_bundled_products_manifest(root)
    deleted = _deleted_bundled_product_names(manifest)
    deleted.add(clean_name)
    manifest["deleted"] = sorted(deleted, key=str.casefold)
    manifest["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _atomic_write_json(manifest_path, manifest)


def clear_product_json_deleted(filename: str) -> None:
    """Clear a deletion marker after the user explicitly recreates a product."""
    clean_name = Path(str(filename or "").strip()).name
    if not clean_name:
        return
    root = app_data_root()
    manifest_path = root / "config" / "bundled_products_manifest.json"
    manifest = _read_bundled_products_manifest(root)
    deleted = _deleted_bundled_product_names(manifest)
    if clean_name not in deleted:
        return
    deleted.discard(clean_name)
    manifest["deleted"] = sorted(deleted, key=str.casefold)
    manifest["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _atomic_write_json(manifest_path, manifest)


def _sync_bundled_products(root: Path, target: Path, source: Path) -> None:
    """Version bundled defaults while respecting products deleted by the user.

    Earlier releases copied products into LocalAppData only when the destination
    did not exist.  A corrected bundled product therefore never reached users
    who had already run an older build.  This versioned sync refreshes managed
    defaults once per seed revision and backs up the previous user-side file
    before replacement.  Between revisions, a user edit is left untouched.

    A deliberately deleted bundled product is recorded in the manifest and is
    not recreated on every call to :func:`get_protocol_dir`.
    """
    if not source.is_dir():
        return

    manifest_path = root / "config" / "bundled_products_manifest.json"
    previous = _read_bundled_products_manifest(root)
    deleted_names = _deleted_bundled_product_names(previous)

    previous_revision = str(previous.get("revision") or "")
    previous_hashes = previous.get("hashes")
    if not isinstance(previous_hashes, dict):
        previous_hashes = {}

    revision_changed = previous_revision != BUNDLED_PRODUCT_SEED_REVISION
    current_hashes: dict[str, str] = {}
    sync_complete = True
    backup_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = target / "backups" / f"bundled_refresh_{backup_stamp}"

    # Remove defaults retired from the application, but only when the
    # LocalAppData copy is byte-for-byte the old managed default.  A user-edited
    # or explicitly re-imported file with the same name is preserved.
    for retired_name, retired_hash in RETIRED_BUNDLED_PRODUCT_HASHES.items():
        dest = target / Path(retired_name).name
        if not dest.is_file():
            continue
        try:
            dest_hash = _sha256_file(dest)
        except OSError:
            sync_complete = False
            continue
        previous_hash = str(previous_hashes.get(dest.name) or "")
        managed_hashes = {str(retired_hash or ""), previous_hash} - {""}
        if dest_hash not in managed_hashes:
            continue
        try:
            backup_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest, backup_dir / dest.name)
            dest.unlink()
            deleted_names.add(dest.name)
        except OSError:
            sync_complete = False

    for item in sorted(source.glob("*.json"), key=lambda path: path.name.casefold()):
        try:
            source_hash = _sha256_file(item)
        except OSError:
            continue
        current_hashes[item.name] = source_hash
        dest = target / item.name

        if not dest.exists():
            if item.name in deleted_names:
                # The user deliberately removed this bundled product.  Missing
                # must not be interpreted as "seed it again".
                continue
            try:
                shutil.copy2(item, dest)
            except OSError:
                sync_complete = False
            continue

        # A file with this name exists again, which means the user explicitly
        # recreated/imported it.  It should participate in normal future syncs.
        deleted_names.discard(item.name)

        try:
            dest_hash = _sha256_file(dest)
        except OSError:
            dest_hash = ""
        if dest_hash == source_hash:
            continue

        previous_hash = str(previous_hashes.get(item.name) or "")
        safe_managed_update = bool(previous_hash and dest_hash == previous_hash)
        if not (revision_changed or safe_managed_update):
            # Same bundled revision but the file changed locally: preserve it.
            continue

        try:
            backup_dir.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                shutil.copy2(dest, backup_dir / dest.name)
            shutil.copy2(item, dest)
        except OSError:
            # Startup must remain possible even when product storage is locked.
            sync_complete = False
            continue

    try:
        _atomic_write_json(
            manifest_path,
            {
                "revision": (
                    BUNDLED_PRODUCT_SEED_REVISION
                    if sync_complete
                    else previous_revision
                ),
                "hashes": current_hashes,
                "deleted": sorted(deleted_names, key=str.casefold),
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
        )
    except OSError:
        pass


def get_protocol_dir() -> Path:
    """Return writable user product directory with versioned bundled defaults."""
    root = app_data_root()
    target = root / "products"
    target.mkdir(parents=True, exist_ok=True)
    _sync_bundled_products(root, target, resource_path("product"))
    return target


def crash_log_dir() -> Path:
    return logs_dir()



def _seed_bundled_user_defaults(root: Path) -> None:
    """Copy bundled initial mutable data without ever overwriting user files."""
    candidates = [resource_path("defaults/data"), resource_path("data")]
    source = next((path for path in candidates if path.exists() and path.is_dir()), None)
    if source is None:
        return
    destination = root / "data"
    try:
        destination.mkdir(parents=True, exist_ok=True)
        for item in source.rglob("*"):
            relative = item.relative_to(source)
            target = destination / relative
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif item.is_file() and not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
    except OSError:
        # Defaults are optional. Existing user data always takes priority.
        return

def _legacy_candidates() -> list[tuple[Path, str]]:
    candidates: list[tuple[Path, str]] = []
    try:
        project_root = Path(__file__).resolve().parent.parent
        candidates.extend([(project_root / "data", "data"), (project_root / "product", "products")])
    except Exception:
        pass
    try:
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).resolve().parent
            candidates.extend([(exe_dir / "data", "data"), (exe_dir / "product", "products")])
    except Exception:
        pass
    docs = Path.home() / "Documents" / "串口解析工具"
    candidates.extend([(docs / "data", "data"), (docs / "product", "products")])

    # Migrate data created by earlier installers/product names.
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        programs = Path(local_app_data) / "Programs"
        for legacy_name in ("串口数据解析", "SerialPortParser", "Super Max Serial Tool"):
            legacy_root = programs / legacy_name
            candidates.extend(
                [
                    (legacy_root / "data", "data"),
                    (legacy_root / "product", "products"),
                    (legacy_root / "products", "products"),
                ]
            )
    return candidates


def _migrate_legacy_data(root: Path) -> None:
    marker = root / ".legacy_migration_v1_done"
    if marker.exists():
        return
    try:
        for source, subdir in _legacy_candidates():
            if not source.exists() or not source.is_dir():
                continue
            destination = root / subdir
            destination.mkdir(parents=True, exist_ok=True)
            for item in source.iterdir():
                dest = destination / item.name
                if dest.exists():
                    continue
                try:
                    if item.is_dir():
                        shutil.copytree(item, dest)
                    elif item.is_file():
                        shutil.copy2(item, dest)
                except OSError:
                    continue
        marker.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
    except OSError:
        # Migration is best effort; it will be retried on a later start.
        pass


def write_crash_log(exc: BaseException) -> Path | None:
    """Write a timestamped crash report under LocalAppData/logs."""
    import traceback as _tb

    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = crash_log_dir() / f"crash_{ts}_{uuid.uuid4().hex[:8]}.log"
        tb_s = _tb.format_exc()
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"Time:       {datetime.now().isoformat(timespec='seconds')}\n")
            f.write(f"Frozen:     {getattr(sys, 'frozen', False)}\n")
            f.write(f"Executable: {sys.executable}\n")
            f.write(f"MEIPASS:    {getattr(sys, '_MEIPASS', '')}\n")
            f.write(f"CWD:        {os.getcwd()}\n")
            f.write(f"Argv:       {sys.argv}\n")
            f.write("\n========== Exception ==========\n")
            f.write(f"{type(exc).__module__}.{type(exc).__name__}: {exc}\n")
            f.write("\n========== Traceback ==========\n")
            f.write(tb_s)
            f.write("\n========== sys.path ==========\n")
            for item in sys.path:
                f.write(item + "\n")
        return path
    except Exception:
        return None
