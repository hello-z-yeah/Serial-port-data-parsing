@echo off
chcp 65001 >nul 2>&1
setlocal

REM ============================================================
REM  串口数据解析 - 打包脚本
REM
REM  前置条件：
REM    1. 安装官方 Python 3.11+ ：https://www.python.org/downloads/
REM       安装时务必勾选以下选项：
REM         [x] Add python.exe to PATH
REM         [x] tcl/tk and IDLE（展开 Customize Python 勾选）
REM    2. 双击此文件即可打包生成 exe
REM
REM  命令行用法（由一键出安装包.bat 自动调用）：
REM    set SKIP_PAUSE=1
REM    call 打包exe.bat
REM    说明：SKIP_PAUSE=1 时脚本遇到 pause 全部跳过，适合 CI / 一键流水线
REM ============================================================

echo.
echo === 检查 Python 环境 ===
where python >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 python.exe。
    echo 请从 https://www.python.org/downloads/ 安装官方 Python，
    echo 安装时务必勾选 "Add python.exe to PATH"。
    if not defined SKIP_PAUSE pause
    exit /b 1
)

python --version
python -c "import tkinter; print('tkinter OK')" 2>nul
if errorlevel 1 (
    echo [错误] 当前 Python 没有 tkinter。
    echo 请安装 python.org 官方 Python，并勾选 tcl/tk 选项。
    if not defined SKIP_PAUSE pause
    exit /b 1
)

echo.
echo === 预打包语法自检（py_compile） ===
python -m py_compile exe_entry.py protocol_parser\parser.py protocol_parser\serial_collector.py protocol_parser\gui.py protocol_parser\cli.py protocol_parser\monitor.py protocol_parser\updater.py protocol_parser\session_snapshot.py protocol_parser\__main__.py protocol_parser\docx_importer.py protocol_parser\attr_editor.py protocol_parser\__init__.py
if errorlevel 1 (
    echo [错误] Python 语法检查失败，请修复上方错误后再打包。
    if not defined SKIP_PAUSE pause
    exit /b 1
)
echo 语法自检通过。

echo.
echo === 安装依赖（可能需要 1-3 分钟，请等待）===
echo     如遇卡顿，请检查网络或代理设置。
echo.
echo [1/3] 安装 pyinstaller...
python -m pip install pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
    echo [错误] pyinstaller 安装失败。
    if not defined SKIP_PAUSE pause
    exit /b 1
)
echo.
echo [2/3] 安装 pyserial...
python -m pip install pyserial -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
    echo [错误] pyserial 安装失败。
    if not defined SKIP_PAUSE pause
    exit /b 1
)
echo.
echo [3/3] 安装 python-docx（用于导入 Word 协议文档）...
python -m pip install python-docx -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
    echo [错误] python-docx 安装失败。
    if not defined SKIP_PAUSE pause
    exit /b 1
)

echo.
echo === 清理旧的 dist/build 目录 ===
if exist "%~dp0dist" rmdir /s /q "%~dp0dist"
if exist "%~dp0build" rmdir /s /q "%~dp0build"
if exist "%~dp0串口数据解析.exe" del /f /q "%~dp0串口数据解析.exe"
if errorlevel 1 (
    echo [警告] 清理旧文件时出现小问题，继续打包...
)

echo.
echo === 开始打包 ===
python -m PyInstaller ^
    --noconfirm ^
    --onefile ^
    --windowed ^
    --name "串口数据解析" ^
    --hidden-import docx ^
    --hidden-import docx.opc.constants ^
    --add-data "product;product" ^
    --add-data "protocol_parser;protocol_parser" ^
    exe_entry.py

if errorlevel 1 (
    echo.
    echo [打包失败]
    if not defined SKIP_PAUSE pause
    exit /b 1
)

echo.
echo === 复制 exe 到项目根目录 ===
copy /Y "%~dp0dist\串口数据解析.exe" "%~dp0串口数据解析.exe"
if errorlevel 1 (
    echo [错误] 复制失败，请手动从 dist 目录复制。
    if not defined SKIP_PAUSE pause
    exit /b 1
) else (
    echo 已复制到：%~dp0串口数据解析.exe
)

echo.
echo === 打包完成 ===
echo 输出文件：%~dp0串口数据解析.exe
echo.
echo 双击 串口数据解析.exe 即可启动程序。
echo.
if not defined SKIP_PAUSE pause

