"""角色感知的自动回复引擎。"""
from __future__ import annotations

from dataclasses import dataclass
import json
from types import SimpleNamespace
from typing import Callable, Any

from .parser import parse_attr_payload_fields


@dataclass
class ReplyRule:
    name: str
    description: str
    action: Callable[[Any, Any], bytes | list[bytes] | tuple[bytes, ...] | None]
    enabled: bool = True


class AutoReplyEngine:
    def __init__(
        self,
        collector,
        cmd_engine,
        attr_center,
        on_error: Callable[[str], None] | None = None,
        on_before_send: Callable[[int, str], None] | None = None,
    ):
        self._collector = collector
        self._cmd = cmd_engine
        self._ac = attr_center
        self._on_error = on_error
        self._on_before_send = on_before_send
        self._enabled = False
        self._role = "mcu"
        self._rules: dict[int, ReplyRule] = {}
        self._low_power_active = False
        self._last_applied_attrids: list[int] = []
        self._register_mcu_rules()

    def set_collector(self, collector) -> None:
        if collector is not self._collector:
            self.reset_state()
        self._collector = collector

    def set_before_send(self, callback: Callable[[int, str], None] | None) -> None:
        self._on_before_send = callback

    def set_role(self, role: str) -> None:
        self.reset_state()
        self._role = "module" if str(role).lower() == "module" else "mcu"
        if self._role == "mcu":
            self._register_mcu_rules(preserve_enabled=True)
        else:
            self._rules = {}

    def enable(self, enable: bool, *, enable_all_rules: bool = False) -> None:
        new_value = bool(enable)
        if new_value != self._enabled:
            self.reset_state()
        self._enabled = new_value
        if self._enabled and enable_all_rules:
            self.enable_all_rules()

    def enable_all_rules(self) -> None:
        """Enable every registered MCU reply rule.

        The global UI switch means "enable automatic replies" to users.  A
        previously unchecked per-command rule must not silently survive a new
        global enable and make only part of the protocol stop responding.
        Users may still disable individual rows afterwards.
        """
        for rule in self._rules.values():
            rule.enabled = True

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def role(self) -> str:
        return self._role

    @property
    def rules(self) -> dict[int, ReplyRule]:
        return self._rules

    @property
    def low_power_active(self) -> bool:
        return self._low_power_active

    @property
    def last_applied_attrids(self) -> list[int]:
        """Internal attribute IDs changed by the most recent handled command."""
        return list(self._last_applied_attrids)

    def wake(self) -> bytes:
        """IO 唤醒：重置心跳计数器，返回首次心跳回复(0x00)"""
        self._ac.reset_heartbeat_counter()
        self._low_power_active = False
        return self._cmd.build_heartbeat_reset()

    def reset_state(self) -> None:
        """Reset connection/product scoped automatic-reply state."""
        self._low_power_active = False
        self._last_applied_attrids = []
        self._ac.reset_heartbeat_counter()

    def set_rule_enabled(self, cmd_code: int, enabled: bool) -> None:
        rule = self._rules.get(int(cmd_code) & 0xFF)
        if rule is not None:
            rule.enabled = bool(enabled)

    def _register_mcu_rules(self, preserve_enabled: bool = False) -> None:
        old = {code: rule.enabled for code, rule in self._rules.items()} if preserve_enabled else {}
        definitions = {
            0x20: ("回复心跳", "模拟MCU：回复模组心跳；首次00，后续01", self._reply_heartbeat),
            0x21: ("回复设备信息", "根据PID、Model及属性映射回复设备信息", self._reply_dev_info),
            0x22: ("回复收到模组状态", "回应已收到模组工作状态", self._reply_module_status_ack),
            0x24: ("回复快照请求", "用实时属性中心的当前值回复设备快照", self._reply_snapshot),
            0x03: ("回复属性查询", "回复模组查询的指定可读属性", self._reply_get_attrs),
            0x01: ("自动回复收到模组命令", "回复消息ID，并仅上报本次命令涉及的可上报属性", self._reply_cmd_dispatch),
            0x12: ("MCU回复模组Action", "回复模组下发的Action命令", self._reply_action),
            0x60: ("回复收到模组产测状态", "回应已收到模组产测状态", self._reply_prod_test_ack),
        }
        self._rules = {
            code: ReplyRule(name, desc, action, old.get(code, True))
            for code, (name, desc, action) in definitions.items()
        }

    @staticmethod
    def _cmd_int(result) -> int:
        raw = getattr(result, "cmd_code", 0)
        if isinstance(raw, int):
            return raw & 0xFF
        try:
            return int(str(raw), 16) if str(raw).lower().startswith("0x") else int(raw)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _extract_msg_id(result) -> int:
        for field_obj in getattr(result, "fields", None) or []:
            if not isinstance(field_obj, dict):
                continue
            name = str(field_obj.get("name") or "").lower().replace("_", "")
            if name in ("消息id", "消息id", "msgid", "messageid"):
                try:
                    return int(field_obj.get("value", 0)) & 0xFF
                except (TypeError, ValueError):
                    return 0
        return 0

    def _warn(self, message: str) -> None:
        if self._on_error is not None:
            try:
                self._on_error(message)
            except Exception:
                pass

    def on_frame(self, result, frame, ts: float) -> int:
        del ts
        self._last_applied_attrids = []
        if not self._enabled or self._role != "mcu" or self._collector is None:
            return 0
        cmd_int = self._cmd_int(result)
        # 低功耗模式下跳过心跳回复（协议要求低功耗时停止心跳）
        if cmd_int == 0x20 and self._low_power_active:
            return 0
        rule = self._rules.get(cmd_int)
        if rule is None:
            if cmd_int == 0x01:
                self._warn("收到命令下发，但当前产品没有注册 0x01 自动回复规则")
            return 0
        if not rule.enabled:
            if cmd_int == 0x01:
                self._warn("收到命令下发，但 0x01 自动回复规则已关闭")
            return 0
        direction = str(getattr(result, "direction", "") or "")
        if direction and "MCU→模组" in direction:
            return 0
        try:
            payloads = rule.action(result, frame)
            if payloads is None:
                return 0
            if isinstance(payloads, (bytes, bytearray)):
                payload_list = [bytes(payloads)]
            else:
                payload_list = [bytes(p) for p in payloads if p]
            if not payload_list:
                return 0
            for payload in payload_list:
                try:
                    self._collector.send(
                        payload,
                        metadata={"auto_reply": True, "rule_name": rule.name},
                    )
                except TypeError as exc:
                    # Backward compatibility for lightweight collectors used by
                    # integrations/tests that implement send(data) only.
                    if "metadata" not in str(exc) and "unexpected keyword" not in str(exc):
                        raise
                    self._collector.send(payload)
                if self._on_before_send is not None:
                    try:
                        self._on_before_send(1, rule.name)
                    except Exception:
                        pass
            return len(payload_list)
        except Exception as exc:  # 自动回复失败不能中断串口接收线程
            self._warn(f"自动回复失败（{rule.name}）：{exc}")
            return 0

    def _reply_heartbeat(self, result, frame) -> bytes:
        del result, frame
        count = self._ac.increment_heartbeat()
        first = count == 1
        return self._cmd.build_heartbeat_resp(reset_flag=first)

    def _reply_dev_info(self, result, frame) -> bytes:
        del result, frame
        return self._cmd.build_dev_info_resp()

    def _reply_module_status_ack(self, result, frame) -> bytes:
        del frame
        # 状态 5 表示进入低功耗；收到任何其他明确状态时必须退出低功耗，
        # 否则心跳会被永久静默。
        status_value: int | None = None
        for field_obj in getattr(result, "fields", None) or []:
            if not isinstance(field_obj, dict):
                continue
            if field_obj.get("name") in ("模组工作状态", "module_status"):
                val = field_obj.get("value")
                if isinstance(val, int):
                    status_value = val
                    break
        if status_value is not None:
            self._low_power_active = status_value == 5
        return self._cmd.build_module_status_ack()

    def _reply_snapshot(self, result, frame) -> bytes:
        del result, frame
        return self._cmd.build_snapshot_resp()

    def _reply_get_attrs(self, result, frame) -> bytes:
        del frame
        msg_id = self._extract_msg_id(result)
        requested = self._ac.get_frame_attrids(result)
        unknown = self._ac.get_unknown_frame_attrids(result)
        if unknown:
            self._warn(
                "属性查询包含未知属性："
                + "、".join(f"0x{aid:02X}" for aid in unknown)
            )
        readable = [
            aid for aid in requested
            if (entry := self._ac.get_entry(aid)) is not None and entry.access != "只写"
        ]
        return self._cmd.build_get_attr_resp(msg_id, readable)

    def _reply_cmd_dispatch(self, result, frame) -> list[bytes]:
        """Validate a module write command before acknowledging it.

        Rules:
        - read/write and write-only attributes may be changed;
        - read-only attributes are ignored and never reported;
        - write-only attributes are accepted but never included in status reports;
        - unknown attributes or product-invalid values reject the whole command,
          so the simulator never sends a success ACK for a command it could not
          execute completely;
        - repeated same-value writes still trigger a single-attribute status
          synchronization for every read/write attribute carried by the command.
        """
        msg_id = self._extract_msg_id(result)
        records = self._ac.get_frame_attr_records(result)

        # Recovery path for old/custom command definitions that parsed only the
        # leading message id and did not expose the following attr list.  The
        # raw frame still follows V3 ``msg_id + attr_list`` and is decoded with
        # the exact same parser used by the normal receive path.
        if not records:
            raw_data = bytes(getattr(frame, "data", b"") or b"")
            if len(raw_data) > 1:
                try:
                    fallback_fields = parse_attr_payload_fields(
                        raw_data[1:], self._ac.cfg, force_report=False
                    )
                    fallback_result = SimpleNamespace(fields=fallback_fields)
                    records = self._ac.get_frame_attr_records(fallback_result)

                    # 部分历史/自定义产品把 0x01 request 错配成仅解析 msg_id。
                    # 恢复出的属性字段不仅用于自动回复，也补回原 ParseResult，
                    # 这样实时数据窗口能显示“命令涉及了哪个 serialId/属性”，
                    # 不再只有消息 ID 而看不到控制内容。
                    if fallback_fields:
                        original_fields = list(getattr(result, "fields", None) or [])
                        original_fields.extend(fallback_fields)
                        try:
                            result.fields = original_fields
                        except Exception:
                            pass
                except Exception as exc:
                    self._warn(f"命令下发属性恢复解析失败：{exc}")

        if not records:
            self._warn("命令下发未包含可解析的属性，未回复成功消息 ID")
            return []

        unknown: list[int] = []
        read_only: list[int] = []
        writable_order: list[int] = []
        validated_values: dict[int, Any] = {}
        invalid: list[str] = []

        for wire_id, internal_id, raw_value in records:
            if internal_id is None:
                if wire_id not in unknown:
                    unknown.append(wire_id)
                continue

            entry = self._ac.get_entry(internal_id)
            if entry is None:
                if wire_id not in unknown:
                    unknown.append(wire_id)
                continue

            if entry.access not in ("读写", "只写"):
                if internal_id not in read_only:
                    read_only.append(internal_id)
                continue

            try:
                normalized = self._ac.validate_attr_value(internal_id, raw_value)
            except ValueError as exc:
                invalid.append(str(exc))
                continue

            if internal_id not in writable_order:
                writable_order.append(internal_id)
            validated_values[internal_id] = normalized

        if unknown or invalid:
            parts: list[str] = []
            if unknown:
                parts.append(
                    "未知属性 " + "、".join(f"0x{aid:02X}" for aid in unknown)
                )
            if invalid:
                parts.append("；".join(invalid))
            self._warn(
                "命令下发校验失败，整帧未执行且未回复成功消息 ID："
                + "；".join(parts)
            )
            return []

        if read_only:
            self._warn(
                "命令下发试图修改只读属性，已忽略："
                + "、".join(f"0x{aid:02X}" for aid in read_only)
            )

        # 先完成全部合法写入，再生成状态同步，避免上报旧值。set_attr_value
        # 会再次校验，作为属性中心的最终防线。
        for attrid in writable_order:
            self._ac.set_attr_value(attrid, validated_values[attrid])
        self._last_applied_attrids = list(writable_order)

        replies: list[bytes] = [self._cmd.build_cmd_ack_resp(msg_id)]
        reportable = [
            attrid
            for attrid in writable_order
            if (entry := self._ac.get_entry(attrid)) is not None
            and entry.access == "读写"
        ]
        if reportable:
            replies.append(
                self._cmd.build_attr_report(reportable, validated_values)
            )
        return replies

    @staticmethod
    def _extract_action_id(result) -> int:
        for field_obj in getattr(result, "fields", None) or []:
            if not isinstance(field_obj, dict):
                continue
            name = str(field_obj.get("name") or "").lower().replace("_", "")
            if "action" in name or "行为" in name:
                try:
                    return int(field_obj.get("value", 0)) & 0xFF
                except (TypeError, ValueError):
                    return 0
        return 0

    def _action_outputs(self, action_id: int) -> list[tuple[int, Any, int]]:
        """Resolve optional Action output parameters from imported JSON.

        Products without ActionEvent/output metadata correctly return an empty
        output list.  Several common key spellings are accepted so future JSON
        imports do not require per-product hard-coding.
        """
        cfg = self._ac.cfg or {}
        raw = cfg.get("source_function_json")
        try:
            source = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception:
            source = {}
        actions = (
            cfg.get("actions")
            or cfg.get("ActionEvent")
            or (source.get("ActionEvent") if isinstance(source, dict) else None)
            or (source.get("actions") if isinstance(source, dict) else None)
            or []
        )
        if isinstance(actions, dict):
            actions = list(actions.values())
        target = None
        for item in actions if isinstance(actions, list) else []:
            if not isinstance(item, dict):
                continue
            raw_id = item.get("serialId", item.get("actionId", item.get("iid", item.get("id"))))
            try:
                resolved = int(str(raw_id), 16) if str(raw_id).lower().startswith("0x") else int(raw_id)
            except (TypeError, ValueError):
                continue
            if (resolved & 0xFF) == (action_id & 0xFF):
                target = item
                break
        if not isinstance(target, dict):
            return []
        outputs = (
            target.get("outParams")
            or target.get("outputParams")
            or target.get("outputs")
            or target.get("output")
            or []
        )
        if isinstance(outputs, dict):
            outputs = list(outputs.values())
        result: list[tuple[int, Any, int]] = []
        for item in outputs if isinstance(outputs, list) else []:
            if not isinstance(item, dict):
                continue
            raw_id = item.get("serialId", item.get("attrid", item.get("iid", item.get("id"))))
            try:
                attrid = int(str(raw_id), 16) if str(raw_id).lower().startswith("0x") else int(raw_id)
            except (TypeError, ValueError):
                continue
            entry = self._ac.get_entry(attrid)
            if entry is not None:
                value = item.get("value", item.get("default", entry.current_value))
                try:
                    value = self._ac.validate_attr_value(attrid, value)
                except ValueError:
                    value = entry.current_value
                result.append((attrid, value, entry.typeid))
                continue
            try:
                typeid = int(item.get("type", item.get("typeid", 2)))
            except (TypeError, ValueError):
                typeid = 2
            value = item.get("value", item.get("default", 0))
            result.append((attrid & 0xFF, value, typeid))
        return result

    def _reply_action(self, result, frame) -> bytes:
        del frame
        action_id = self._extract_action_id(result)
        return self._cmd.build_action_resp(
            self._extract_msg_id(result), action_id, self._action_outputs(action_id)
        )

    def _reply_prod_test_ack(self, result, frame) -> bytes:
        del result, frame
        return self._cmd.build_prod_test_ack()
