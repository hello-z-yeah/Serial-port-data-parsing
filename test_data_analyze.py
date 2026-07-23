import sys
sys.path.insert(0, ".")

data_hex = "01 00 00 06 F7 00 00 85 03 0B F5 00 11 73 63 63 2E 62 68 66 5F 6C 69 67 68 74 2E 79 62 31 0E F3 00 36 00 00 02 00 00 01 00 01 02 00 00 02 00 02 03 00 00 01 02 03 03 00 00 02 02 04 03 00 00 03 00 05 04 00 00 01 02 06 04 00 00 02 02"

data = bytes.fromhex(data_hex.replace(" ", ""))

print("数据总长度:", len(data), "字节")
print()

# 尝试按 (typeid + attrid + ...) 格式解析
TYPEID_MAP = {
    1: {"name": "uint8", "size": 1},
    2: {"name": "int8", "size": 1},
    3: {"name": "uint16", "size": 2},
    4: {"name": "int16", "size": 2},
    5: {"name": "uint32", "size": 4},
    6: {"name": "int32", "size": 4},
    11: {"name": "uint8_array", "size": -1},
    12: {"name": "uint16_array", "size": -1},
    13: {"name": "string", "size": -1},
    14: {"name": "raw", "size": -1},
    15: {"name": "bool", "size": 1},
}
TYPEID_FORCE_REPORT_BIT = 0x80

pos = 0
while pos < len(data):
    if pos + 2 > len(data):
        print(f"[{pos:3d}] 残留: {data[pos:].hex(' ')}")
        break
    
    type_byte = data[pos]
    attrid = data[pos + 1]
    typeid = type_byte & ~TYPEID_FORCE_REPORT_BIT
    force = bool(type_byte & TYPEID_FORCE_REPORT_BIT)
    
    type_info = TYPEID_MAP.get(typeid)
    type_name = type_info["name"] if type_info else f"?{typeid}"
    
    if typeid in (11, 12, 13, 14):
        if pos + 3 > len(data):
            print(f"[{pos:3d}] type=0x{type_byte:02X}({type_name}) attrid=0x{attrid:02X} 长度越界")
            break
        value_len = data[pos + 2]
        value_start = pos + 3
    else:
        value_len = type_info["size"] if type_info else 1
        value_start = pos + 2
    
    value_end = value_start + value_len
    if value_end > len(data):
        print(f"[{pos:3d}] type=0x{type_byte:02X}({type_name}) attrid=0x{attrid:02X} 值越界(需要{value_len}B)")
        break
    
    value_chunk = data[value_start:value_end]
    hex_str = value_chunk.hex(" ")
    
    # 解码
    extra = ""
    if typeid == 3:  # uint16_le
        val = int.from_bytes(value_chunk, "little")
        extra = f" = {val}"
    elif typeid == 4:  # int16_le
        val = int.from_bytes(value_chunk, "little", signed=True)
        extra = f" = {val}"
    elif typeid == 5:  # uint32_le
        val = int.from_bytes(value_chunk, "little")
        extra = f" = {val}"
    elif typeid == 6:  # int32_le
        val = int.from_bytes(value_chunk, "little", signed=True)
        extra = f" = {val}"
    elif typeid == 13:  # string
        try:
            s = value_chunk.decode("utf-8", errors="replace")
            extra = f' = "{s}"'
        except:
            pass
    elif typeid == 1 or typeid == 15:
        extra = f" = {value_chunk[0]}"
    elif typeid == 2:
        val = value_chunk[0] if value_chunk[0] < 128 else value_chunk[0] - 256
        extra = f" = {val}"
    
    force_str = " [强制]" if force else ""
    print(f"[{pos:3d}] type=0x{type_byte:02X}({type_name:<12s}) attrid=0x{attrid:02X} len={value_len:2d}  val={hex_str:<25s}{extra}{force_str}")
    
    pos = value_end
