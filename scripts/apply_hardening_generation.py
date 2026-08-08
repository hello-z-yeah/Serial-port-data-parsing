#!/usr/bin/env python3
"""Surgical apply of generation / auto_reply / gui / ui_helpers hardening.

Idempotent. Run from repo root on fix/hardening-p0-p1:

    python scripts/apply_hardening_generation.py

Prerequisites (Windows / any OS):
    git checkout d288572 -- protocol_parser/attr_center.py protocol_parser/auto_reply.py protocol_parser/gui.py protocol_parser/ui_helpers.py

Then run this script. storage.py and the new tests are already correct on the branch.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _must_contain(text: str, anchor: str, label: str) -> None:
    if anchor not in text:
        raise SystemExit(f"[FAIL] {label}: expected anchor not found — file may already be patched or wrong base")


def patch_attr_center() -> None:
    p = ROOT / "protocol_parser" / "attr_center.py"
    text = p.read_text(encoding="utf-8")
    if "self._generation = 0" in text and "expected_generation" in text:
        print("attr_center.py: already patched, skip")
        return

    old = """        self._lock = RLock()
        self._heartbeat_count = 0
        self.load_warnings: list[str] = []
"""
    new = """        self._lock = RLock()
        self._heartbeat_count = 0
        self._generation = 0
        self.load_warnings: list[str] = []
"""
    _must_contain(text, old, "attr_center __init__")
    text = text.replace(old, new, 1)

    old = """                self.cfg = target_cfg
                self.load_warnings = warnings
                self._heartbeat_count = 0

            except Exception:
"""
    new = """                self.cfg = target_cfg
                self.load_warnings = warnings
                self._heartbeat_count = 0
                # 产品/协议切换代数：异步回调必须携带捕获时的 generation；
                # 若小于当前值则视为过期帧，丢弃写入，防止旧协议 ID 污染新状态。
                self._generation += 1

            except Exception:
"""
    _must_contain(text, old, "attr_center load_product")
    text = text.replace(old, new, 1)

    old = """    def update_from_frame(self, result) -> list[int]:
        \"\"\"从 ParseResult 更新属性，返回发生变化的 attrid。\"\"\"
        if self._cmd_int(result) not in (0x10, 0x01, 0x24, 0x03):
            return []
        changed: list[int] = []
        cmd_int = self._cmd_int(result)
        with self._lock:
            for _, internal_id, raw_value in self._frame_attr_records(result):
"""
    new = """    def update_from_frame(self, result, *, expected_generation: int | None = None) -> list[int]:
        \"\"\"从 ParseResult 更新属性，返回发生变化的 attrid。

        expected_generation: 若由异步回调传入且与当前 generation 不一致，则整帧丢弃，
        防止跨产品切换时的幽灵回调把旧协议 Attribute ID 写入新状态中心。
        \"\"\"
        if self._cmd_int(result) not in (0x10, 0x01, 0x24, 0x03):
            return []
        changed: list[int] = []
        cmd_int = self._cmd_int(result)
        with self._lock:
            if expected_generation is not None and expected_generation != self._generation:
                return []
            for _, internal_id, raw_value in self._frame_attr_records(result):
"""
    _must_contain(text, old, "attr_center update_from_frame")
    text = text.replace(old, new, 1)

    old = """    def set_attr_value(self, attrid: int, value: Any) -> None:
        with self._lock:
            entry = self._attrs.get(int(attrid))
            if entry is not None:
                entry.current_value = self.validate_attr_value(attrid, value)
"""
    new = """    def set_attr_value(self, attrid: int, value: Any, *, expected_generation: int | None = None) -> None:
        with self._lock:
            if expected_generation is not None and expected_generation != self._generation:
                return
            entry = self._attrs.get(int(attrid))
            if entry is not None:
                entry.current_value = self.validate_attr_value(attrid, value)
"""
    _must_contain(text, old, "attr_center set_attr_value")
    text = text.replace(old, new, 1)

    old = """    def apply_values_atomic(self, values: dict[int, Any]) -> dict[int, Any]:
        \"\"\"在同一把锁下校验并写入多个属性，保证无中间态可见。

        返回被修改属性的旧值快照，供调用方在后续失败时调用
        :meth:`restore_values` 完整回滚。任一属性校验失败时，**不会**
        修改任何属性，异常直接抛出。
        \"\"\"
        if not values:
            return {}
        with self._lock:
            normalized: dict[int, Any] = {}
"""
    new = """    def apply_values_atomic(
        self,
        values: dict[int, Any],
        *,
        expected_generation: int | None = None,
    ) -> dict[int, Any]:
        \"\"\"在同一把锁下校验并写入多个属性，保证无中间态可见。

        返回被修改属性的旧值快照，供调用方在后续失败时调用
        :meth:`restore_values` 完整回滚。任一属性校验失败时，**不会**
        修改任何属性，异常直接抛出。

        expected_generation: 若由异步路径传入且与当前 generation 不一致，则丢弃写入并返回空快照。
        \"\"\"
        if not values:
            return {}
        with self._lock:
            if expected_generation is not None and expected_generation != self._generation:
                return {}
            normalized: dict[int, Any] = {}
