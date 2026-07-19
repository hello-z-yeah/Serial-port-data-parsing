@echo off
setlocal

REM ============================================================
REM  Protocol Parser GUI version build script
REM
REM  Prerequisites:
REM    1. Install official Python 3.11+ from https://www.python.org/downloads/
REM       When installing, CHECK these options:
REM         [x] Add python.exe to PATH
REM         [x] tcl/tk and IDLE  (expand Customize Python to enable)
REM    2. Double-click this .bat file to build the GUI exe
REM ============================================================

echo.
echo === Check Python environment ===
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] python.exe not found in PATH.
    echo Please install official Python from https://www.python.org/downloads/
    echo Be sure to check "Add python.exe to PATH" during installation.
    pause
    exit /b 1
)

python --version
python -c "import tkinter; print('tkinter OK')" 2>nul
if errorlevel 1 (
    echo [ERROR] tkinter is not available in current Python.
    echo Please install the official Python from python.org and enable tcl/tk option.
    pause
    exit /b 1
)

echo.
echo === Install dependencies (this may take 1-3 minutes, please wait) ===
echo     If stuck, check your network / proxy settings.
echo.
echo [1/3] Installing pyinstaller...
python -m pip install pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
    echo [ERROR] Failed to install pyinstaller.
    pause
    exit /b 1
)
echo.
echo [2/3] Installing pyserial...
python -m pip install pyserial -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
    echo [ERROR] Failed to install pyserial.
    pause
    exit /b 1
)
echo.
echo [3/3] Installing python-docx (for Word import feature)...
python -m pip install python-docx -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
    echo [ERROR] Failed to install python-docx.
    pause
    exit /b 1
)

echo.
echo === Build GUI exe ===
python -m PyInstaller ^
    --onefile ^
    --windowed ^
    --name ProtocolParser ^
    --hidden-import docx ^
    --hidden-import docx.opc.constants ^
    --add-data "product;product" ^
    --add-data "protocol_parser;protocol_parser" ^
    exe_entry.py

if errorlevel 1 (
    echo.
    echo [BUILD FAILED]
    pause
    exit /b 1
)

echo.
echo === Build complete ===
echo Output: %~dp0dist\ProtocolParser.exe
echo.
echo Double-click ProtocolParser.exe to launch the GUI.
echo.
pause
