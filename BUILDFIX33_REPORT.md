# BuildFix33 — hardening P0/P1

基于 `v3.1.0` / `ecacfe4`。不改 UI 布局、不改协议骨架。

分支：`fix/hardening-p0-p1`

## 已直接合入源码（可直接用）

| 文件 | 内容 |
|------|------|
| `protocol_parser/auto_reply.py` | 0x01 真事务：写前快照、失败回滚、统一异常、不改 `result.fields` |
| `protocol_parser/storage.py` | stop 超时保留 `_thread`；文档化 `drain=False` |
| `protocol_parser/ui_helpers.py` | 展示层不再调用 `validate_attr_value` |

## 大文件以 patch 交付（需应用一次）

因单次 API 推送体积限制，`parser.py` / `mcu_page.py` 以 patch 形式提交：

| 文件 | 内容 |
|------|------|
| `patches/parser_strict_hex_and_cache.patch` | HEX 严格 token + builtin 锁/deepcopy |
| `patches/mcu_page_qtimer_context.patch` | QTimer 带 `self` receiver + IO 唤醒生命周期 |
| `scripts/apply_buildfix33_patches.py` | 一键应用上述两个 patch |

### 应用方式（仓库根目录）

```bash
git checkout fix/hardening-p0-p1
python scripts/apply_buildfix33_patches.py
git add protocol_parser/parser.py protocol_parser/mcu_page.py
git commit -m "fix(parser,mcu_page): apply BuildFix33 patches to source"
git push origin fix/hardening-p0-p1
```

应用后源码与本地验证一致：

- `parse_hex_input('A5x5A')` 抛 `HexParseError`
- `get_builtin_v3()` 返回 deepcopy
- 全部 `QTimer.singleShot` 带 `self` receiver

## 未改

- GUI 布局 / 列宽
- PID（BuildFix31）
- wire serialId 显示 / 日志 Typeid 字段（BuildFix32）
- RawDataWriter → GUI Signal 绑定（BuildFix31）
