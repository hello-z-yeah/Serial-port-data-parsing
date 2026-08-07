# Windows 构建说明

## 图形化构建

双击：

```text
SST_Build_Manager.pyw
```

若 `.pyw` 没有正确关联到 Python，双击：

```text
SST_Build_Manager.vbs
```

按顺序执行：

1. 环境检查
2. 安装依赖
3. 运行自动测试
4. 构建 Windows 安装包

生成位置：

```text
release\SST_串口工具_Setup_3.1.0_x64.exe
```

## 命令行构建

在项目目录打开终端：

```cmd
python SST_Build_Manager.py diagnose
python SST_Build_Manager.py install-deps
python SST_Build_Manager.py test
python SST_Build_Manager.py build-installer
```

也可以指定真实解释器：

```cmd
"C:\Users\90780\AppData\Local\Programs\Python\Python314\python.exe" SST_Build_Manager.py build-installer
```

## 所需环境

- Windows 10/11 x64
- Python 3.11–3.14 x64
- Inno Setup 6
- 可用的 tkinter（Python 官方安装包默认包含）

构建管理器会自动检查并安装 Python 依赖。Inno Setup 需要单独安装。

## 安装脚本兼容性

安装脚本不再引用可选的 `ChineseSimplified.isl`，因此不会因 Inno Setup 未安装额外语言包而编译失败。安装器使用 Inno Setup 内置默认语言，任务名和软件说明仍可显示中文。

## 发布前检查

建议在无 Python 的干净 Windows 虚拟机中验证：

- 安装、启动、覆盖升级和卸载
- CH340/USB 串口连接与拔插
- 自动回复心跳、设备信息、快照和 0x01 命令
- 长时间接收与原始数据分卷
- 磁盘满、目录无权限和串口被占用
- LocalAppData 产品备份与内置产品升级
