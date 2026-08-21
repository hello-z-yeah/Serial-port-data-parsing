# SerialX 3.3.3

SerialX（原名 Super Max Serial Tool / SMST）是 Windows 串口协议分析与模拟 MCU 工具，支持 HEX/ASCII 收发、产品 JSON、实时属性、自动回复、原始数据保存和协议日志。

详细使用说明见 **[USER_GUIDE.md](USER_GUIDE.md)**。

## 3.3.3 关键变化

- 新增监听工具页，支持 HEX/ASCII 规则匹配、记录高亮与 Word 导出。
- 会话偏好安全恢复；不会自动打开串口或恢复循环发送。
- 在线更新使用单实例状态机、HTTPS 备用源及安装包 SHA-256 校验。
- GUI、CLI 与多串口管理器统一使用优化版串口采集实现。
- 补齐 F5、Shift+F5、Ctrl+F、Ctrl+L、Ctrl+S 与 Ctrl+Shift+E 快捷键。

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
| 安装包 | `release\SerialXSetup3.3.3_x64.exe` |
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
protocol_parser/serial_collector_optimized.py  生产串口 RX/TX 与解析工作线程
protocol_parser/serial_collector.py   帧同步与旧集成兼容实现
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