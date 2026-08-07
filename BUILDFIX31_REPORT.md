# Super Max Serial Tool v3.1.0 — BuildFix31

基于用户上传的 `SMST_3.1.0.zip` / BuildFix30 源码，仅执行本轮 4 个定向补丁要求；未修改 UI 布局、串口协议骨架或 RawDataWriter 内部线程逻辑。

## 补丁 1：PID 前导零

文件：`protocol_parser/dev_info_encoder.py`

- `_pid_to_uint32()` 不再使用 `int(text, 0)`。
- `0x` 前缀明确按 16 进制解析，其余字符串强制按十进制解析。
- `00123` 正确得到 123；`0x7B` 得到 123；非法字符串继续抛 `ProtocolConfigError`。
- 新增 0x21 编码回归：PID `00123` 的 F7 字段包含 `06 F7 00 00 00 7B`。

## 补丁 2：0x01 自动回复写入二次预检

文件：`protocol_parser/auto_reply.py`

- 保持 unknown / invalid 拒绝路径、ACK 顺序与状态上报逻辑不变。
- 在任何 `set_attr_value()` 之前，对 `writable_order` 的全部属性再次调用 `validate_attr_value()`。
- 若二次预检期间任一属性失败，则此前没有发生任何写入，也不会生成 ACK，从而保持事务语义。

## 补丁 3：HEX 输入严格白名单

文件：`protocol_parser/parser.py`

- 在原有清理与 `_RE_PREFIXED_HEX` / `findall` 流程之前增加字符白名单。
- 合法：`A5 5A 03 20`、`0xA5,0x5A`。
- 非法：`0xZZ11`、`A5G5` 立即抛 `HexParseError`，不再静默清理后继续发送。
- 后续原有 HEX 清理/解析逻辑未改。

## 补丁 4：RawDataWriter 跨线程 UI hardening

检查结果：BuildFix30 当前代码已经满足要求，无需重复改写。

现有结构：

- `UiBridge.storage_error_signal = Signal(str)`
- `UiBridge.storage_drop_signal = Signal(int)`
- 两个信号分别连接 `_on_storage_error` / `_on_storage_drop`
- `RawDataWriter(on_error=...)` 仅执行 `self.bridge.storage_error_signal.emit(...)`
- `RawDataWriter(on_drop=...)` 仅执行 `self.bridge.storage_drop_signal.emit(...)`

因此后台写盘线程不会直接操作 QWidget；本轮只新增回归测试锁定该结构，没有修改布局，也没有修改 `storage.py`。

## 验证

- `python -m compileall -q protocol_parser tests exe_entry.py`：通过
- 全部自动测试：**176 passed**
- BuildFix31 定向测试：**5 passed**
- 应用身份：`Super Max Serial Tool 3.1.0 / SuperMaxSerialTool.exe`：通过

## 发布说明

为避免误运行旧代码构建出的安装包，本源码包不携带上传压缩包中的旧 `release/*.exe`。请在 Windows 上运行 `SMST_Build_Manager.pyw` 重新构建 v3.1.0 安装包。
