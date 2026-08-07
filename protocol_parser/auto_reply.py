"""角色感知的自动回复引擎。"""
from __future__ import annotations

from dataclasses import dataclass
import inspect
import json
import logging
import threading
from types import SimpleNamespace
from typing import Callable, Any

from .exceptions import AttributeValidationError, ValidationError
from .parser import parse_attr_payload_fields

_log = logging.getLogger(__name__)

# 协议/产品校验类异常：应归一化为“拒绝本帧”，不应当作引擎崩溃。
_VALIDATION_ERRORS = (AttributeValidationError, ValidationError, ValueError, TypeError)


def _require_u8(value: Any, label: str = "值") -> int:
    """严格单字节边界：拒绝隐式 & 0xFF 截断导致的指令撞车。"""
    try:
        iv = int(value)
    except (TypeError, ValueError) as exc:
        raise AttributeValidationError(f"{label} 不是整数：{value!r}") from exc
    if not 0 <= iv <= 0xFF:
        raise AttributeValidationError(
            f"{label} 超出单字节范围 0–255：{iv}"
        )
    return iv


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
        # 保护 enable/rules/role/low_power 等跨线程共享状态；
        # on_frame 在串口线程，enable/set_role 在 GUI 线程。
        self._lock = threading.RLock()
        self._send_supports_metadata = self._detect_send_metadata(collector)
        self._register_mcu_rules()

    def set_collector(self, collector) -> None:
        with self._lock:
            if collector is not self._collector:
                self.reset_state()
            self._collector = collector
            self._send_supports_metadata = self._detect_send_metadata(collector)

    @staticmethod
    def _detect_send_metadata(collector) -> bool:
        """缓存 collector.send 是否接受 metadata 参数，避免运行时 TypeError 文本匹配。"""
        if collector is None:
            return False
        send = getattr(collector, "send", None)
        if not callable(send):
            return False
        try:
            sig = inspect.signature(send)
        except (TypeError, ValueError):
            # 内置/C 扩展等无法取签名时，保守假设不支持 keyword
            return False
        params = sig.parameters
        if "metadata" in params:
            return True
        # 存在 **kwargs 时也允许传入 metadata
        return any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
        )

    def set_before_send(self, callback: Callable[[int, str], None] | None) -> None:
        with self._lock:
            self._on_before_send = callback

    def set_role(self, role: str) -> None:
        with self._lock:
            self.reset_state()
            self._role = "module" if str(role).lower() == "module" else "mcu"
            if self._role == "mcu":
                self._register_mcu_rules(preserve_enabled=True)
            else:
                self._rules = {}

    def enable(self, enable: bool, *, enable_all_rules: bool = False) -> None:
        with self._lock:
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
        with self._lock:
            for rule in self._rules.values():
                rule.enabled = True

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    @property
    def role(self) -> str:
        with self._lock:
            return self._role

    @property
    def rules(self) -> dict[int, ReplyRule]:
        with self._lock:
            return dict(self._rules)

    @property
    def low_power_active(self) -> bool:
        with self._lock:
            return self._low_power_active

    @property
    def last_applied_attrids(self) -> list[int]:
        """Internal attribute IDs changed by the most recent handled command."""
        with self._lock:
            return list(self._last_applied_attrids)

    def wake(self) -> bytes:
        """IO 唤醒：重置心跳计数器，返回首次心跳回复(0x00)"""
        with self._lock:
            self._ac.reset_heartbeat_counter()
            self._low_power_active = False
            return self._cmd.build_heartbeat_reset()

    def reset_state(self) -> None:
        """Reset connection/product scoped automatic-reply state."""
        # 允许在已持有 self._lock 时调用（RLock 可重入）
        with self._lock:
            self._low_power_active = False
            self._last_applied_attrids = []
            self._ac.reset_heartbeat_counter()

    def set_rule_enabled(self, cmd_code: int, enabled: bool) -> None:
        with self._lock:
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
                    return _require_u8(field_obj.get("value", 0), "消息ID")
                except AttributeValidationError:
                    return 0
        return 0

    def _warn(self, message: str) -> None:
        if self._on_error is not None:
            try:
                self._on_error(message)
            except Exception:
                _log.exception("auto_reply on_error callback raised")

    def on_frame(self, result, frame, ts: float) -> int:
        del ts
        # 决策与组包在锁内，实际 send 在锁外，避免与 GUI/采集线程互相阻塞。
        with self._lock:
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
            rule_name = rule.name
            collector = self._collector
            before_send = self._on_before_send
            supports_metadata = self._send_supports_metadata
            try:
                payloads = rule.action(result, frame)
            except _VALIDATION_ERRORS as exc:
                self._warn(f"自动回复校验失败（{rule_name}）：{exc}")
                return 0
            except Exception as exc:
                # 非预期异常：记完整 traceback，仍不打断串口接收线程
                _log.exception("自动回复内部异常（%s）", rule_name)
                self._warn(f"自动回复失败（{rule_name}）：{exc}")
                return 0
            if payloads is None:
                return 0
            if isinstance(payloads, (bytes, bytearray)):
                payload_list = [bytes(payloads)]
            else:
                payload_list = [bytes(p) for p in payloads if p]
            if not payload_list:
                return 0

        for payload in payload_list:
            if supports_metadata:
                collector.send(
                    payload,
                    metadata={"auto_reply": True, "rule_name": rule_name},
                )
            else:
                collector.send(payload)
            if before_send is not None:
                try:
                    before_send(1, rule_name)
                except Exception:
                    _log.exception("auto_reply on_before_send callback raised")
        return len(payload_list)

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
            msg_id_len = self._msg_id_prefix_length(
                getattr(frame, "cmd_code", 0x01),
                getattr(self._ac, "cfg", None),
            )
            if len(raw_data) > msg_id_len:
                try:
                    fallback_fields = parse_attr_payload_fields(
                        raw_data[msg_id_len:], self._ac.cfg, force_report=False
                    )
                    fallback_result = SimpleNamespace(fields=fallback_fields)
                    records = self._ac.get_frame_attr_records(fallback_result)
                    # 恢复出的属性仅用于本函数 records；不原地修改 result.fields，
                    # 避免同一 ParseResult 被展示层/其他逻辑复用时产生串扰。
                    # 实时日志展示由 ui_helpers._recover_display_attr_fields 独立处理。
                except _VALIDATION_ERRORS as exc:
                    self._warn(f"命令下发属性恢复解析失败：{exc}")
                except Exception as exc:
                    _log.exception("命令下发属性恢复解析内部异常")
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
            except _VALIDATION_ERRORS as exc:
                invalid.append(str(exc))
                continue
            except Exception as exc:
                # 非预期内部错误：记 traceback，本帧仍整帧拒绝，避免半写
                _log.exception("命令下发属性校验内部异常 attr=0x%02X", internal_id)
                invalid.append(f"内部错误：{exc}")
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

        if not writable_order:
            # 全部为只读：属性被忽略但仍回复成功消息 ID（与历史行为一致）
            # 未知/非法值已在上文整帧拒绝，不会走到这里。
            return [self._cmd.build_cmd_ack_resp(msg_id)]

        # 二次预检：保证提交前全部仍合法（与一次校验分离，便于测试与竞态窗口）
        try:
            for attrid in writable_order:
                self._ac.validate_attr_value(attrid, validated_values[attrid])
        except _VALIDATION_ERRORS as exc:
            self._warn(f"命令下发二次校验失败，整帧未执行且未回复成功消息 ID：{exc}")
            return []
        except Exception as exc:
            _log.exception("命令下发二次校验内部异常")
            self._warn(f"命令下发二次校验失败，整帧未执行且未回复成功消息 ID：{exc}")
            return []

        # 原子批量写入（AttrStateCenter 事务接口）；测试用假对象回退到顺序写入
        values_to_write = {aid: validated_values[aid] for aid in writable_order}
        try:
            if hasattr(self._ac, "apply_values_atomic"):
                old_values = self._ac.apply_values_atomic(values_to_write)
            else:
                old_values = {}
                for attrid, value in values_to_write.items():
                    entry = self._ac.get_entry(attrid)
                    if entry is not None:
                        old_values[attrid] = getattr(entry, "current_value", None)
                    self._ac.set_attr_value(attrid, value)
        except _VALIDATION_ERRORS as exc:
            self._warn(f"命令下发写入校验失败，整帧未执行且未回复成功消息 ID：{exc}")
            return []
        except Exception as exc:
            _log.exception("命令下发原子写入内部异常")
            self._warn(f"命令下发写入失败，整帧未执行且未回复成功消息 ID：{exc}")
            return []

        self._last_applied_attrids = list(writable_order)

        try:
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
        except Exception as exc:
            # 组包失败：用 AttrStateCenter 事务接口一次性回滚
            try:
                if hasattr(self._ac, "restore_values"):
                    self._ac.restore_values(old_values)
                else:
                    for attrid, old in old_values.items():
                        self._ac.set_attr_value(attrid, old)
            except Exception as rb_exc:
                _log.exception("属性事务回滚失败")
                self._last_applied_attrids = []
                self._warn(
                    f"命令下发组包失败且回滚失败（状态可能脏）：{exc} | {rb_exc}"
                )
                raise RuntimeError(
                    f"属性回滚失败，状态可能不一致：{rb_exc}"
                ) from exc
            self._last_applied_attrids = []
            _log.exception("命令下发组包失败，已回滚属性状态")
            self._warn(f"命令下发写入/组包失败，已回滚属性状态：{exc}")
            return []

    @staticmethod
    def _msg_id_prefix_length(cmd_code: int = 0x01, cfg: dict | None = None) -> int:
        """从协议配置推断命令 payload 中消息 ID 前缀字节数。

        V3 默认 1 字节；若命令 request.format 指明更大宽度或自定义布局，
        优先尊重配置，避免硬编码偏移。
        """
        del cmd_code  # 预留：可按命令差异化
        # 当前内置 V3 与产品 JSON 的 msg_id* 格式均为 1 字节
        # 允许 cfg.frame.msg_id_size / cfg.__msg_id_width__ 显式覆盖
        if isinstance(cfg, dict):
            frame = cfg.get("frame") if isinstance(cfg.get("frame"), dict) else {}
            for key in ("msg_id_size", "msg_id_width", "__msg_id_width__"):
                raw = frame.get(key, cfg.get(key))
                if raw is None:
                    continue
                try:
                    size = int(raw)
                    if size > 0:
                        return size
                except (TypeError, ValueError):
                    continue
        return 1

    @staticmethod
    def _extract_action_id(result) -> int:
        for field_obj in getattr(result, "fields", None) or []:
            if not isinstance(field_obj, dict):
                continue
            name = str(field_obj.get("name") or "").lower().replace("_", "")
            if "action" in name or "行为" in name:
                try:
                    return _require_u8(field_obj.get("value", 0), "Action ID")
                except AttributeValidationError:
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
            try:
                resolved_u8 = _require_u8(resolved, "Action serialId")
            except AttributeValidationError:
                continue
            try:
                action_u8 = _require_u8(action_id, "Action ID")
            except AttributeValidationError:
                return []
            if resolved_u8 == action_u8:
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
                attrid = _require_u8(attrid, "属性ID")
            except (TypeError, ValueError, AttributeValidationError):
                continue
            entry = self._ac.get_entry(attrid)
            if entry is not None:
                value = item.get("value", item.get("default", entry.current_value))
                try:
                    value = self._ac.validate_attr_value(attrid, value)
                except _VALIDATION_ERRORS:
                    value = entry.current_value
                except Exception:
                    _log.exception("Action 输出属性校验内部异常 attr=0x%02X", attrid)
                    value = entry.current_value
                result.append((attrid, value, entry.typeid))
                continue
            try:
                typeid = int(item.get("type", item.get("typeid", 2)))
            except (TypeError, ValueError):
                typeid = 2
            value = item.get("value", item.get("default", 0))
            try:
                attrid_u8 = _require_u8(attrid, "属性ID")
            except AttributeValidationError:
                continue
            result.append((attrid_u8, value, typeid))
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
