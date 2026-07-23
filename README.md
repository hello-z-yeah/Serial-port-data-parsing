# 串口数据解析（V3.0 全双工协议型分析器 + 会话快照冷更新）

> 协议型全双工串口分析工具：既懂 V3.0 协议的**帧结构 + 命令字 + CRC 校验**，又支持毫秒级**双向收发**（被动监听 + 主动指令发送/周期循环下发）。程序在线升级时，会自动把串口号、波特率、正在循环发送的指令等运行状态**写入会话快照**，新程序启动后「无感恢复」，用户侧几乎察觉不到被打断。

---

## ✨ 主要特性一览

| 能力 | 说明 |
|------|------|
| 📡 **串口实时监控（RX）** | 按 JSON 协议解析串口实时流：帧头、命令字、长度、校验、属性块一键拆解；支持 HEX / ASCII 双模式、详细 / 紧凑双视图 |
| 🚀 **指令发送（TX，三选一）** | ① 协议模式（自动组帧 + CRC，用户只填 CmdID + fields JSON）② Raw HEX ③ Raw ASCII（UTF-8） |
| ⏱ **毫秒级周期循环** | 三种模式皆可循环下发，最小间隔 10ms；停止监控/串口异常时自动中止，避免"假运行" |
| 🎨 **TX / RX 同屏毫秒级区分** | 发送帧用红色粗体 `[TX]` 标签；状态栏 `RX N / TX M`；日志 + 原始数据落盘统一带 `[RX]/[TX]` 前缀 |
| 🧊 **会话快照冷更新（无感断点）** | 点「检查更新」→ 后台校验版本元数据 + SHA256 比对 → 安全停串口 + 写快照 → 独立进程替换 exe + 重启 → **自动恢复：界面参数 + 串口 + 正在循环的指令** |
| 📄 **Word 协议文档一键导入** | .docx → 自动提取帧结构表 / 命令字表 / 属性表 → `product/产品名.json` |
| 📂 **多产品协议切换** | 顶部下拉一键切协议；放 JSON 到 `product/` 即可被识别；支持自定义扩展 |
| 📦 **一键出安装包** | `installer/一键出安装包.bat`：PyInstaller 单文件 → Inno Setup 6 安装包（开始菜单 / 桌面图标 / 卸载信息）；卸载**保留用户自定义 product/ 协议** |
| 🧪 **命令行 + GUI 双形态** | GUI 适合现场；CLI 支持 `protocols / show / parse / batch / serial / paste` 六种子命令，便于自动化 |

> 🪟 平台：**仅 Windows 64 位**（单文件 exe / 安装包均 x64）

---

## 🚀 三种使用方式（任选其一）

### 🪟 方式 A：安装版 exe（推荐给最终用户 / 同事 / 客户）

有"下一步下一步"安装向导、桌面快捷方式可选、**控制面板里一键卸载**、重装不丢自定义协议。

1. 拿到或自己生成安装包：`release\串口数据解析_安装包_3.0.0_x64.exe`（自己生成的方法见「🛠️ 构建与打包」一节）
2. 双击安装包 → 默认装到：  
   ```
   %LocalAppData%\Programs\串口数据解析
   ```
   （这是**用户级安装**，不需要管理员权限；导入 Word 协议 / 保存日志 / 写更新快照都不会因 UAC 失败）
3. 开始菜单 → `串口数据解析` → 启动；或安装向导最后勾选「创建桌面快捷方式」。

### 🟢 方式 B：绿色版 exe（拷贝到哪都能跑，不写注册表）

适合：临时发给现场工程师；U 盘带走；不想在目标机上"安装"。

