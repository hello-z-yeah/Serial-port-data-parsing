"""协议监控工具：串口实时采集 + 粘贴交互解析。

提供两种模式：
- serial:  连接串口，实时接收并解析 V3.0 协议帧
- paste:   交互式粘贴 hex 数据，立即解析（支持多条）
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path
from typing import TextIO

from .parser import ParseResult, ProtocolError, load_protocol, parse_frame, parse_hex_input, to_hex
from .serial_collector import FrameSynchronizer, SerialCollector


# ---------- 渲染 ----------

def _format_timestamp(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]


def render_result_compact(result: ParseResult, ts: float | None = None) -> str:
    """紧凑单行输出（用于实时监控）。"""
    ts_str = f"[{_format_timestamp(ts)}] " if ts else ""
    cs = "✓" if result.checksum_ok else "✗" if result.checksum_ok is False else " "
    status = "OK" if not result.error else "ERR"
    dir_label = f" [{result.direction}]" if result.direction else ""
    return f"{ts_str}{status} {cs} {result.cmd_code:<6} {result.cmd_name}{dir_label}  | {result.raw_hex}"


def render_result_detail(result: ParseResult, ts: float | None = None) -> str:
    """详细多行输出（用于粘贴模式）。"""
    lines: list[str] = []
    if ts:
        lines.append(f"时间: {_format_timestamp(ts)}")
    lines.append(f"原始: {result.raw_hex}")
    lines.append(f"命令: {result.cmd_code}  {result.cmd_name}")
    if result.direction:
        lines.append(f"方向: {result.direction}")
    if result.description:
        lines.append(f"说明: {result.description}")
    if result.checksum_ok is not None:
        lines.append(f"校验: {'通过' if result.checksum_ok else '失败'}")
    if result.length_match is False:
        lines.append(f"长度: 不匹配（length字段与实际不一致）")
    if result.error:
        lines.append(f"错误: {result.error}")
    if result.fields:
        lines.append("字段:")
        for f in result.fields:
            name = f.get("name", "")
            text = f.get("text", "")
            lines.append(f"  · {name:<24} {text}")
    return "\n".join(lines)


# ---------- 日志 ----------

class ResultLogger:
    """把解析结果保存到日志文件。

    新增安全属性（防止长时运行无限变大）：
      1) 默认按大小滚动：max_bytes=10MB，backup_count=10 份
         - 超过 10MB 就把当前 txt 改名为 log.txt.1 / .2 / ...
         - 超过 backup_count 份的 .10 自动删掉
      2) 也支持按日期滚动：rotate_mode='size' | 'daily'
         - daily：一天一个文件，文件名自动加 YYYYMMDD 后缀，保留 N 天（backup_count 天）
      3) 兼容原有 API：`__enter__/__exit__`、`.log(result, ts)` 不变，外部无需改动

    自定义参数示例：
        logger = ResultLogger(path, mode='detail',
                              rotate_mode='size', max_bytes=10*1024*1024, backup_count=10)
    """

    def __init__(
        self,
        path: str | Path,
        mode: str = "compact",
        rotate_mode: str = "size",
        max_bytes: int = 10 * 1024 * 1024,
        backup_count: int = 10,
    ):
        self.path = Path(path)
        self.mode = mode  # compact / detail
        if rotate_mode not in ("size", "daily", "none"):
            raise ValueError(f"非法 rotate_mode: {rotate_mode!r}，应为 size/daily/none")
        self.rotate_mode = rotate_mode
        self.max_bytes = max(1, int(max_bytes))
        self.backup_count = max(0, int(backup_count))
        self._f: TextIO | None = None
        self._count = 0
        # daily 模式：当前打开的日期标签，用于跨日自动切新文件
        self._daily_tag: str | None = None

    # -------- 内部：打开/关闭/滚动 --------
    def _ensure_parent_dir(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _current_daily_tag(self) -> str:
        return datetime.now().strftime("%Y%m%d")

    def _daily_path(self, date_tag: str) -> Path:
        """daily 模式：log.txt -> log.20260723.txt"""
        if self.path.suffix:
            return self.path.with_name(f"{self.path.stem}.{date_tag}{self.path.suffix}")
        return self.path.with_name(f"{self.path.name}.{date_tag}")

    def _open_for_write(self, target_path: Path, append: bool = True) -> TextIO:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        f = target_path.open("a" if append else "w", encoding="utf-8")
        return f

    def _rollover_size_if_needed(self, extra_bytes_estimate: int = 0) -> None:
        """size 模式：当前文件 (已有大小 + 即将写入) > max_bytes 时滚动。

        兼容两种文件位置状态：
          - append 模式打开：每次 write 后 _f.tell() 就是磁盘真实大小
          - 被外部改名/删文件导致 tell/stat 不准：用 Path.stat 兜底
        另外：默认 ResultLogger 每次 log 前都会写一行 "开始记录"（~48B），
        所以把 max_bytes 视为**滚动阈值**，不是绝对硬上限，接近阈值就滚。
        """
        if self.rotate_mode != "size" or self._f is None:
            return
        # 1. 当前文件大小：优先 tell（write 之后最准）；否则用 stat
        cur = -1
        try:
            self._f.flush()
            cur = self._f.tell()
        except (OSError, ValueError):
            cur = -1
        if cur <= 0:
            try:
                cur = self.path.stat().st_size
            except OSError:
                return
        # 2. 阈值：max_bytes 是"触发滚动"的上限，当前内容 + 预计写入超了就滚
        if cur + extra_bytes_estimate <= self.max_bytes:
            return
        # 3. 滚动：关闭当前 → log.txt.1 / .2 / ... → 超出 backup_count 的删掉
        try:
            self._f.close()
        except OSError:
            pass
        self._f = None
        # 3a. 重命名 i -> i+1（倒序）
        for i in range(self.backup_count - 1, 0, -1):
            src = self.path.with_name(f"{self.path.name}.{i}")
            dst = self.path.with_name(f"{self.path.name}.{i + 1}")
            if src.exists():
                try:
                    if dst.exists():
                        dst.unlink()
                except OSError:
                    pass
                try:
                    src.rename(dst)
                except OSError:
                    pass
        # 3b. 把当前主日志改名为 .1
        if self.backup_count > 0:
            dst1 = self.path.with_name(f"{self.path.name}.1")
            if self.path.exists():
                try:
                    if dst1.exists():
                        dst1.unlink()
                except OSError:
                    pass
                try:
                    self.path.rename(dst1)
                except OSError:
                    pass
        # 3c. 兜底：删掉超出上限的老文件（backup_count+1 及以上的老卷号都清）
        if self.backup_count > 0:
            for p in self.path.parent.glob(f"{self.path.name}.*"):
                try:
                    suffix_tail = p.name[len(self.path.name) + 1 :]
                    if suffix_tail.isdigit():
                        if int(suffix_tail) > self.backup_count:
                            p.unlink()
                except (ValueError, OSError):
                    pass
        # 4. 重新打开主日志（覆盖模式，写入新的 header + body）
        self._f = self._open_for_write(self.path, append=False)
        self._write_header()

    def _rollover_daily_if_needed(self) -> None:
        """daily 模式：跨天自动切到新文件（旧文件保留 backup_count 天）。"""
        if self.rotate_mode != "daily" or self._f is None:
            return
        today = self._current_daily_tag()
        if today == self._daily_tag:
            return
        # 切日：先关旧（旧就是 log.YYYYMMDD.txt，名字已经带日期，不用再滚）
        self._f.close()
        self._f = None
        self._daily_tag = today
        # 删除 N 天前的旧日志
        if self.backup_count > 0:
            from datetime import timedelta
            base = datetime.now()
            keep_tags = {(base - timedelta(days=i)).strftime("%Y%m%d") for i in range(self.backup_count + 1)}
            parent = self.path.parent
            stem = self.path.stem
            suffix = self.path.suffix
            pattern = f"{stem}.*{suffix}" if suffix else f"{stem}.*"
            for p in parent.glob(pattern):
                # 提取中间的日期串
                rest = p.name[len(stem) + 1:]
                if suffix and rest.endswith(suffix):
                    rest = rest[:-len(suffix)]
                if re.fullmatch(r"\d{8}", rest) and rest not in keep_tags:
                    try:
                        p.unlink()
                    except OSError:
                        pass
        self._f = self._open_for_write(self._daily_path(today), append=True)

    # -------- 对外 API：保持和旧版本完全一致 --------
    def __enter__(self) -> "ResultLogger":
        self._ensure_parent_dir()
        if self.rotate_mode == "daily":
            self._daily_tag = self._current_daily_tag()
            target = self._daily_path(self._daily_tag)
            self._f = self._open_for_write(target, append=True)
        else:
            self._f = self._open_for_write(self.path, append=True)
        self._write_header()
        return self

    def __exit__(self, *args) -> None:
        if self._f:
            self._f.write(f"===== 结束记录（共 {self._count} 条） =====\n")
            self._f.flush()
            self._f.close()
            self._f = None

    def _write_header(self) -> None:
        if not self._f:
            return
        self._f.write(f"\n===== 开始记录 {datetime.now().isoformat(timespec='seconds')} =====\n")
        self._f.flush()

    def log(self, result: ParseResult, ts: float | None = None) -> None:
        if not self._f:
            return
        ts = ts or time.time()
        if self.mode == "detail":
            body = (
                f"--- {_format_timestamp(ts)} ---\n"
                + render_result_detail(result)
                + "\n\n"
            )
        else:
            body = render_result_compact(result, ts) + "\n"

        # 滚动检查
        if self.rotate_mode == "size":
            self._rollover_size_if_needed(extra_bytes_estimate=len(body.encode("utf-8")))
        else:
            self._rollover_daily_if_needed()
        # 滚动后 __enter__ 保证 _f 一定存在，但滚动再保险一次
        if self._f is None:
            return
        self._f.write(body)
        self._f.flush()
        self._count += 1


# 兼容性：旧代码可能用 text_log（原来叫这个名，也可能是笔误；保留为 ResultLogger 别名即可）
# 注意：为避免重复实现，不再单独提供 text_log；需要按天滚动时直接 rotate_mode='daily'
import re  # noqa: E402  （放在文件底部以免污染其他代码）



# ---------- 粘贴交互模式 ----------

def run_paste_mode(cfg: dict, logger: ResultLogger | None = None) -> int:
    """交互式粘贴解析。

    用户粘贴 hex 数据后按回车，立即解析。
    输入空行退出。
    支持一次粘贴多条（换行分隔）。
    """
    product = cfg.get("product", "unknown")
    print(f"=== 协议解析工具 - 粘贴模式 (产品: {product}) ===")
    print("粘贴 hex 数据（支持空格/逗号分隔，一行一条），按回车解析。")
    print("输入空行退出。\n")

    sync = FrameSynchronizer(cfg)
    line_count = 0

    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出。")
            break

        if not line:
            if line_count == 0:
                print("已退出。")
                break
            continue

        line_count += 1

        # 尝试整行解析为一条完整指令
        try:
            data = parse_hex_input(line)
            result = parse_frame(data, cfg)
            print()
            print(render_result_detail(result))
            print()
            if logger:
                logger.log(result)
        except ProtocolError as e:
            # 整行解析失败，可能是帧流数据 — 用同步器试试
            print(f"\n[!] 整行解析失败: {e}")
            print("    尝试作为字节流进行帧同步...")
            try:
                data = parse_hex_input(line)
                frames = sync.feed(data)
                if frames:
                    for frame in frames:
                        result = parse_frame(frame.raw, cfg)
                        print()
                        print(render_result_detail(result))
                        print()
                        if logger:
                            logger.log(result)
                    print(f"    共提取 {len(frames)} 帧。")
                else:
                    print("    未提取到完整帧（可能数据不足）。")
                    print(f"    缓冲区剩余 {sync.partial_bytes} 字节。")
            except ProtocolError as e2:
                print(f"    也失败: {e2}\n")

    return 0


# ---------- 串口实时模式 ----------

def run_serial_mode(
    cfg: dict,
    port: str,
    baudrate: int = 115200,
    detail: bool = False,
    logger: ResultLogger | None = None,
) -> int:
    """串口实时采集解析。"""
    product = cfg.get("product", "unknown")
    print(f"=== 协议解析工具 - 串口实时模式 ===")
    print(f"产品: {product}")
    print(f"串口: {port} @ {baudrate} bps")
    print(f"模式: {'详细' if detail else '紧凑'}")
    print("按 Ctrl+C 停止\n")

    def on_frame(result: ParseResult, frame, ts: float) -> None:
        if detail:
            print(render_result_detail(result, ts))
            print("-" * 60)
        else:
            print(render_result_compact(result, ts))
        if logger:
            logger.log(result, ts)

    def on_error(msg: str) -> None:
        print(f"[错误] {msg}")

    collector = SerialCollector(
        cfg=cfg,
        port=port,
        baudrate=baudrate,
        on_frame=on_frame,
        on_error=on_error,
    )

    try:
        collector.start()
    except Exception as e:
        print(f"打开串口失败: {e}")
        return 2

    print(f"[已连接] 等待数据...\n")

    try:
        while collector.running:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n\n正在停止...")
    finally:
        collector.stop()

    if collector.sync:
        print(f"共接收 {collector.sync.frame_count} 帧，错误 {collector.sync.error_count} 次。")
    return 0


def list_serial_ports() -> list[dict]:
    """列出所有可用串口。"""
    return SerialCollector.list_ports()
