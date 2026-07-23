import sys
sys.path.insert(0, ".")
from protocol_parser.parser import parse_frame, load_protocol, ParseResult

def print_result(r: ParseResult, title: str) -> None:
    print("=" * 60)
    print(title)
    print("=" * 60)
    ts = "cmd_code"
    cs = "✓" if r.checksum_ok else "✗" if r.checksum_ok is False else " "
    status = "OK" if r.error is None else "ERR"
    print(f"{status} {cs} {r.cmd_code}  {r.cmd_name}")
    if r.direction:
        print(f"  [{r.direction}]")
    print(f"  原始: {r.raw_hex}")
    if r.error:
        print(f"  错误: {r.error}")
    for f in r.fields:
        ftype = f.get("type", "")
        fname = f.get("name", "")
        ftext = f.get("text", "")
        if ftype == "separator":
            print(f"  {fname}")
        else:
            print(f"  · {fname:<22} {ftext}")
    print()

# 0x21 设备信息
data21 = bytes.fromhex("A5 A5 03 21 00 58 01 00 00 06 F7 00 00 85 03 0B F5 00 11 73 63 63 2E 62 68 66 5F 6C 69 67 68 74 2E 79 62 31 0E F3 00 36 00 00 02 00 00 01 00 01 02 00 00 02 00 02 03 00 00 01 02 03 03 00 00 02 02 04 03 00 00 03 00 05 04 00 00 01 02 06 04 00 00 02 02".replace(" ", ""))

# 0x24 快照
data24 = bytes.fromhex("A5 A5 03 24 00 1B 00 00 00 00 01 00 00 02 00 02 03 00 02 04 00 00 05 00 02 06 01 02 07 01 00 08 01 BB".replace(" ", ""))

cfg = load_protocol("product/v3_serial.json")

r = parse_frame(data21, cfg, direction="response")
print_result(r, "0x21 设备信息 (当前解析)")

r2 = parse_frame(data24, cfg, direction="response")
print_result(r2, "0x24 快照 (当前解析)")

