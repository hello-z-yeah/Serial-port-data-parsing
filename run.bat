@echo off
setlocal

REM ============================================================
REM  Protocol Parser - Quick Start Script
REM
REM  Double-click this file to launch the GUI protocol parser.
REM  First run will automatically install dependencies.
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

echo.
echo === Check dependencies ===
python -c "import serial; import docx" 2>nul
if errorlevel 1 (
    echo Dependencies not found, installing...
    echo     If stuck, check your network / proxy settings.
    python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    if errorlevel 1 (
        echo [ERROR] Failed to install dependencies.
        pause
        exit /b 1
    )
)

echo.
echo === Launch Protocol Parser ===
python exe_entry.py

if errorlevel 1 (
    echo.
    echo [ERROR] Program exited with error.
    pause
)
