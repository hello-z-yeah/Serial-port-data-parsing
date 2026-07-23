import sys
sys.path.insert(0, ".")

# 先去掉3字节版本号
data_hex = "01 00 00 06 F7 00 00 85 03 0B F5 00 11 73 63 63 2E 62 68 66 5F 6C 69 67 68 74 2E 79 62 31 0E F3 00 36 00 00 02 00 00 01 00 01 02 00 00 02 00 02 03 00 00 01 02 03 03 00 00 02 02 04 03 00 00 03 00 05 04 00 00 01 02 06 04 00 00 02 02"
data = bytes.fromhex(data_hex.replace(" ", ""))

# 按用户描述的结构分析：
# 01 00 00 → 属性单元 1 (typeid=0x01, attrid=0x00, val=0x00)
# 06 F7 → typeid=6(int32), attrid=0xF7 (PID)
# 00 00 85 03 → PID值，小端 → 0x03850000 = ? 不对
# 等一下: 00 00 85 03 小端 → 0x03850000 = 59047936, 但用户期望34051=0x8503
# 哦! PID是 00 00 85 03 → 如果是uint32但只有2字节有效: 00 00 是高字节, 85 03 是低字节
# 0x00008503 = 34051 → 对的! 这是大端(big-endian)!

print("=== 按用户描述重新分析 ===")
print()

# 前3字节版本号跳过
ver = data[:3]
print(f"版本: {ver[0]}.{ver[1]}.{ver[2]}")
print()

rest = data[3:]
pos = 0

print("rest =", rest.hex(" "))
print()

# 假设:
# 06 F7 → typeid=0x06 (int32), attrid=0xF7 (PID)
# 00 00 85 03 → 4字节值, 大端 = 0x00008503 = 34051 ✓
pid_bytes = rest[pos+2:pos+6]
pid = int.from_bytes(pid_bytes, "big")
print(f"PID (0xF7): 字节={pid_bytes.hex(' ')} → 大端={pid} = 0x{pid:08X}")
pos += 6
print(f"pos now {pos}: 剩余 {rest[pos:].hex(' ')}")
print()

# 0B F5 → typeid=0x0B(uint8_array? 但11是变长有len字段), attrid=0xF5 (MODEL)
# 00 11 → len=17字节
type_byte = rest[pos]
attrid = rest[pos+1]
len_byte = rest[pos+2]
print(f"MODEL 标记: type=0x{type_byte:02X}, attrid=0x{attrid:02X}, len={len_byte}=0x{len_byte:02X}")
model_bytes = rest[pos+3:pos+3+len_byte]
model_str = model_bytes.decode("ascii")
print(f"MODEL值: {model_bytes.hex(' ')} → ASCII='{model_str}'")
pos += 3 + len_byte
print(f"pos now {pos}: 剩余 {rest[pos:].hex(' ')}")
print()

# 0E F3 → typeid=0x0E(raw, 变长), attrid=0xF3
# 00 36 → len=54字节
type_byte = rest[pos]
attrid = rest[pos+1]
len_byte = rest[pos+2]
print(f"0xF3 标记: type=0x{type_byte:02X}, attrid=0x{attrid:02X}, len={len_byte}=0x{len_byte:02X}")
payload = rest[pos+3:pos+3+len_byte]
print(f"payload({len(payload)}B): {payload.hex(' ')}")
pos += 3 + len_byte
print(f"pos now {pos}, 剩余长度={len(rest)-pos}")
print()

print("=== 解析54字节payload为属性列表 ===")
ppos = 0
while ppos < len(payload):
    if ppos + 2 > len(payload):
        print(f"  [{ppos:3d}] 残留: {payload[ppos:].hex(' ')}")
        break
    t = payload[ppos]
    a = payload[ppos+1]
    
    type_sizes = {1:1, 2:1, 3:2, 4:2, 5:4, 6:4, 15:1}
    var_len_types = {11, 12, 13, 14}
    
    if t in var_len_types:
        if ppos + 3 > len(payload):
            break
        vl = payload[ppos+2]
        vs = ppos + 3
    else:
        vl = type_sizes.get(t, 1)
        vs = ppos + 2
    ve = vs + vl
    if ve > len(payload):
        print(f"  [{ppos:3d}] type=0x{t:02X} attrid=0x{a:02X} 越界")
        break
    vc = payload[vs:ve]
    
    extra = ""
    if t == 1:
        extra = f" = {vc[0]}"
    elif t == 2:
        v = vc[0] if vc[0] < 128 else vc[0] - 256
        extra = f" = {v}"
    elif t == 3:
        v = int.from_bytes(vc, "little")
        extra = f" = {v} (小端)"
        vb = int.from_bytes(vc, "big")
        extra += f" / {vb}(大端)"
    elif t == 4:
        v = int.from_bytes(vc, "little", signed=True)
        extra = f" = {v} (小端)"
    elif t == 5:
        v = int.from_bytes(vc, "little")
        extra = f" = {v} (小端)"
        vb = int.from_bytes(vc, "big")
        extra += f" / {vb}(大端)"
    elif t == 6:
        v = int.from_bytes(vc, "little", signed=True)
        extra = f" = {v} (小端)"
    
    print(f"  [{ppos:3d}] type=0x{t:02X} attrid=0x{a:02X} len={vl} val={vc.hex(' ')}{extra}")
    ppos = ve
