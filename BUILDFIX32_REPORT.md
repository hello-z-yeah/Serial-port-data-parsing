# Super Max Serial Tool v3.1.0 — BuildFix32

基于 `Super Max Serial Tool v3.1.0 BuildFix31`，本轮只做两项显示层修改：

1. 实时属性表 ID 列显示线协议 `serialId`；内部 `attrid` 与业务索引保持不变。
2. 模拟 MCU 左侧数据日志中，0x10 状态上报和 0x01 命令下发的属性语义追加 `Typeid / Attrid / Data`。

未修改 UI 布局、表格列数/列宽、产品导入 ID 计算、串口收发、0x01 事务、自动回复或编码逻辑。

## 1. 实时属性表显示 wire serialId

文件：`protocol_parser/mcu_page.py`

- `refresh_attr_table()` 在刷新前调用 `build_snapshot_attrid_map(center.cfg)` 获取与 0x21 / 0x24 / 0x10 共用的 canonical 映射。
- ID 列显示 `internal attrid -> wire serialId` 映射后的值。
- `_attr_row_by_id`、复选框字典、发送输入框字典、属性上报回调仍全部使用 `entry.attrid` 内部 ID。
- 映射不可用时仅在显示层回退到内部 ID，不影响业务逻辑。
- 未启用可选补丁 C：当前 canonical 映射已能覆盖显式 `snapshot_wire_id` 和 MIOT `services/properties` 产品，无需改写 AttrStateCenter 配置。

## 2. 0x10 / 0x01 日志追加线字段

文件：`protocol_parser/ui_helpers.py`

`_format_attr_semantics()` 在原有中文属性语义后追加：

```text
Typeid:XX Attrid:XX Data:...
```

其中：

- `Typeid`：优先使用解析候选字段中的 typeid，缺失时回退产品属性 typeid。
- `Attrid`：直接使用帧中解析出的 wire attrid / serialId，不进行内部 ID 替换。
- `Data`：定长整数按线上字节宽度以大写 HEX 补齐显示；bytes 直接显示 HEX；字符串/数组保留短文本。

示例：

```text
[TX] A5 A5 03 10 00 03 02 04 05 ...
  → 状态上报 (0x10) | MCU→模组 | 照明-模式YHQ Typeid:02 Attrid:04 Data:05
```

## 验证

- BuildFix32 定向测试：3 passed
- 全部自动测试：179 passed
- `python -m compileall -q .`：通过
- 应用版本保持：3.1.0

新增回归覆盖：

- 实时属性 ID 列只改变显示值，内部行索引仍使用内部 attrid。
- 0x10：`02 04 05` 显示 `Typeid:02 Attrid:04 Data:05`。
- 0x01：消息 ID 后的 `02 04 05` 同样显示线字段，且保留消息 ID 与中文语义。

## 发布说明

本包不携带旧的 `build/` / `dist/` Windows 构建产物，避免误运行未包含 BuildFix32 修改的旧 EXE。请在 Windows 上运行 `SMST_Build_Manager.pyw` 重新构建 v3.1.0 安装包。