1. 拿到或自己生成 `串口数据解析.exe`（自己生成：双击项目根 `打包exe.bat`）
2. 把 exe 放到任意非 `C:\Program Files\` 的目录（推荐放在用户目录 / D 盘 / 桌面）
3. 双击运行：首次会在 exe 同目录建 `product/` 并复制默认协议。

### 🐍 方式 C：Python 源码运行（开发 / 调试 / 改代码）

**前置**：官方 Python **3.11+**（安装时必勾：`Add python.exe to PATH` + `tcl/tk and IDLE`）。

```bash
# 1. 安装依赖（可选：国内镜像加速）
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 2. 启动 GUI
python exe_entry.py
```

或者直接双击根目录的 [启动程序.bat](file:///d:/测试/工具/串口解析/Serial-port-data-parsing/启动程序.bat)（首次会自动装依赖，网络异常时还会尝试 `packages/` 下的离线 whl）。

---

## 📖 文档索引（全部在「使用说明书」对应章节）

| 你想做什么 → 看使用说明书哪章 | 章节 |
|------|------|
| 主界面 2 个 Tab 是啥？顶部工具栏每个按钮都干啥？ | 三、界面功能详解 / 3.1 顶部工具栏 |
| 串口实时里打开/关闭串口？RX/TX 在界面上怎么区分？ | 3.3 串口实时 Tab（接收 + 解析 + RX/TX 同屏） |
| 怎么发指令？协议模式的 fields JSON 怎么写？Raw HEX / ASCII 怎么切？毫秒级循环怎么开？ | 3.4 指令发送 Tab（协议组包 + Raw + 毫秒级周期循环） |
| 日志 / 原始数据两种保存有啥区别？会不会自动分割？ | 3.5 日志与原始数据保存（双写入 + RX/TX 区分） |
| 导入 Word .docx 协议文档 / 手写 JSON 协议 | 第四章 导入产品协议 |
| 「检查更新」到底干了啥？为啥升级回来串口还开着、循环还发着？ | 第五章 会话快照冷更新（无感断点续接） |
| 命令行有哪些用法？怎么批量解析文件？怎么 CLI 开串口？ | 第六章 命令行模式（高级用户） |
| 项目里哪个文件是干啥的？installer/ 是啥？release/ 什么时候有？ | 第七章 项目文件说明 |
| 常见 9 问：exe 打不开 / JSON 解析失败 / 发着发着循环停了 / 安装版绿色版选哪个 / 安装版 product 在哪？… | 第八章 常见问题 |
| 协议 JSON 的 `frame / checksum / commands / attributes` 各字段参考 | 第九章 协议 JSON 配置参考 |

👉 **完整详细文档：[使用说明书.md](file:///d:/测试/工具/串口解析/Serial-port-data-parsing/使用说明书.md)**

---

## 🛠️ 构建与打包（产出绿色 exe / 安装版 exe）

### ① 只想要绿色 exe（`串口数据解析.exe`）

双击项目根的 [打包exe.bat](file:///d:/测试/工具/串口解析/Serial-port-data-parsing/打包exe.bat) 即可。  
新版脚本会做这几件事（全部自动）：
1. `py_compile` 全量语法自检（打包前先拦语法错误，避免白等 2 分钟）
2. 清理旧 `dist/` / `build/` / 根目录旧 exe
3. 装 PyInstaller + pyserial + python-docx（清华源镜像加速）
4. PyInstaller 单文件 `--onefile --windowed`，内嵌 `product/` + `protocol_parser/`
5. 自动把 `dist\串口数据解析.exe` 复制到项目根（打包 bat 报告路径就是你能直接双击的那个）

> 如果你是从一键安装包脚本内部调用，脚本会 `set SKIP_PAUSE=1`，全部流程不会被 `pause` 卡住；手动双击仍然会在结束时 pause 方便你看日志。

### ② 想要带"下一步/卸载/快捷方式"的安装版 exe

你只需要**多装一个工具**：Inno Setup 6（免费，一次安装反复使用）

#### Step 1：装 Inno Setup 6
下载页：<https://jrsoftware.org/isdl.php> → 下载 `innosetup-6.x.x.exe`（Current Release，不要 RC）→ **安装时必勾 2 项**：
- `[x] Inno Setup Preprocessor (ISPP)`
- `[x] Chinese Simplified language`（中文简体语言包，否则编译 .iss 会报找不到 `ChineseSimplified.isl`）

默认装到 `C:\Program Files (x86)\Inno Setup 6\` 就行，脚本会自动找。

#### Step 2：一键出安装包
双击 [installer\一键出安装包.bat](file:///d:/测试/工具/串口解析/Serial-port-data-parsing/installer/一键出安装包.bat)，它内部串起来这 3 步：
```
清理旧安装包  →  [SKIP_PAUSE=1] call 打包exe.bat  →  ISCC.exe 编译 installer\串口数据解析.iss
```
产物输出：
```
release\串口数据解析_安装包_3.0.0_x64.exe
```

> 自定义 ISCC 路径支持：如果你把 Inno Setup 装到了别的盘，先在命令行 `set "ISCC_OVERRIDE=D:\你的路径\Inno Setup 6\ISCC.exe"` 再跑一键 bat。

---

## ⚙️ 环境与依赖

| 维度 | 要求 |
|------|------|
| 操作系统 | Windows 10 / 11 x64 （安装包 / 绿色 exe / 源码均只测试 x64） |
| 源码运行 Python | 3.11+（官方 python.org 版本，带 tcl/tk） |
| Python 依赖 | `pyserial`、`python-docx`、`pyinstaller`（打包时） |
| 生成安装包 | Inno Setup 6（勾 ISPP + Chinese Simplified language） |

---

## 📁 项目结构速览

```
Serial-port-data-parsing/
├── exe_entry.py                  ← 程序/GUI 入口（打包 & python exe_entry.py 都走它）
├── protocol_parser/              ← 核心代码
│   ├── parser.py                 ← V3.0 协议解析器 + encode_frame 编码器（对称）
│   ├── serial_collector.py       ← 串口采集 + send/send_raw 线程安全写 + on_tx_sent
│   ├── gui.py                    ← Tk 主界面（Notebook: 串口实时 / 指令发送）
│   ├── updater.py                ← 冷更新：版本元数据校验 + SHA256 + 静默下载 + 替换
│   ├── session_snapshot.py       ← 会话快照数据类 + 读写（扩展到 TX 循环状态）
│   ├── cli.py / monitor.py / __main__.py / __init__.py
│   ├── docx_importer.py          ← Word .docx → 产品 JSON
│   └── attr_editor.py
├── product/                      ← 产品协议 JSON
│   ├── v3_serial.json            ← 串口接入标准协议 V3.0（默认）
│   └── _template.json            ← 新建协议的模板
├── installer/
│   ├── 串口数据解析.iss            ← Inno Setup 安装包脚本
│   └── 一键出安装包.bat            ← 绿色 exe + ISCC 一条链路
├── release/                      ← 安装包 exe 输出目录（跑一键脚本后生成）
├── 启动程序.bat                     ← 源码模式一键启动
├── 打包exe.bat                      ← 绿色单文件 exe 打包（先语法自检 + 清旧产物）
├── requirements.txt
├── README.md                     ← 就是你现在看的这份（入口页）
└── 使用说明书.md                   ← 详细 10 章文档（上面「文档索引」都指向它）
```

---

## 📝 版本速览

### V3.0（当前版本）
- 全双工主架构落地：`send()/send_raw() + 写锁 + on_tx_sent`，TX/RX 毫秒级同屏区分
- Notebook 双 Tab：串口实时 + 指令发送；三选一发送模式 + 最小 10ms 周期循环
- `encode_frame` 协议组包对称于 `parse_frame`；fields JSON 支持 attrs 简写三元组 + `__raw_payload__` 覆盖
- 会话快照冷更新：扩展 7 个发送状态字段，串口打开成功才恢复循环
- 一键出安装包流水线：`installer/串口数据解析.iss` + `一键出安装包.bat`；用户级安装路径（lowest + LocalAppData）；卸载保留 product 自定义协议
- 打包脚本加固：py_compile 全量自检 + 清旧 + `SKIP_PAUSE` 支持 CI/一键

### V2.x（历史能力已保留）
- 粘贴解析 / 批量解析 / CLI 命令行 6 子命令
- Word .docx 协议导入 / 多产品协议切换
- 串口实时监控 + 日志保存 + PyInstaller 绿色 exe
