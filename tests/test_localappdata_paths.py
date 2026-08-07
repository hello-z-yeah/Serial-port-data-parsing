from __future__ import annotations

import json
from pathlib import Path

import protocol_parser.paths as paths
from protocol_parser.app_info import APP_DATA_DIR_NAME


def test_windows_path_resolution_uses_localappdata(tmp_path: Path) -> None:
    root = paths._resolve_app_data_root(
        platform_name="nt",
        environ={"LOCALAPPDATA": str(tmp_path)},
        home=tmp_path / "home",
    )
    assert root == tmp_path / APP_DATA_DIR_NAME


def test_mutable_subdirectories_are_created_under_app_root(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / APP_DATA_DIR_NAME
    monkeypatch.setattr(paths, "_resolve_app_data_root", lambda **_kwargs: root)
    monkeypatch.setattr(paths, "_migrate_legacy_data", lambda _root: None)
    monkeypatch.setattr(paths, "resource_path", lambda _relative: tmp_path / "missing")

    assert paths.app_data_root() == root
    assert paths.user_data_path() == root / "data"
    assert paths.config_dir() == root / "config"
    assert paths.logs_dir() == root / "logs"
    assert paths.get_protocol_dir() == root / "products"
    assert all(path.is_dir() for path in (
        root,
        root / "data",
        root / "config",
        root / "logs",
        root / "products",
    ))


def test_bundled_mutable_defaults_are_seeded_without_overwrite(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "app"
    bundled = tmp_path / "bundle" / "defaults" / "data"
    (bundled / "cmdlib").mkdir(parents=True)
    (bundled / "cmdlib" / "hex_cmds.json").write_text("bundled", encoding="utf-8")

    monkeypatch.setattr(paths, "_resolve_app_data_root", lambda **_kwargs: root)
    monkeypatch.setattr(paths, "_migrate_legacy_data", lambda _root: None)
    monkeypatch.setattr(
        paths,
        "resource_path",
        lambda relative: tmp_path / "bundle" / relative,
    )

    assert paths.app_data_root() == root
    seeded = root / "data" / "cmdlib" / "hex_cmds.json"
    assert seeded.read_text(encoding="utf-8") == "bundled"

    seeded.write_text("user", encoding="utf-8")
    assert paths.app_data_root() == root
    assert seeded.read_text(encoding="utf-8") == "user"


def test_versioned_bundled_product_refresh_backs_up_stale_copy(tmp_path: Path) -> None:
    root = tmp_path / "app"
    target = root / "products"
    source = tmp_path / "bundle" / "product"
    target.mkdir(parents=True)
    source.mkdir(parents=True)

    bundled = source / "wise1.avamp.wise51.json"
    bundled.write_text('{"version": "new"}', encoding="utf-8")
    stale = target / bundled.name
    stale.write_text('{"version": "old"}', encoding="utf-8")

    paths._sync_bundled_products(root, target, source)

    assert stale.read_text(encoding="utf-8") == '{"version": "new"}'
    backups = list((target / "backups").rglob("wise1.avamp.wise51.json"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == '{"version": "old"}'
    manifest = root / "config" / "bundled_products_manifest.json"
    assert manifest.is_file()


def test_user_edit_is_preserved_within_same_bundled_revision(tmp_path: Path) -> None:
    root = tmp_path / "app"
    target = root / "products"
    source = tmp_path / "bundle" / "product"
    target.mkdir(parents=True)
    source.mkdir(parents=True)

    bundled = source / "wise1.avamp.wise51.json"
    bundled.write_text('{"version": "new"}', encoding="utf-8")
    paths._sync_bundled_products(root, target, source)

    destination = target / bundled.name
    destination.write_text('{"version": "user-edit"}', encoding="utf-8")
    paths._sync_bundled_products(root, target, source)

    assert destination.read_text(encoding="utf-8") == '{"version": "user-edit"}'


def test_deleted_bundled_product_is_not_seeded_again(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "app"
    target = root / "products"
    source = tmp_path / "bundle" / "product"
    target.mkdir(parents=True)
    source.mkdir(parents=True)

    bundled = source / "wise1.avamp.wise51.json"
    bundled.write_text('{"product": "wise"}', encoding="utf-8")
    paths._sync_bundled_products(root, target, source)
    destination = target / bundled.name
    assert destination.is_file()

    monkeypatch.setattr(paths, "_resolve_app_data_root", lambda **_kwargs: root)
    monkeypatch.setattr(paths, "_migrate_legacy_data", lambda _root: None)
    monkeypatch.setattr(paths, "_seed_bundled_user_defaults", lambda _root: None)

    paths.mark_product_json_deleted(destination.name)
    destination.unlink()
    paths._sync_bundled_products(root, target, source)

    assert not destination.exists()
    manifest = json.loads(
        (root / "config" / "bundled_products_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert destination.name in manifest["deleted"]


def test_explicit_restore_clears_bundled_product_deletion_marker(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "app"
    target = root / "products"
    source = tmp_path / "bundle" / "product"
    target.mkdir(parents=True)
    source.mkdir(parents=True)

    bundled = source / "wise1.avamp.wise51.json"
    bundled.write_text('{"product": "wise"}', encoding="utf-8")

    monkeypatch.setattr(paths, "_resolve_app_data_root", lambda **_kwargs: root)
    monkeypatch.setattr(paths, "_migrate_legacy_data", lambda _root: None)
    monkeypatch.setattr(paths, "_seed_bundled_user_defaults", lambda _root: None)

    paths.mark_product_json_deleted(bundled.name)
    paths._sync_bundled_products(root, target, source)
    assert not (target / bundled.name).exists()

    paths.clear_product_json_deleted(bundled.name)
    paths._sync_bundled_products(root, target, source)
    assert (target / bundled.name).is_file()


def test_retired_default_product_is_removed_when_untouched(tmp_path: Path) -> None:
    root = tmp_path / "app"
    target = root / "products"
    source = tmp_path / "bundle" / "product"
    target.mkdir(parents=True)
    source.mkdir(parents=True)

    fixture = Path("tests/fixtures/legacy_miot_product.json")
    destination = target / "wise1.avamp.wise51.json"
    destination.write_bytes(fixture.read_bytes())

    paths._sync_bundled_products(root, target, source)

    assert not destination.exists()
    backups = list((target / "backups").rglob(destination.name))
    assert len(backups) == 1
    assert backups[0].read_bytes() == fixture.read_bytes()


def test_retired_default_product_preserves_user_modified_copy(tmp_path: Path) -> None:
    root = tmp_path / "app"
    target = root / "products"
    source = tmp_path / "bundle" / "product"
    target.mkdir(parents=True)
    source.mkdir(parents=True)

    destination = target / "wise1.avamp.wise51.json"
    destination.write_text('{"product": "user-customized"}', encoding="utf-8")

    paths._sync_bundled_products(root, target, source)

    assert destination.is_file()
    assert destination.read_text(encoding="utf-8") == '{"product": "user-customized"}'


def test_retired_default_is_not_shipped_in_product_directory() -> None:
    assert not Path("product/wise1.avamp.wise51.json").exists()
