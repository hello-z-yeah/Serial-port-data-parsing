; ================================================================
;  串口数据解析 - Inno Setup 安装包脚本
;
;  使用方法（推荐用「一键出安装包.bat」，不要直接手动编译）：
;    1. 先运行项目根目录的 `打包exe.bat`，产出 `串口数据解析.exe`
;    2. 再用 Inno Setup 6 的 ISCC.exe 编译本文件（一键脚本会自动做）
;
;  快速改版本/品牌：只需要改下面 #define 的几行
; ================================================================

#define MyAppName          "串口数据解析"
#define MyAppVersion       "3.0.0"
#define MyAppPublisher     "本地工具"
#define MyAppExeName       "串口数据解析.exe"
#define MyAppAssistedGUID  "{B1F3A7D8-6C9E-4F2B-9A8C-7D5E3F1A2B4C}"

[Setup]
; 同一个 GUID 保证未来升级时控制面板/卸载信息复用同一条
AppId={{#MyAppAssistedGUID}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=no
; 用户级安装（不需要管理员），目录默认可写 —— 这样导入 Word 协议/保存日志/写会话快照都不会因 UAC 失败
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
; 仅 Win64（与 README 一致：仅 Windows 64 位）
; Output: 安装包 exe 输出到项目根的 release\
OutputDir=..\release
OutputBaseFilename=串口数据解析_安装包_{#MyAppVersion}_x64
Compression=lzma2/ultra
SolidCompression=yes
WizardStyle=modern
; 让"程序和功能"里的条目更规范
UninstallDisplayIcon={app}\{#MyAppExeName}
VersionInfoVersion={#MyAppVersion}.0
VersionInfoDescription={#MyAppName} 安装程序
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}.0
VersionInfoCopyright=Copyright (C) 2026 {#MyAppPublisher}
UninstallFilesDir={app}\Uninstall
ChangesAssociations=no

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标:"; Flags: unchecked

[Files]
; 主程序（必须先用 打包exe.bat 产出项目根目录的 串口数据解析.exe）
Source: "..\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
; 产品协议 JSON（内置 + 用户自定义）：整目录搬，含子目录
Source: "..\product\*"; DestDir: "{app}\product"; Flags: ignoreversion recursesubdirs createallsubdirs
; 说明文档
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\使用说明书.md"; DestDir: "{app}"; Flags: ignoreversion
; 注意：启动程序.bat 不随安装包分发（那是开发/源码运行用的，安装版用不到）

[Icons]
; 开始菜单：主程序
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
; 开始菜单：说明文档（用记事本打开 .md，机器装了 Markdown 编辑器/VSCode 时会关联到对应程序）
Name: "{group}\使用说明书"; Filename: "{app}\使用说明书.md"
Name: "{group}\README 说明"; Filename: "{app}\README.md"
; 开始菜单：卸载
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
; 桌面快捷方式（由 tasks 控制，默认不勾选，用户可勾选）
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; 安装完成后可选"立即启动"
Filename: "{app}\{#MyAppExeName}"; Description: "现在启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 卸载时只删我们安装时明确写的、且肯定不会被用户写入新数据的文件。
; product/ 不整个删除 —— 因为用户可能在里面放了自己导入的 Word 协议 JSON，卸载时要保留他们的自定义数据。
Type: files; Name: "{app}\{#MyAppExeName}"
Type: files; Name: "{app}\README.md"
Type: files; Name: "{app}\使用说明书.md"
