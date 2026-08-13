# SerialX 3.1.8

SerialX（原名 Super Max Serial Tool / SMST）是 Windows 串口协议分析与模拟 MCU 工具，支持 HEX/ASCII 收发、产品 JSON、实时属性、自动回复、原始数据保存和协议日志。

详细使用说明见 **[USER_GUIDE.md](USER_GUIDE.md)**。

## 3.1.8 关键变化

- 品牌重命名为 **SerialX**，安装包、exe、显示名全部更新。
- 解析日志文本增加分层背景高亮：箭头无背景、命令名/方向带背景、`属性id:XX 值:XX` 前缀带背景。
- 0x24 快照日志采用快照格式（`属性id:XX 值:XX 名称值`），属性前缀带背景高亮。
- UI 全量 Fluent 风格灰色 Tooltip，级别标签采用圆角背景样式。
- 安装脚本改用 `{#MyAppBaseName}` 变量，避免硬编码路径名。

## 构建入口

推荐双击项目根目录中的：

```text
SMST_Build_Manager.pyw
```

或双击 `SMST_Build_Manager.vbs`（若 `.pyw` 未正确关联）。

命令行方式：

```cmd
python SMST_Build_Manager.py diagnose
python SMST_Build_Manager.py install-deps
python SMST_Build_Manager.py test
python SMST_Build_Manager.py build-installer
```

## 输出路径

| 产物 | 路径 |
|---|---|
| 安装包 | `release\SerialXSetup3.1.8_x64.exe` |
| 文件夹版 | `dist\SerialX\SerialX.exe` |
| 便携版 | `dist\SerialX_Portable.exe` |

文件夹版必须保留整个 `dist\SerialX` 目录，不能只复制 EXE。

## 用户数据位置

```text
%LOCALAPPDATA%\SST_串口工具\
├─ config\
├─ products\
│  └─ backups\
├─ data\
└─ logs\
```

## 开发要求

- Windows 10/11 x64
- Python 3.11–3.14 x64
- Inno Setup 6

## 主要源码

```text
protocol_parser/gui.py               主窗口与监控页面编排
protocol_parser/mcu_page.py          模拟 MCU 页面
protocol_parser/serial_collector.py   串口 RX/TX 工作线程
protocol_parser/auto_reply.py        自动回复与命令事务处理
protocol_parser/attr_center.py       属性状态、权限与范围校验
protocol_parser/storage.py           可靠原始数据写盘
protocol_parser/paths.py             LocalAppData 与内置产品同步
protocol_parser/session_snapshot.py  原子会话快照
protocol_parser/dpi_font.py          全局 DPI/分辨率字体适配
installer/serial_port_parser.iss     Inno Setup 安装脚本
```

## 版本历史

### 3.1.0（SMST）

- 实时数据窗口隐藏字号控件（Ctrl+滚轮调节）
- 实时属性名称按可用宽度换行
- 发送面板 HEX/ASCII 独立维护
- 自动回复事务式处理
- 内置产品版本化同步