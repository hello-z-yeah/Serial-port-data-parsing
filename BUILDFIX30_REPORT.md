# Super Max Serial Tool v3.1.0 — BuildFix30

## 修复目标

修复导入产品 JSON 后，模拟 MCU 对 `0x21` 设备信息请求生成的回复帧与产品导出文件中的真实设备信息不一致的问题。

## 两个帧的差异结论

用户提供的正确帧与 BuildFix29 生成帧都具有正确的本帧校验和，因此问题不是校验算法，而是设备信息数据段来源错误：

| 项目 | 正确帧 | BuildFix29 错误帧 |
|---|---:|---:|
| Data Length | `0x00BA` | `0x00B4` |
| 设备版本前缀 | `00 00 09` | `01 00 00` |
| F3 映射长度 | `0x0096`（150 B） | `0x0090`（144 B） |
| F3 映射项数 | 25 项 × 6 B | 24 项 × 6 B |
| 首项映射 | `02 00 0B 00 00 01` | `00 00 02 00 00 01` |
| 最终 CHK | `E0` | `A2` |

正确帧中的 `Base.expandRules` 已经给出了协议规定的 serialId 顺序、typeid、SIID 和 PIID。BuildFix29 未成功读取这段原始元数据时，会从界面保留的属性和 `services` 顺序重新构建 F3 表，导致：

1. 版本退回对话框默认值 `1.0.0`；
2. 被属性选择器过滤的属性不再进入 F3；
3. F3 顺序改成 JSON service/property 遍历顺序；
4. typeid、serialId、SIID/PIID 组合与设备真实映射不一致。

## 修复内容

### 1. `Base.expandRules` 成为 0x21 的权威数据源

- 导入 JSON 时提取并保存规范化的 `device_info_expand_rules`。
- 生成设备信息回复时，只要原始 JSON 存在 `Base.expandRules`，逐字节复用该扩展区。
- 属性选择、属性排序和属性表显示不再改变 0x21 的 F3 映射。
- 明确存在但格式错误的 expandRules 会给出配置错误，不再静默回退到重新生成的错误帧。

### 2. `Base.version` 成为设备信息版本前缀的权威来源

- 自动读取 `Base.version` 并保存到 `product_info.device_info_version`。
- 0x21 优先使用该值，而不是导入窗口默认的 `1.0.0`。
- 本次用户帧恢复为 `00 00 09`。

### 3. 兼容历史已保存产品

编码时可从 `source_function_json` 自动恢复元数据，无需强制重新导入。支持：

- `Base` / `base` 大小写差异；
- `expandRules` / `expand_rules`；
- `data`、`result`、`payload` 等包装层；
- 被 JSON 再编码一至两次的字符串；
- UTF-8 BOM 和常见 HEX 分隔符。

### 4. 导入与编辑路径统一

- 新导入产品会保存规范化 expandRules 和设备信息版本。
- 修改旧产品时保留已有设备信息元数据。
- 从文件加载和直接粘贴 JSON 使用同一解析逻辑。

## 回归验证

新增用户帧逐字节回归测试，验证以下情况均生成完全一致的正确帧：

- UI 中 MCU 版本仍为 `1.0.0`，但源 JSON 的 `Base.version` 为 `[0,0,9]`；
- 属性编辑器只保留少量属性；
- `services` 顺序与 F3 serial 顺序不同；
- `Base` 使用小写及 `expand_rules`；
- `source_function_json` 被双层 JSON 编码；
- 历史配置只有 source JSON，没有独立 `device_info_expand_rules` 字段。

验证结果：

- `compileall`：通过
- 自动测试：171 项全部通过
- 应用身份：`Super Max Serial Tool 3.1.0 / SuperMaxSerialTool.exe`
- 正确帧逐字节比较：通过

## 未修改内容

未修改串口收发线程、0x21 请求识别、自动回复开关、属性状态中心、快照/状态上报、UI 布局和安装程序版本。应用版本保持 `v3.1.0`。