"""
    _must_contain(text, old, "attr_center apply_values_atomic")
    text = text.replace(old, new, 1)

    old = """    @property
    def heartbeat_count(self) -> int:
        with self._lock:
            return self._heartbeat_count
"""
    new = """    @property
    def generation(self) -> int:
        \"\"\"Monotonic product/protocol generation. Bumped on successful load_product.\"\"\"
        with self._lock:
            return self._generation

    @property
    def heartbeat_count(self) -> int:
        with self._lock:
            return self._heartbeat_count
"""
    _must_contain(text, old, "attr_center generation property")
    text = text.replace(old, new, 1)

    p.write_text(text, encoding="utf-8")
    print("attr_center.py: patched")


def patch_auto_reply() -> None:
    p = ROOT / "protocol_parser" / "auto_reply.py"
    text = p.read_text(encoding="utf-8")
    if "expected_generation=expected_gen" in text and "仅在组包完全成功后才提交 applied 缓存" in text:
        print("auto_reply.py: already patched, skip")
        return

    old = """        # 原子批量写入（AttrStateCenter 事务接口）；测试用假对象回退到顺序写入
        values_to_write = {aid: validated_values[aid] for aid in writable_order}
        try:
            if hasattr(self._ac, \"apply_values_atomic\"):
                old_values = self._ac.apply_values_atomic(values_to_write)
            else:
                old_values = {}
                for attrid, value in values_to_write.items():
                    entry = self._ac.get_entry(attrid)
                    if entry is not None:
                        old_values[attrid] = getattr(entry, \"current_value\", None)
                    self._ac.set_attr_value(attrid, value)
        except _VALIDATION_ERRORS as exc:
            self._warn(f\"命令下发写入校验失败，整帧未执行且未回复成功消息 ID：{exc}\")
            return []
        except Exception as exc:
            _log.exception(\"命令下发原子写入内部异常\")
            self._warn(f\"命令下发写入失败，整帧未执行且未回复成功消息 ID：{exc}\")
            return []

        self._last_applied_attrids = list(writable_order)

        try:
            replies: list[bytes] = [self._cmd.build_cmd_ack_resp(msg_id)]
            reportable = [
                attrid
                for attrid in writable_order
                if (entry := self._ac.get_entry(attrid)) is not None
                and entry.access == \"读写\"
            ]
            if reportable:
                replies.append(
                    self._cmd.build_attr_report(reportable, validated_values)
                )
            return replies
        except Exception as exc:
"""
    new = """        # 原子批量写入（AttrStateCenter 事务接口）；测试用假对象回退到顺序写入
        values_to_write = {aid: validated_values[aid] for aid in writable_order}
        expected_gen = getattr(self._ac, \"generation\", None)
        try:
            if hasattr(self._ac, \"apply_values_atomic\"):
                # 兼容旧 mock / 无 generation 参数的测试替身
                try:
                    old_values = self._ac.apply_values_atomic(
                        values_to_write, expected_generation=expected_gen
                    )
                except TypeError:
                    old_values = self._ac.apply_values_atomic(values_to_write)
            else:
                old_values = {}
                for attrid, value in values_to_write.items():
                    entry = self._ac.get_entry(attrid)
                    if entry is not None:
                        old_values[attrid] = getattr(entry, \"current_value\", None)
                    self._ac.set_attr_value(attrid, value)
        except _VALIDATION_ERRORS as exc:
            self._warn(f\"命令下发写入校验失败，整帧未执行且未回复成功消息 ID：{exc}\")
            return []
        except Exception as exc:
            _log.exception(\"命令下发原子写入内部异常\")
            self._warn(f\"命令下发写入失败，整帧未执行且未回复成功消息 ID：{exc}\")
            return []

        # 若因 generation 过期导致空快照，视为整帧丢弃
        if not old_values and values_to_write:
            return []

        try:
            replies: list[bytes] = [self._cmd.build_cmd_ack_resp(msg_id)]
            reportable = [
                attrid
                for attrid in writable_order
                if (entry := self._ac.get_entry(attrid)) is not None
                and entry.access == \"读写\"
            ]
            if reportable:
                replies.append(
                    self._cmd.build_attr_report(reportable, validated_values)
                )
            # 仅在组包完全成功后才提交 applied 缓存，与事务提交点对齐
            self._last_applied_attrids = list(writable_order)
            return replies
        except Exception as exc:
"""
    _must_contain(text, old, "auto_reply apply block")
    text = text.replace(old, new, 1)
    p.write_text(text, encoding="utf-8")
    print("auto_reply.py: patched")


def patch_gui() -> None:
    p = ROOT / "protocol_parser" / "gui.py"
    text = p.read_text(encoding="utf-8")
    if "attr_gen = self._attr_center.generation" in text:
        print("gui.py: already patched, skip")
        return

    old = """            if generation != self._collector_generation or not mcu_enabled:
                return
            try:
                changed: list[int] = []
                cmd_int = -1

                # If a legacy/custom product command definition fails to parse
"""
    new = """            if generation != self._collector_generation or not mcu_enabled:
                return
            try:
                # 捕获本回调时刻的 AttrStateCenter generation。
                # 若产品在回调执行期间被切换，写入会因 generation 不一致被丢弃。
                attr_gen = self._attr_center.generation
                changed: list[int] = []
                cmd_int = -1

                # If a legacy/custom product command definition fails to parse
"""
    _must_contain(text, old, "gui on_mcu_frame start")
    text = text.replace(old, new, 1)

    old = """                            # 自动回复关闭时仍应把合法的模组写命令同步到属性中心；
                            # 旧逻辑完全跳过，导致实时属性永远不更新。
                            changed = self._attr_center.update_from_frame(result)
"""
    new = """                            # 自动回复关闭时仍应把合法的模组写命令同步到属性中心；
                            # 旧逻辑完全跳过，导致实时属性永远不更新。
                            changed = self._attr_center.update_from_frame(
                                result, expected_generation=attr_gen
                            )
"""
    _must_contain(text, old, "gui 0x01 update_from_frame")
    text = text.replace(old, new, 1)

    old = """                    else:
                        changed = self._attr_center.update_from_frame(result)
                        if self._auto_reply.enabled:
                            self._auto_reply.on_frame(result, frame, ts)
"""
    new = """                    else:
                        changed = self._attr_center.update_from_frame(
                            result, expected_generation=attr_gen
                        )
                        if self._auto_reply.enabled:
                            self._auto_reply.on_frame(result, frame, ts)
"""
    _must_contain(text, old, "gui non-0x01 update_from_frame")
    text = text.replace(old, new, 1)

    p.write_text(text, encoding="utf-8")
    print("gui.py: patched")


def patch_ui_helpers() -> None:
    p = ROOT / "protocol_parser" / "ui_helpers.py"
    text = p.read_text(encoding="utf-8")
    if "structure error (malformed protocol?)" in text:
        print("ui_helpers.py: already patched, skip")
        return

    old = """        if callable(resolver):
            try:
                internal_id = resolver(wire_id)
            except Exception:
                _log.debug(\"resolve_wire_attrid failed for wire_id=%s\", wire_id, exc_info=True)
                internal_id = None
"""
    new = """        if callable(resolver):
            try:
                internal_id = resolver(wire_id)
            except (AttributeError, KeyError):
                _log.exception(
                    \"resolve_wire_attrid structure error (malformed protocol?) wire_id=%s\",
                    wire_id,
                )
                internal_id = None
            except Exception:
                _log.debug(\"resolve_wire_attrid failed for wire_id=%s\", wire_id, exc_info=True)
                internal_id = None
"""
    _must_contain(text, old, "ui_helpers resolver")
    text = text.replace(old, new, 1)

    old = """        try:
            entry = getter(internal_id)
        except Exception:
            _log.debug(\"get_entry failed for internal_id=%s\", internal_id, exc_info=True)
            entry = None
"""
    new = """        try:
            entry = getter(internal_id)
        except (AttributeError, KeyError):
            _log.exception(
                \"get_entry structure error (malformed protocol?) internal_id=%s\",
                internal_id,
            )
            entry = None
        except Exception:
            _log.debug(\"get_entry failed for internal_id=%s\", internal_id, exc_info=True)
            entry = None
"""
    _must_contain(text, old, "ui_helpers get_entry")
    text = text.replace(old, new, 1)

    old = """            else:
                data_shown = str(data_val)
        except Exception:
            data_shown = str(data_val)
"""
    new = """            else:
                data_shown = str(data_val)
        except (AttributeError, KeyError):
            _log.exception(
                \"_format_attr_semantics fatal structure error while formatting Data\"
            )
            data_shown = str(data_val)
        except Exception:
            data_shown = str(data_val)
"""
    _must_contain(text, old, "ui_helpers data_shown")
    text = text.replace(old, new, 1)

    p.write_text(text, encoding="utf-8")
    print("ui_helpers.py: patched")


def main() -> int:
    print("Applying generation / lifecycle hardening (surgical)...")
    try:
        patch_attr_center()
        patch_auto_reply()
        patch_gui()
        patch_ui_helpers()
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 1
    print("Done. Verify with:")
    print("  pytest tests/test_generation_and_error_oneshot.py -q")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
