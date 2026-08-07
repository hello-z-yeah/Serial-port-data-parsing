# BuildFix33 — hardening P0/P1

基于 `v3.1.0` / `ecacfe4`。不改 UI 布局、不改协议骨架。

## 修复内容

1. **auto_reply 真事务**（`protocol_parser/auto_reply.py`）
   - 写前快照旧值；写入 + 组 ACK/report 包在 try 中
   - 失败回滚全部已写属性，返回 `[]`（不发成功 ACK）
   - 两轮校验统一捕获 `(ValueError, TypeError)`
   - 恢复路径不再原地修改 `result.fields`

2. **HEX 严格 token**（`protocol_parser/parser.py`）
   - 仅允许 `(?:0x)?[0-9a-f]+` 用空白/逗号分隔
   - `A5x5A` / 孤立 `x` / `0xZZ11` 直接拒绝

3. **builtin 协议缓存**（`protocol_parser/parser.py`）
   - `_builtin_v3_lock` 保护初始化/刷新
   - `get_builtin_v3` 返回 `copy.deepcopy` 快照

4. **展示层去校验**（`protocol_parser/ui_helpers.py`）
   - `_format_attr_semantics` 不再调用 `validate_attr_value`

5. **QTimer 生命周期**（`protocol_parser/mcu_page.py`）
   - 全部 `QTimer.singleShot` 带 `self` receiver
   - IO 唤醒回调经页面方法转发，页面销毁后不再触发

6. **RawDataWriter stop 超时**（`protocol_parser/storage.py`）
   - 超时保留 `_thread`，禁止半停止 start
   - 文档明确 `drain=False` 语义与重试 stop

## 未改

- GUI 布局 / 列宽
- PID（已在 BuildFix31）
- wire serialId 显示 / 日志 Typeid 字段（已在 BuildFix32）
- storage 回调 Signal 绑定（已在 BuildFix31）
