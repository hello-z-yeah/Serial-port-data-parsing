@echo off
chcp 65001 >nul 2>&1
setlocal ENABLEEXTENSIONS

REM ============================================================
REM  一键出安装包（绿色 exe 打包 → Inno Setup 编译安装包）
REM
REM  使用：双击本文件即可；或者命令行 call
REM  产物：项目根目录下的 release\串口数据解析_安装包_<版本>_x64.exe
REM
REM  前置：
REM    1. 已安装官方 Python 3.11+（同"打包exe.bat"的要求）
REM    2. 已安装 Inno Setup 6（推荐 6.2+），安装时务必勾选：
REM         [x] Inno Setup Preprocessor (ISPP)
REM         [x] Chinese Simplified language (中文简体语言包)
REM       官网下载：https://jrsoftware.org/isdl.php
REM       默认安装路径：C:\Program Files (x86)\Inno Setup 6\ISCC.exe
REM ============================================================

pushd "%~dp0"
cd /d "%~dp0"
REM 切到项目根（installer 的上一级）
cd ..
set "PROJECT_ROOT=%cd%"
set "INSTALLER_DIR=%PROJECT_ROOT%\installer"
set "ISS_FILE=%INSTALLER_DIR%\串口数据解析.iss"
set "RELEASE_DIR=%PROJECT_ROOT%\release"
set "SKIP_PAUSE=1"

echo.
echo ================================================================
echo  [1/3] 准备工作区：清理旧产物 + 检查必备文件
echo ================================================================
if not exist "%ISS_FILE%" (
    echo [错误] 找不到 Inno Setup 脚本：%ISS_FILE%
    echo 请确认 installer\ 目录存在且未被手动删除。
    pause
    popd
    exit /b 1
)

REM 清理 release（保留，避免删错用户别的东西，只清同名安装包 exe）
if not exist "%RELEASE_DIR%" mkdir "%RELEASE_DIR%"
if exist "%RELEASE_DIR%\串口数据解析_安装包_*.exe" (
    echo 清理旧的安装包 exe ...
    del /f /q "%RELEASE_DIR%\串口数据解析_安装包_*.exe" >nul 2>&1
)

echo.
echo ================================================================
echo  [2/3] 生成绿色 exe（调用 打包exe.bat，带 SKIP_PAUSE=1）
echo ================================================================
call "%PROJECT_ROOT%\打包exe.bat"
if errorlevel 1 (
    echo.
    echo [错误] 打包 exe 失败，请查看上方日志修复。
    pause
    popd
    exit /b 1
)
if not exist "%PROJECT_ROOT%\串口数据解析.exe" (
    echo [错误] 打包 exe 报告成功，但找不到：%PROJECT_ROOT%\串口数据解析.exe
    echo 请手动执行 打包exe.bat 检查。
    pause
    popd
    exit /b 1
)

echo.
echo ================================================================
echo  [3/3] 编译 Inno Setup 安装包（ISCC.exe）
echo ================================================================
set "ISCC_PATH="
REM 1) 命令行参数覆盖：ISCC_OVERRIDE
if defined ISCC_OVERRIDE (
    if exist "%ISCC_OVERRIDE%" set "ISCC_PATH=%ISCC_OVERRIDE%"
)
REM 2) 默认 x64 / x86 安装路径
if not defined ISCC_PATH (
    if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set "ISCC_PATH=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
)
if not defined ISCC_PATH (
    if exist "C:\Program Files\Inno Setup 6\ISCC.exe" set "ISCC_PATH=C:\Program Files\Inno Setup 6\ISCC.exe"
)
REM 3) PATH 里找（where.exe 不会像 PowerShell 一样被卡住）
if not defined ISCC_PATH (
    for /f "delims=" %%I in ('where.exe iscc 2^>nul') do (
        set "ISCC_PATH=%%I"
        goto :found_iscc
    )
)
:found_iscc

if not defined ISCC_PATH (
    echo.
    echo [错误] 找不到 Inno Setup 编译器（ISCC.exe）。
    echo 请按以下步骤安装：
    echo   1) 打开官网：https://jrsoftware.org/isdl.php
    echo   2) 下载 innosetup-6.x.x.exe（Current Release 6.x）
    echo   3) 安装时 **必须勾选**：
    echo        - [x] Inno Setup Preprocessor (ISPP)
    echo        - [x] Chinese Simplified language（中文简体）
    echo   4) 默认安装完成后，再重新运行本脚本。
    echo.
    echo 如果你已经安装在自定义路径，请在本命令行先执行：
    echo      set "ISCC_OVERRIDE=D:\Your\Path\Inno Setup 6\ISCC.exe"
    echo   然后再次双击本 bat。
    echo.
    pause
    popd
    exit /b 1
)
echo 使用 ISCC.exe：%ISCC_PATH%

cd /d "%INSTALLER_DIR%"
"%ISCC_PATH%" /Qp "%ISS_FILE%"
set "ISCC_RC=%ERRORLEVEL%"
cd /d "%PROJECT_ROOT%"

if not "%ISCC_RC%"=="0" (
    echo.
    echo [错误] Inno Setup 编译失败，错误码 %ISCC_RC%。
    echo.
    echo 常见原因：
    echo   1) 缺少中文语言包 ^(ChineseSimplified.isl^) —— 请重新运行 Inno Setup 安装程序，
    echo      点"修改"，勾选"Chinese Simplified language"后再试。
    echo   2) 项目根目录缺少 串口数据解析.exe 或 product\ 协议目录。
    echo   3) 安装路径的 {app}\product 写权限问题（本脚本默认用户级安装，一般不会发生）。
    echo.
    pause
    popd
    exit /b 1
)

REM 确认产物存在
for %%F in ("%RELEASE_DIR%\串口数据解析_安装包_*_x64.exe") do (
    set "SETUP_EXE=%%~fF"
    goto :found_setup
)
set "SETUP_EXE="
:found_setup

echo.
echo ================================================================
echo  全部完成！
echo ================================================================
echo 绿色 exe    ：%PROJECT_ROOT%\串口数据解析.exe
echo 安装包 exe  ：%SETUP_EXE%
echo 安装包大小  ：
if defined SETUP_EXE (
    for %%A in ("%SETUP_EXE%") do echo     %%~zA 字节  ( %%~zA 字节 ≈ 把数字除以 1048576 看 MB )
)
echo.
echo 安装包使用说明：
echo   * 双击后默认安装到 当前用户的 %%LocalAppData%%\Programs\串口数据解析
echo     - 不需要管理员权限
echo     - 安装目录默认可写，导入 Word 协议 / 保存日志 / 冷更新写快照都不会失败
echo   * 开始菜单会新增"串口数据解析"分组（主程序 + 使用说明书 + README + 卸载）
echo   * 可选：安装向导末尾可勾选"创建桌面快捷方式"
echo   * 卸载：从"设置 - 应用 - 安装的应用"或开始菜单里的"卸载 串口数据解析"执行；
echo          product\ 目录下用户自己导入的协议 JSON **不会被删除**。
echo.
popd
if /i not "%1"=="--no-pause" pause
endlocal
