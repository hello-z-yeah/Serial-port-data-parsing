"""串口帧同步与数据采集模块。

从连续的字节流中识别并切出完整的 V3.0 协议帧，
供解析器使用。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .parser import (
    Frame,
    ParseResult,
    ProtocolError,
    load_protocol,
    parse_frame,
    split_frame,
    to_hex,
)

try:
    import serial
    import serial.tools.list_ports
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False


@dataclass
class FrameSynchronizer:
    """字节流帧同步器：从任意位置输入字节，输出完整帧。

    工作原理：
    1. 累积字节到缓冲区
    2. 找到帧头（header）
    3. 读取长度字段
    4. 等收齐完整帧（header + ver + cmd + length + data + chk）
    5. 切出一帧并返回
    6. 循环直到缓冲区不足一帧
    """

    cfg: dict
    buffer: bytearray = field(default_factory=bytearray)
    frame_count: int = 0
    error_count: int = 0
    partial_bytes: int = 0

    def feed(self, data: bytes) -> list[Frame]:
        """输入一段字节，返回解析出的所有完整帧。"""
        self.buffer.extend(data)
        self.partial_bytes = len(self.buffer)
        frames: list[Frame] = []

        while True:
            frame = self._try_extract_one()
            if frame is None:
                break
            frames.append(frame)
            self.frame_count += 1

        self.partial_bytes = len(self.buffer)
        return frames

    def _try_extract_one(self) -> Frame | None:
        """尝试从缓冲区头部提取一帧。"""
        buf = self.buffer
        frame_cfg = self.cfg.get("frame", {})
        header_size = frame_cfg.get("header_size", 2)
        expected_header = _parse_int(frame_cfg.get("header", "0xA5A5"))
        ver_offset = frame_cfg.get("ver_offset", 2)
        ver_size = frame_cfg.get("ver_size", 1)
        cmd_offset = frame_cfg.get("cmd_offset", 3)
        length_offset = frame_cfg.get("length_offset", 4)
        length_size = frame_cfg.get("length_size", 2)
        length_byte_order = frame_cfg.get("length_byte_order", "big")
        checksum_size = frame_cfg.get("checksum", {}).get("length", 1)

        min_header = length_offset + length_size  # 至少要读到长度字段

        while len(buf) >= header_size:
            # 查找帧头
            header_val = int.from_bytes(buf[:header_size], "big")
            if header_val == expected_header:
                break
            # 帧头不匹配，移进一个字节再试
            self.error_count += 1
            buf.pop(0)

        if len(buf) < min_header:
            return None  # 数据不足，等更多

        # 读取 length 字段
        data_len = int.from_bytes(
            bytes(buf[length_offset:length_offset + length_size]),
            byteorder=length_byte_order,
        )

        # 计算总帧长
        total_len = length_offset + length_size + data_len + checksum_size

        # 防止异常长度（比如帧头误识别导致 length 很大）
        max_frame = frame_cfg.get("max_frame_size", 4096)
        if data_len > max_frame:
            # 异常长度，可能帧头识别错了，移进一个字节继续找
            self.error_count += 1
            buf.pop(0)
            return None  # 让外层循环继续

        if len(buf) < total_len:
            return None  # 数据还没收齐

        # 提取完整帧
        raw = bytes(buf[:total_len])
        del buf[:total_len]

        # 用 split_frame 正式拆分（同时做校验等）
        try:
            return split_frame(raw, self.cfg)
        except ProtocolError:
            # 拆分失败（比如版本不对），这帧可能是垃圾，丢掉
            self.error_count += 1
            return None

    def reset(self) -> None:
        """清空缓冲区。"""
        self.buffer.clear()
        self.partial_bytes = 0


@dataclass
class SerialCollector:
    """串口数据采集器：连接串口，实时解析帧，回调输出；支持全双工安全发送。

    接收路径（RX）：
      - on_frame(result, Frame, ts) : HEX 模式解析到一条帧
      - on_error(msg)               : 串口/解析异常，用于 GUI 弹窗提示
      - on_raw(data, ts)            : ASCII 模式下原始字节块（HEX 模式不会触发）

    发送路径（TX）：
      - send(frame_bytes) / send_raw(plain_bytes_or_str) 把数据写到端口；
        内部自带 _write_lock（线程锁），保证 GUI 线程点一次"发送"和后台的"周期发送线程"
        不会把字节流穿插写入。
      - on_tx_sent(data_sent, direction_label, ts) : 成功写入后回调，用于 GUI 同屏
        显示 TX（颜色 / 方向标签 / 日志写入时区分 RX/TX）。
    """

    cfg: dict
    port: str
    baudrate: int = 9600
    bytesize: int = 8
    stopbits: float = 1.0  # 允许 1.5（之前是 int，兼容 GUI 下拉"1 / 1.5 / 2"）
    direction: str | None = None
    on_frame: Callable[[ParseResult, Frame, float], None] | None = None
    on_error: Callable[[str], None] | None = None
    on_raw: Callable[[bytes, float], None] | None = None  # ASCII 模式 RX 回调
    raw_mode: bool = False  # True=仅输出原始数据，不做协议解析
    on_tx_sent: Callable[[bytes, str, float], None] | None = None  # bytes, dir_label, ts
    running: bool = False
    _thread: threading.Thread | None = None
    _serial: "serial.Serial | None" = None
    sync: FrameSynchronizer | None = None
    _write_lock: threading.Lock | None = None

    def start(self) -> None:
        if not HAS_SERIAL:
            raise RuntimeError("pyserial 未安装，请执行: pip install pyserial")
        if self.running:
            return
        self.sync = FrameSynchronizer(self.cfg)
        bytesize_map = {5: serial.FIVEBITS, 6: serial.SIXBITS, 7: serial.SEVENBITS, 8: serial.EIGHTBITS}
        stopbits_map = {1: serial.STOPBITS_ONE, 1.5: serial.STOPBITS_ONE_POINT_FIVE, 2: serial.STOPBITS_TWO}
        sb = self.stopbits
        try:
            sb_val = stopbits_map.get(float(sb), serial.STOPBITS_ONE)
        except Exception:
            sb_val = serial.STOPBITS_ONE
        self._write_lock = self._write_lock or threading.Lock()
        self._serial = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=bytesize_map.get(self.bytesize, serial.EIGHTBITS),
            parity=serial.PARITY_NONE,
            stopbits=sb_val,
            timeout=0.1,
        )
        self.running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.running = False
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._serial and self._serial.is_open:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None

    # ------------------------------------------------------------------
    # 全双工发送：统一走锁，保证写入原子
    # ------------------------------------------------------------------

    def _send_bytes_locked(self, payload: bytes, dir_label: str) -> int:
        """写串口的真正底层入口：
        - 必须已 start()（_serial open）否则抛错
        - 加 _write_lock
        - 成功后 on_tx_sent(data, dir_label, now)
        """
        if not self._serial or not self._serial.is_open:
            raise RuntimeError("串口未打开，请先开始监控再发送")
        if not payload:
            return 0
        if self._write_lock is None:
            self._write_lock = threading.Lock()
        import time as _t
        with self._write_lock:
            n = self._serial.write(payload)
            self._serial.flush()
        ts = _t.time()
        try:
            if self.on_tx_sent:
                self.on_tx_sent(bytes(payload), dir_label, ts)
        except Exception as e:  # noqa: BLE001
            try:
                if self.on_error:
                    self.on_error(f"TX 回调异常: {e}")
            except Exception:
                pass
        return n

    def send(self, frame_bytes: bytes | str) -> int:
        """发送一个协议型完整帧。
        - frame_bytes: bytes 或 HEX 字符串（"A5 A5 03 20 ..."）
        """
        from protocol_parser.parser import to_hex, _parse_int, EncodeFrameError

        payload: bytes
        if isinstance(frame_bytes, (bytes, bytearray)):
            payload = bytes(frame_bytes)
        elif isinstance(frame_bytes, str):
            s = frame_bytes.strip()
            s_clean = s.replace(" ", "").replace("\n", "").replace("\r", "").replace("\t", "")
            if s_clean.lower().startswith("0x"):
                s_clean = s_clean[2:]
            if len(s_clean) % 2 == 1:
                s_clean = "0" + s_clean
            try:
                payload = bytes.fromhex(s_clean)
            except Exception as e:
                from protocol_parser.parser import EncodeFrameError
                raise EncodeFrameError(f"TX HEX 字符串非法：{frame_bytes!r}，原因：{e}") from e
        else:
            raise TypeError("send() 需要 bytes 或 HEX 字符串")
        return self._send_bytes_locked(payload, "TX")

    def send_raw(self, data, *, as_text: bool | None = None) -> int:
        """发送原始字节。
        参数：
          data      : bytes / bytearray / str
          as_text   : None → 根据 data 类型推断（str 视为 ASCII/UTF-8，bytes 直接写）
                      True → str.encode() 写文本；False → 字符串当 HEX 写。
        """
        if isinstance(data, (bytes, bytearray)):
            payload = bytes(data)
        elif isinstance(data, str):
            if as_text is None:
                # 启发式：如果全是 hex+空格 且长度>0，当作 HEX；否则文本
                s = data.strip()
                hex_chars = set("0123456789abcdefABCDEF \t\n\r")
                looks_like_hex = (
                    len(s) > 0
                    and all(c in hex_chars for c in s)
                    and (any(c in "0123456789abcdefABCDEF" for c in s))
                )
                if looks_like_hex:
                    return self.send(data)  # 当作 HEX 字符串
                payload = data.encode("utf-8")
            elif as_text:
                payload = data.encode("utf-8")
            else:
                return self.send(data)
        else:
            raise TypeError("send_raw() 需要 bytes 或 str")
        return self._send_bytes_locked(payload, "TX")

    # ------------------------------------------------------------------
    # 读取循环（保留原有逻辑，不再多加）
    # ------------------------------------------------------------------

    def _read_loop(self) -> None:
        assert self._serial is not None and self.sync is not None
        try:
            while self.running:
                try:
                    raw = self._serial.read(4096)
                except serial.SerialException as e:
                    if self.on_error:
                        self.on_error(f"串口读取错误: {e}")
                    break

                if not raw:
                    continue

                now = time.time()

                # raw_mode：直接回调原始字节，不做协议解析
                if self.raw_mode:
                    if self.on_raw:
                        self.on_raw(raw, now)
                    continue

                # 无协议（cfg 没有 frame 配置）：直接把原始字节回传给 GUI 显示，
                # 不走帧同步，避免 FrameSynchronizer 用默认 0xA5A5 帧头误消费字节
                frame_cfg = self.cfg.get("frame", {}) if self.cfg else {}
                if not frame_cfg:
                    if self.on_raw:
                        try:
                            self.on_raw(raw, now)
                        except Exception as e:
                            if self.on_error:
                                self.on_error(f"原始数据回调异常（已跳过）: {e}")
                    continue

                # HEX 模式：帧同步 + 协议解析
                frames = self.sync.feed(raw)
                # 没切出任何帧（如接收的是纯 ASCII 文本，无协议帧头）：
                # 把这段原始字节回传给 GUI，按 HEX 格式显示，避免界面空白误以为没数据
                if not frames and self.on_raw:
                    try:
                        self.on_raw(raw, now)
                    except Exception as e:
                        if self.on_error:
                            self.on_error(f"原始数据回调异常（已跳过）: {e}")
                for frame in frames:
                    try:
                        result = parse_frame(frame.raw, self.cfg, direction=self.direction)
                    except Exception as e:
                        if self.on_error:
                            self.on_error(f"帧解析异常（已跳过）: {e}")
                        continue
                    try:
                        if self.on_frame:
                            self.on_frame(result, frame, now)
                    except Exception as e:
                        if self.on_error:
                            self.on_error(f"回调异常（已跳过）: {e}")
        except Exception as e:
            if self.on_error:
                self.on_error(f"采集异常: {e}")

    @staticmethod
    def list_ports() -> list[dict]:
        """列出当前所有可用串口。"""
        if not HAS_SERIAL:
            return []
        ports = []
        for p in serial.tools.list_ports.comports():
            ports.append({
                "device": p.device,
                "description": p.description,
                "hwid": p.hwid,
            })
        return ports


def _parse_int(v) -> int:
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s.startswith("0x"):
            return int(s, 16)
        return int(s, 0)
    raise ProtocolError(f"无法解析为整数: {v}")
