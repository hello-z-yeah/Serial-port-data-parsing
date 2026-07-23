"""命令行入口：支持单条解析、批量解析、协议查询。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .parser import (
    ParseResult,
    ProtocolError,
    TYPEID_MAP,
    _log_error_to_disk as _parser_log_error,
    classify_protocol_error,
    get_builtin_v3,
    load_protocol,
    merge_protocol,
    parse_frame,
    parse_hex_input,
    to_hex,
)

DEFAULT_PROTOCOL_DIR = Path(__file__).resolve().parent.parent / "product"


def _log_error_to_disk(exc: Exception) -> Path:
    """兼容旧调用：统一转发到 parser._log_error_to_disk（带 datetime/Path/tempfile 兜底）。"""
    return _parser_log_error(exc)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ProtocolError as e:
        friendly, debug = classify_protocol_error(e)
        print(f"[错误] {friendly}", file=sys.stderr)
        if debug:
            print(f"       详细: {debug}", file=sys.stderr)
        try:
            _log_error_to_disk(e)
        except Exception:
            pass
        return 2
    except KeyboardInterrupt:
        print("[提示] 用户中断", file=sys.stderr)
        return 130
    except Exception as e:  # noqa: BLE001  —— 顶层兜底，绝对不让堆栈直接裸抛
        friendly, debug = classify_protocol_error(e)
        print(f"[错误] {friendly}", file=sys.stderr)
        log_path = _log_error_to_disk(e)
        if debug:
            print(f"       详细: {debug}", file=sys.stderr)
        print(f"       堆栈已写入: {log_path}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())


def find_protocol_file(product: str, protocol_dir: Path | None = None) -> Path:
    d = protocol_dir or DEFAULT_PROTOCOL_DIR
    # 先按文件名匹配
    candidate = d / f"{product}.json"
    if candidate.exists():
        return candidate
    # 再按 product 字段匹配
    if d.exists():
        for f in d.glob("*.json"):
            try:
                cfg = load_protocol(f)
                if cfg.get("product") == product:
                    return f
            except ProtocolError:
                continue
    raise ProtocolError(
        f"找不到产品 '{product}' 的协议文件（在 {d} 中查找）。"
        f"使用 'protocols' 子命令查看可用协议。"
    )


def list_protocols(protocol_dir: Path | None = None) -> list[dict]:
    d = protocol_dir or DEFAULT_PROTOCOL_DIR
    if not d.exists():
        return []
    out: list[dict] = []
    for f in sorted(d.glob("*.json")):
        try:
            cfg = load_protocol(f)
            out.append({
                "product": cfg.get("product", ""),
                "file": str(f),
                "description": cfg.get("description", ""),
                "command_count": len(cfg.get("commands", [])),
            })
        except ProtocolError as e:
            out.append({"file": str(f), "error": str(e)})
    return out


# ---------- 渲染 ----------

def render_single(result: ParseResult) -> str:
    lines: list[str] = []
    lines.append(f"产品: {result.product}")
    lines.append(f"原始字节: {result.raw_hex}")
    lines.append(f"命令字: {result.cmd_code}")
    lines.append(f"命令名: {result.cmd_name}")
    if result.direction:
        lines.append(f"方向: {_direction_label(result.direction)}")
    if result.description:
        lines.append(f"说明: {result.description}")
    if result.checksum_ok is not None:
        flag = "通过" if result.checksum_ok else "失败"
        lines.append(f"校验: {flag}")
    if result.error:
        lines.append(f"错误: {result.error}")
    if result.fields:
        lines.append("")
        lines.append("字段解析:")
        lines.append(f"  {'字段名':<16} {'类型':<12} {'值/含义':<24}")
        lines.append(f"  {'-'*16} {'-'*12} {'-'*24}")
        for f in result.fields:
            name = f.get("name", "")
            ftype = f.get("type", "")
            text = f.get("text", "")
            lines.append(f"  {name:<16} {ftype:<12} {text:<24}")
    return "\n".join(lines)


def _direction_label(d: str) -> str:
    return {"request": "主机→设备", "response": "设备→主机"}.get(d, d)


def render_batch(results: list[ParseResult]) -> str:
    lines = [f"共解析 {len(results)} 条指令：", ""]
    for i, r in enumerate(results, 1):
        status = "OK" if r.error is None else "ERR"
        cs = ""
        if r.checksum_ok is False:
            cs = " [校验失败]"
        lines.append(f"[{i:03d}] {status}{cs} {r.cmd_code:<8} {r.cmd_name:<20} | {r.raw_hex}")
        if r.error:
            lines.append(f"      └─ {r.error}")
    return "\n".join(lines)


# ---------- 子命令 ----------

def cmd_parse(args: argparse.Namespace) -> int:
    try:
        proto_file = find_protocol_file(args.product, args.protocol_dir)
        cfg = load_protocol(proto_file)
        # 与V3.0基础协议合并
        base_cfg = get_builtin_v3()
        cfg = merge_protocol(base_cfg, cfg)
    except ProtocolError as e:
        friendly, _ = classify_protocol_error(e)
        _log_error_to_disk(e)
        print(f"[错误] {friendly}", file=sys.stderr)
        return 2

    try:
        data = parse_hex_input(args.hex)
    except ProtocolError as e:
        friendly, _ = classify_protocol_error(e)
        _log_error_to_disk(e)
        print(f"[错误] {friendly}", file=sys.stderr)
        return 2

    result = parse_frame(data, cfg, direction=args.direction)
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(render_single(result))
    return 0 if result.error is None else 1


def cmd_batch(args: argparse.Namespace) -> int:
    try:
        proto_file = find_protocol_file(args.product, args.protocol_dir)
        cfg = load_protocol(proto_file)
        # 与V3.0基础协议合并
        base_cfg = get_builtin_v3()
        cfg = merge_protocol(base_cfg, cfg)
    except ProtocolError as e:
        friendly, _ = classify_protocol_error(e)
        _log_error_to_disk(e)
        print(f"[错误] {friendly}", file=sys.stderr)
        return 2

    in_path = Path(args.file)
    if not in_path.exists():
        print(f"错误: 输入文件不存在: {in_path}", file=sys.stderr)
        return 2

    results: list[ParseResult] = []
    with in_path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                data = parse_hex_input(line)
                results.append(parse_frame(data, cfg))
            except ProtocolError as e:
                # 占位结果，记录原始行号
                results.append(ParseResult(
                    product=cfg.get("product", ""),
                    raw_hex=line,
                    cmd_code="",
                    cmd_name="输入错误",
                    direction="",
                    description="",
                    error=f"[行 {lineno}] {e}",
                ))

    if args.out:
        out_path = Path(args.out)
        payload = [r.to_dict() for r in results]
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"已写入 {len(results)} 条解析结果到 {out_path}")

    print(render_batch(results))
    return 0


def cmd_protocols(args: argparse.Namespace) -> int:
    protos = list_protocols(args.protocol_dir)
    if not protos:
        print(f"在 {DEFAULT_PROTOCOL_DIR} 中没有找到协议配置文件。")
        return 0
    print(f"可用协议（目录: {DEFAULT_PROTOCOL_DIR}）：")
    print()
    for p in protos:
        if "error" in p:
            print(f"  - {p['file']}  [加载失败: {p['error']}]")
            continue
        print(f"  - {p['product']:<20} ({p['command_count']} 条命令)")
        print(f"      文件: {p['file']}")
        if p.get("description"):
            print(f"      说明: {p['description']}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    try:
        proto_file = find_protocol_file(args.product, args.protocol_dir)
        cfg = load_protocol(proto_file)
        # 与V3.0基础协议合并
        base_cfg = get_builtin_v3()
        cfg = merge_protocol(base_cfg, cfg)
    except ProtocolError as e:
        friendly, _ = classify_protocol_error(e)
        _log_error_to_disk(e)
        print(f"[错误] {friendly}", file=sys.stderr)
        return 2

    print(f"产品: {cfg.get('product')}")
    print(f"说明: {cfg.get('description', '')}")
    print(f"命令总数: {len(cfg.get('commands', []))}")
    print()
    frame = cfg.get("frame", {})
    if frame:
        print("帧结构:")
        header = frame.get("header")
        if header:
            print(f"  帧头: {header} ({frame.get('header_size', 2)}B)")
        ver = frame.get("ver")
        if ver:
            print(f"  版本: {ver}")
        if frame.get("checksum"):
            cs = frame["checksum"]
            print(f"  校验: {cs.get('algorithm', '?')} (覆盖 {cs.get('covers', '?')}, {cs.get('length', 1)}B)")
        print()

    print("命令列表:")
    for cmd in cfg.get("commands", []):
        # V3.0 双向命令
        if "request" in cmd or "response" in cmd:
            print(f"  {cmd['cmd_code']:<8} {cmd['name']}")
            if cmd.get("description"):
                print(f"            {cmd['description']}")
            req = cmd.get("request", {})
            resp = cmd.get("response", {})
            print(f"            请求: {req.get('format', '?'):<20} ({req.get('name', '')})")
            print(f"            响应: {resp.get('format', '?'):<20} ({resp.get('name', '')})")
        else:
            print(f"  {cmd['cmd_code']:<8} [{cmd.get('direction', ''):<8}] {cmd['name']}")
            if cmd.get("description"):
                print(f"            {cmd['description']}")
            for f in cmd.get("fields", []):
                unit = f" ({f['unit']})" if f.get("unit") else ""
                print(f"            - offset={f.get('offset'):<3} len={f.get('length', 1)} "
                      f"{f.get('type', 'hex'):<12} {f['name']}{unit}")

    # 属性表
    attrs = cfg.get("attributes", {})
    if attrs:
        print(f"\n属性表（共 {len(attrs)} 项）:")
        for aid, a in attrs.items():
            type_name = TYPEID_MAP.get(a.get("typeid", 0), {}).get("name", "?")
            print(f"  {aid:<6} [{type_name:<12}] {a.get('name', '')}  ({a.get('access', '')})")
            if a.get("enum"):
                for k, v in a["enum"].items():
                    print(f"            {k}: {v}")
            elif a.get("unit") or a.get("range"):
                extra = []
                if a.get("unit"):
                    extra.append(f"单位 {a['unit']}")
                if a.get("range"):
                    extra.append(f"范围 {a['range']}")
                print(f"            {' '.join(extra)}")
    return 0


# ---------- 入口 ----------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="protocol_parser",
        description="二进制定长帧协议解析器：根据每个产品的协议配置解析指令含义",
    )
    p.add_argument(
        "--protocol-dir",
        type=Path,
        default=None,
        help=f"协议配置目录（默认: {DEFAULT_PROTOCOL_DIR}）",
    )
    sub = p.add_subparsers(dest="command", required=True)

    p_parse = sub.add_parser("parse", help="解析单条指令")
    p_parse.add_argument("--product", required=True, help="产品名称")
    p_parse.add_argument("--hex", required=True, help='指令 hex 字符串，如 "A5 A5 03 24 00 00 71"')
    p_parse.add_argument("--json", action="store_true", help="以 JSON 格式输出")
    p_parse.add_argument(
        "--direction",
        choices=["request", "response"],
        default=None,
        help="显式指定方向（V3.0 双向命令），默认自动识别",
    )
    p_parse.set_defaults(func=cmd_parse)

    p_batch = sub.add_parser("batch", help="批量解析文件（每行一条指令）")
    p_batch.add_argument("--product", required=True, help="产品名称")
    p_batch.add_argument("--file", required=True, help="输入文件路径")
    p_batch.add_argument("--out", default=None, help="结果输出 JSON 文件路径")
    p_batch.set_defaults(func=cmd_batch)

    p_proto = sub.add_parser("protocols", help="列出所有可用协议")
    p_proto.set_defaults(func=cmd_protocols)

    p_show = sub.add_parser("show", help="查看某产品的协议详情")
    p_show.add_argument("--product", required=True, help="产品名称")
    p_show.set_defaults(func=cmd_show)

    # 串口实时监控
    p_serial = sub.add_parser("serial", help="串口实时采集并解析")
    p_serial.add_argument("--product", required=True, help="产品名称")
    p_serial.add_argument("--port", required=True, help="串口名称（如 COM3）")
    p_serial.add_argument("--baudrate", type=int, default=115200, help="波特率（默认 115200）")
    p_serial.add_argument("--detail", action="store_true", help="详细输出模式（默认紧凑）")
    p_serial.add_argument("--log", default=None, help="保存日志到文件")
    p_serial.add_argument("--log-mode", choices=["compact", "detail"], default="compact", help="日志格式（默认紧凑）")
    p_serial.set_defaults(func=cmd_serial)

    # 列出串口
    p_ports = sub.add_parser("ports", help="列出所有可用串口")
    p_ports.set_defaults(func=cmd_ports)

    # 粘贴交互模式
    p_paste = sub.add_parser("paste", help="交互式粘贴 hex 数据解析")
    p_paste.add_argument("--product", required=True, help="产品名称")
    p_paste.add_argument("--log", default=None, help="保存日志到文件")
    p_paste.set_defaults(func=cmd_paste)

    return p


def cmd_serial(args: argparse.Namespace) -> int:
    from .monitor import ResultLogger, run_serial_mode

    try:
        proto_file = find_protocol_file(args.product, args.protocol_dir)
        cfg = load_protocol(proto_file)
        # 与V3.0基础协议合并
        base_cfg = get_builtin_v3()
        cfg = merge_protocol(base_cfg, cfg)
    except ProtocolError as e:
        friendly, _ = classify_protocol_error(e)
        _log_error_to_disk(e)
        print(f"[错误] {friendly}", file=sys.stderr)
        return 2

    logger = None
    if args.log:
        logger = ResultLogger(args.log, mode=args.log_mode)
        logger.__enter__()

    try:
        return run_serial_mode(
            cfg=cfg,
            port=args.port,
            baudrate=args.baudrate,
            detail=args.detail,
            logger=logger,
        )
    finally:
        if logger:
            logger.__exit__(None, None, None)


def cmd_ports(args: argparse.Namespace) -> int:
    from .monitor import list_serial_ports

    ports = list_serial_ports()
    if not ports:
        print("未检测到串口。")
        return 0
    print(f"共 {len(ports)} 个串口：")
    print()
    for i, p in enumerate(ports, 1):
        print(f"  {i:2}. {p['device']:<12} {p['description']}")
        if p.get("hwid"):
            print(f"      {p['hwid']}")
    return 0


def cmd_paste(args: argparse.Namespace) -> int:
    from .monitor import ResultLogger, run_paste_mode

    try:
        proto_file = find_protocol_file(args.product, args.protocol_dir)
        cfg = load_protocol(proto_file)
        # 与V3.0基础协议合并
        base_cfg = get_builtin_v3()
        cfg = merge_protocol(base_cfg, cfg)
    except ProtocolError as e:
        friendly, _ = classify_protocol_error(e)
        _log_error_to_disk(e)
        print(f"[错误] {friendly}", file=sys.stderr)
        return 2

    logger = None
    if args.log:
        logger = ResultLogger(args.log, mode="detail")
        logger.__enter__()

    try:
        return run_paste_mode(cfg=cfg, logger=logger)
    finally:
        if logger:
            logger.__exit__(None, None, None)


def main(argv: list[str] | None = None) -> int:
    """CLI 总入口：最外层统一兜底，不允许把堆栈直接抛给用户。

    退出码：
      0 → 成功
      2 → 已知协议/配置错误（ProtocolError 子类），已打印 friendly 提示
      1 → 未知错误，friendly 提示 + error.log 路径已写入 stderr
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ProtocolError as e:
        friendly, _ = classify_protocol_error(e)
        print(f"[错误] {friendly}", file=sys.stderr)
        try:
            log_path = _log_error_to_disk(e)
            print(f"       详情已写入: {log_path}", file=sys.stderr)
        except Exception:
            pass
        return 2
    except Exception as e:  # noqa: BLE001  顶层兜底必须要广
        friendly, _ = classify_protocol_error(e)
        log_path = _log_error_to_disk(e)
        print(f"[错误] {friendly}", file=sys.stderr)
        print(f"       堆栈已写入: {log_path}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
