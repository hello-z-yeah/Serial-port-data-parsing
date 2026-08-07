"""根据产品配置与属性状态自动生成 V3.0 指令。"""
from __future__ import annotations
from .exceptions import SerialStateError, AttributeValidationError

from typing import Any

from .dev_info_encoder import encode_dev_info_frame, build_snapshot_attrid_map
from .parser import encode_frame


class AutoCmdEngine:
    def __init__(self, attr_center) -> None:
        self._ac = attr_center

    @property
    def cfg(self) -> dict:
        cfg = self._ac.cfg
        if not cfg:
            raise SerialStateError("尚未加载产品协议")
        return cfg

    # 模拟 MCU：回复
    def build_heartbeat_resp(self, reset_flag: bool = False) -> bytes:
        return encode_frame(0x20, self.cfg, direction="response", fields={"value": 0 if reset_flag else 1})

    def build_dev_info_resp(self) -> bytes:
        return encode_dev_info_frame(self.cfg)

    def build_module_status_ack(self) -> bytes:
        return encode_frame(0x22, self.cfg, direction="response", data=b"")

    def build_snapshot_resp(self, attrids: list[int] | None = None) -> bytes:
        """Build an MCU -> module 0x24 snapshot response.

        For MIOT JSON products, 0x21 advertises a sequential wire attribute id
        (0, 1, 2, ...).  0x24 must reuse that id; sending the internal GUI ids
        such as 0x41/0x51 produces a structurally valid but incompatible frame.
        """
        targets = attrids if attrids is not None else self._ac.get_readable_attrs()
        wire_map, uses_miot_serial_ids = build_snapshot_attrid_map(self.cfg)
        default_string = str(
            (self.cfg.get("product_info") or {}).get(
                "snapshot_string_default", "helloworld"
            )
        )

        items: list[tuple[int, Any, int]] = []
        for internal_attrid in targets:
            attrid, value, typeid = self._ac.get_attr_value(internal_attrid)
            entry = self._ac.get_entry(attrid)
            if entry is None or entry.access == "只写":
                continue
            wire_attrid = wire_map.get(attrid, attrid)

            # The simulator needs a concrete payload for unset STRING values.
            # MIOT snapshot examples use "helloworld"; users can override the
            # value in the live attribute table, or configure
            # product_info.snapshot_string_default for another product.
            if uses_miot_serial_ids and typeid == 11 and value in (None, ""):
                value = default_string

            try:
                value = self._ac.validate_attr_value(attrid, value)
            except ValueError:
                # 老产品文件可能保存了越界 nowValue。属性中心加载时通常已经
                # 修正；这里再做最后一道保护，避免生成业务值非法的快照帧。
                value = self._ac.get_valid_default_value(attrid)

            items.append((wire_attrid, value, typeid))

        return encode_frame(0x24, self.cfg, direction="response", fields=items)

    def build_cmd_ack_resp(self, msg_id: int) -> bytes:
        return encode_frame(0x01, self.cfg, direction="response", fields={"msg_id": int(msg_id) & 0xFF})

    def build_action_resp(
        self,
        msg_id: int,
        action_id: int = 0,
        out_params: Any = None,
    ) -> bytes:
        wire_map, _ = build_snapshot_attrid_map(self.cfg)
        actions: list[tuple[int, Any, int]] = []
        if isinstance(out_params, dict):
            for raw_id, value in out_params.items():
                try:
                    attrid = int(str(raw_id), 16) if str(raw_id).lower().startswith("0x") else int(raw_id)
                except (TypeError, ValueError):
                    continue
                entry = self._ac.get_entry(attrid)
                if entry is None:
                    continue
                value = self._ac.validate_attr_value(attrid, value)
                actions.append((wire_map.get(attrid, attrid), value, entry.typeid))
        elif isinstance(out_params, list):
            for item in out_params:
                if not isinstance(item, (list, tuple)) or len(item) < 3:
                    continue
                raw_id, value, raw_typeid = item[0], item[1], item[2]
                try:
                    attrid = int(str(raw_id), 16) if str(raw_id).lower().startswith("0x") else int(raw_id)
                except (TypeError, ValueError):
                    continue
                entry = self._ac.get_entry(attrid)
                if entry is not None:
                    value = self._ac.validate_attr_value(attrid, value)
                    typeid = entry.typeid
                    wire_attrid = wire_map.get(attrid, attrid)
                else:
                    try:
                        typeid = int(raw_typeid)
                    except (TypeError, ValueError):
                        typeid = 2
                    wire_attrid = attrid & 0xFF
                actions.append((wire_attrid, value, typeid))
        return encode_frame(
            0x12, self.cfg, direction="response",
            fields={
                "msg_id": int(msg_id) & 0xFF,
                "action_id": int(action_id) & 0xFF,
                "actions": actions,
            },
        )

    def build_prod_test_ack(self) -> bytes:
        return encode_frame(0x60, self.cfg, direction="response", data=b"")

    # 模拟 MCU：主动发送
    def build_attr_report(
        self,
        attrids: list[int] | None = None,
        values: dict[int, Any] | None = None,
    ) -> bytes:
        targets = attrids if attrids is not None else self._ac.get_readable_attrs()
        wire_map, _ = build_snapshot_attrid_map(self.cfg)
        items: list[tuple[int, Any, int]] = []
        for aid in targets:
            internal_attrid, current, typeid = self._ac.get_attr_value(aid)
            entry = self._ac.get_entry(internal_attrid)
            if entry is None or entry.access == "只写":
                continue
            value = values.get(aid, current) if values else current
            value = self._ac.validate_attr_value(internal_attrid, value)
            wire_attrid = wire_map.get(internal_attrid, internal_attrid)
            items.append((wire_attrid, value, typeid))
        if not items:
            raise AttributeValidationError("没有可由 MCU 状态上报的属性")
        return encode_frame(0x10, self.cfg, direction="request", fields=items)

    def build_get_attr_resp(self, msg_id: int, attrids: list[int]) -> bytes:
        """Reply to module command 0x03 with the requested readable values."""
        wire_map, _ = build_snapshot_attrid_map(self.cfg)
        items: list[tuple[int, Any, int]] = []
        for aid in attrids:
            internal_attrid, current, typeid = self._ac.get_attr_value(aid)
            entry = self._ac.get_entry(internal_attrid)
            if entry is None or entry.access == "只写":
                continue
            value = self._ac.validate_attr_value(internal_attrid, current)
            items.append((wire_map.get(internal_attrid, internal_attrid), value, typeid))
        return encode_frame(
            0x03,
            self.cfg,
            direction="response",
            fields={"msg_id": int(msg_id) & 0xFF, "attrs": items},
        )

    def build_net_config(self, config_type: int = 1) -> bytes:
        return encode_frame(0x23, self.cfg, direction="request", fields={"value": config_type})

    def build_time_request(self, timezone: int = 0) -> bytes:
        return encode_frame(0x26, self.cfg, direction="request", fields={"timezone": timezone})

    def build_mcu_status(self, status: int = 1) -> bytes:
        return encode_frame(0x25, self.cfg, direction="request", fields={"value": status})

    # 低功耗唤醒模拟
    def build_low_power_enter(self) -> bytes:
        """MCU报告进入低功耗 0x25 request: mcu_status=1"""
        return encode_frame(0x25, self.cfg, direction="request", fields={"value": 1})

    def build_low_power_exit(self) -> bytes:
        """MCU报告退出低功耗 0x25 request: mcu_status=0"""
        return encode_frame(0x25, self.cfg, direction="request", fields={"value": 0})

    def build_low_power_service(self, enable: bool = True) -> bytes:
        """服务设置 0x02 request: 开启/关闭低功耗服务
        协议 V3.0 附录定义低功耗服务 attrid=0xD1；0xEF 属于蓝牙场景配对方式，
        不能误用，否则会产生与低功耗无关的副作用。
        """
        # 0x02 的 service_set 数据格式是 [typeid, attrid, value]，不是
        # attr_list，必须直接传完整 data 段。低功耗服务使用 UINT8(typeid=2)。
        return encode_frame(
            0x02,
            self.cfg,
            direction="request",
            data=bytes([0x02, 0xD1, 0x01 if enable else 0x00]),
        )

    def build_module_status_resp(self, status: int = 5) -> bytes:
        """模组工作状态回复 0x22 response（空data，MCU确认收到模组状态）"""
        return encode_frame(0x22, self.cfg, direction="response", data=b"")

    def build_heartbeat_reset(self) -> bytes:
        """心跳回复-重启后首次 0x20 response: heartbeat_resp=0x00"""
        return encode_frame(0x20, self.cfg, direction="response", fields={"value": 0})

    # 模拟模组
    def build_heartbeat_req(self, module_status: int = 0) -> bytes:
        return encode_frame(0x20, self.cfg, direction="request", fields={"value": module_status})

    def build_dev_info_req(self) -> bytes:
        return encode_frame(0x21, self.cfg, direction="request", data=b"")

    def build_snapshot_req(self) -> bytes:
        return encode_frame(0x24, self.cfg, direction="request", data=b"")

    def build_cmd_send(self, msg_id: int, attrid: int, value: Any) -> bytes:
        _, _, typeid = self._ac.get_attr_value(attrid)
        value = self._ac.validate_attr_value(attrid, value)
        wire_map, _ = build_snapshot_attrid_map(self.cfg)
        wire_attrid = wire_map.get(int(attrid), int(attrid))
        return encode_frame(
            0x01, self.cfg, direction="request",
            fields={"msg_id": int(msg_id) & 0xFF, "attrs": [(wire_attrid, value, typeid)]},
        )

    def build_get_attr_req(self, msg_id: int, attrids: list[int]) -> bytes:
        wire_map, _ = build_snapshot_attrid_map(self.cfg)
        wire_attrids = [wire_map.get(int(attrid), int(attrid)) for attrid in attrids]
        return encode_frame(
            0x03, self.cfg, direction="request",
            fields={"msg_id": int(msg_id) & 0xFF, "attrids": wire_attrids},
        )
