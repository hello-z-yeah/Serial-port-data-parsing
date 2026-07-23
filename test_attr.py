import sys
sys.path.insert(0, ".")
from protocol_parser.parser import _parse_attr_list, load_protocol

data_hex = "01 00 00 06 F7 00 00 85 03 0B F5 00 11 73 63 63 2E 62 68 66 5F 6C 69 67 68 74 2E 79 62 31 0E F3 00 36 00 00 02 00 00 01 00 01 02 00 00 02 00 02 03 00 00 01 02 03 03 00 00 02 02 04 03 00 00 03 00 05 04 00 00 01 02 06 04 00 00 02 02"
data = bytes.fromhex(data_hex.replace(" ", ""))

cfg = load_protocol("product/v3_serial.json")
results = _parse_attr_list(data, cfg, force_report=False)

pos = 0
for r in results:
    name = r.name
    tp = r.type
    text = r.text
    off = r.offset
    ln = r.length
    raw_bytes = r.raw
    children = r.children or []
    chi = ""
    if children:
        c0 = children[0]
        chi = f"  [typeid={c0.get('typeid')} attrid={c0.get('attrid')}]"
    hex_val = raw_bytes.hex(" ") if raw_bytes else ""
    print(f"[{off:3d}] {name:<22s} type={tp:<12s} text={text} (raw={hex_val}){chi}")
