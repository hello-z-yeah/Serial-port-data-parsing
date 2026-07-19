@echo off
chcp 65001 >nul 2>&1
setlocal

REM ============================================================
REM  串口数据解析 - 启动脚本
REM
REM  双击此文件启动程序，首次运行会自动安装依赖。
REM ============================================================

echo.
echo === 检查 Python 环境 ===
where python >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 python.exe。
    echo 请从 https://www.python.org/downloads/ 安装官方 Python，
    echo 安装时务必勾选 "Add python.exe to PATH"。
    pause
    exit /b 1
)

python --version

echo.
echo === 检查依赖 ===
python -c "import serial; import docx" 2>nul
if errorlevel 1 (
    echo 依赖未安装，正在安装...
    echo     优先从本地 packages/ 目录安装...

    if exist "packages\pyserial-3.5-py2.py3-none-any.whl" (
        echo 从本地 packages/ 目录安装...
        python -m pip install --no-index --find-links=packages pyserial python-docx lxml typing_extensions
        if errorlevel 1 (
            echo 本地安装失败，尝试从网络安装...
            echo     如遇卡顿，请检查网络或代理设置。
            python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
            if errorlevel 1 (
                echo [错误] 依赖安装失败。
                pause
                exit /b 1
            )
        )
    ) else (
        echo 本地无离线包，从网络安装...
        echo     如遇卡顿，请检查网络或代理设置。
        python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
        if errorlevel 1 (
            echo [错误] 依赖安装失败。
            pause
            exit /b 1
        )
    )
)

echo.
echo === 启动串口数据解析 ===
python exe_entry.py

if errorlevel 1 (
    echo.
    echo [错误] 程序运行出错。
    pause
)
