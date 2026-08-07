from __future__ import annotations

import json
from pathlib import Path

import pytest

import protocol_parser.session_snapshot as snapshots
from protocol_parser.exceptions import SnapshotError
from protocol_parser.session_snapshot import SessionSnapshot


def test_snapshot_round_trip_is_atomic_and_leaves_no_temp_file(tmp_path: Path) -> None:
    target = tmp_path / "session.json"
    snap = SessionSnapshot(port="COM9", baudrate=115200, extras={"mode": "mcu"})
    assert snapshots.save_snapshot(snap, target) == target
    loaded = snapshots.load_snapshot(target)
    assert loaded is not None
    assert loaded.port == "COM9"
    assert loaded.baudrate == 115200
    assert loaded.extras == {"mode": "mcu"}
    assert not list(tmp_path.glob(".*.tmp"))


def test_corrupt_snapshot_is_quarantined_not_deleted(tmp_path: Path) -> None:
    target = tmp_path / "session.json"
    target.write_text("{broken", encoding="utf-8")
    assert snapshots.load_snapshot(target) is None
    assert not target.exists()
    quarantined = list(tmp_path.glob("session.corrupt.*.json"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == "{broken"


def test_replace_failure_preserves_previous_snapshot_and_removes_temp(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "session.json"
    old_payload = {"port": "COM_OLD"}
    target.write_text(json.dumps(old_payload), encoding="utf-8")

    def fail_replace(_src, _dst):
        raise OSError("replace denied")

    monkeypatch.setattr(snapshots.os, "replace", fail_replace)
    with pytest.raises(SnapshotError, match="保存会话快照失败"):
        snapshots.save_snapshot(SessionSnapshot(port="COM_NEW"), target)

    assert json.loads(target.read_text(encoding="utf-8")) == old_payload
    assert not list(tmp_path.glob(".*.tmp"))


def test_snapshot_io_failure_is_not_misclassified_as_corruption(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "session.json"
    target.write_text("{}", encoding="utf-8")
    real_open = open

    def deny_open(path, *args, **kwargs):
        if Path(path) == target:
            raise PermissionError("denied")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(snapshots, "open", deny_open, raising=False)
    with pytest.raises(SnapshotError, match="读取会话快照失败"):
        snapshots.load_snapshot(target)
    assert target.exists()
    assert not list(tmp_path.glob("session.corrupt.*.json"))
