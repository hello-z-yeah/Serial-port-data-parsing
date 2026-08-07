# Super Max Serial Tool 3.1.0

Super Max Serial Tool（SMST）是 Windows 串口协议分析与模拟 MCU 工具，支持 HEX/ASCII 收发、产品 JSON、实时属性、自动回复、原始数据保存和协议日志。
## 3.1.0 关键变化

- 两个实时数据窗口隐藏字号控件，保留 Ctrl+鼠标滚轮字号调节逻辑。
- 实时属性名称与属性文本按可用宽度换行，行高自动展开，完整显示内容。
- 产品 JSON 导入兼容更多名称字段；无法翻译的名称保留原文，不再退化为“属性0xXX”。
- 发送面板与指令库分别维护 HEX/ASCII 格式；ASCII 按 UTF-8 文本发送并以 ASCII 显示。


## 推荐入口

直接双击项目根目录中的：

```text
SMST_Build_Manager.pyw
```

若 `.pyw` 没有正确关联到 Python，可双击：

```text
SMST_Build_Manager.vbs
```

本项目已删除旧 BAT/CMD 构建入口，避免 Windows 文件关联或安全策略导致脚本无法启动。

## 本版本重点修复

- 自动回复只在“模拟 MCU 工具 + JSON 产品 + 全局开关开启”时运行。
- 0x01 命令下发采用事务式处理：整帧校验成功后才更新属性并发送 ACK/状态同步。
- 兼容仅解析出消息 ID 的旧产品配置，可从原始帧恢复解析属性列表。
- 每次重新开启全局自动回复时恢复全部规则，避免出现“心跳能回复、命令不回复”。
- 内置产品采用版本化同步；旧 LocalAppData 产品会先备份，再更新为当前修正版。
- 串口 TX、数据写盘、停止流程均为后台线程，界面不等待驱动或磁盘操作。
- 用户数据统一保存在 `%LOCALAPPDATA%\SuperMaxSerialTool`。
- 完全移除在线更新和单 EXE 自替换。
- 全界面字体按 Windows DPI 与逻辑分辨率自适应；模拟 MCU 的实时属性、预置命令、表格和下拉列表保持一致字号。

## 用户数据位置

```text
%LOCALAPPDATA%\SuperMaxSerialTool\
├─ config\
├─ products\
│  └─ backups\       内置产品升级前的自动备份
├─ data\
└─ logs\
```

安装目录只保存程序文件和只读资源。卸载程序不会删除上述用户数据。

## 源码运行与构建

推荐 Windows 10/11 x64、Python 3.11–3.14 x64、Inno Setup 6。

在构建管理器中可执行：

- 环境检查
- 安装依赖
- 运行自动测试
- 运行源码
- 构建文件夹版 EXE
- 构建单文件便携版
- 构建 Windows 安装包

安装包输出：

```text
release\SuperMaxSerialTool_Setup_3.1.0_x64.exe
```

文件夹版输出：

```text
dist\SuperMaxSerialTool\SuperMaxSerialTool.exe
```

文件夹版必须保留整个 `dist\SuperMaxSerialTool` 目录，不能只复制 EXE。

## 命令行方式

```cmd
python SMST_Build_Manager.py diagnose
python SMST_Build_Manager.py install-deps
python SMST_Build_Manager.py test
python SMST_Build_Manager.py build-installer
```

## 主要源码

```text
protocol_parser/gui.py               主窗口与监控页面编排
protocol_parser/serial_collector.py  串口 RX/TX 工作线程
protocol_parser/auto_reply.py        自动回复与命令事务处理
protocol_parser/attr_center.py       属性状态、权限与范围校验
protocol_parser/storage.py           可靠原始数据写盘
protocol_parser/paths.py             LocalAppData 与内置产品同步
protocol_parser/session_snapshot.py  原子会话快照
protocol_parser/dpi_font.py         全局 DPI/分辨率字体适配
installer/serial_port_parser.iss     Inno Setup 安装脚本
```

详细构建说明见 `WINDOWS_BUILD_GUIDE.md`。

### BuildFix19 UI 适配

- Per-Monitor DPI v2 + PassThrough 取整策略。
- 应用级动态控件尺寸修复，按当前字体测量文字，不保留过时固定宽高。
- 标题栏工具按钮、发送面板、模拟 MCU 三栏和长对话框支持响应式换行、堆叠或滚动。

### BuildFix20 紧凑布局优化

- 串口配置栏在常见桌面宽度下保持单行，不再出现整行“开始监控”按钮。
- “更多”详情区改为紧凑两行表单，只有文件名和路径编辑框参与伸缩。
- 每次响应式重排前清除旧网格 stretch，避免“选择”等按钮异常拉长。
- 实时属性 ID 列加宽并居中，名称、属性文本和发送列重新分配空间。
