; Fast-start onedir installer
#define MyAppName          "Super Max Serial Tool"
#define MyAppVersion       "3.1.0"
#define MyAppPublisher     "Super Max"
#define MyAppExeName       "SuperMaxSerialTool.exe"
#define MyAppAssistedGUID  "{{B1F3A7D8-6C9E-4F2B-9A8C-7D5E3F1A2B4C}"

[Setup]
AppId={#MyAppAssistedGUID}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
OutputDir=..\release
OutputBaseFilename=SuperMaxSerialTool_Setup_{#MyAppVersion}_x64
Compression=lzma2/ultra
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\resources\lkl.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
VersionInfoVersion={#MyAppVersion}.0
VersionInfoDescription={#MyAppName} 安装程序
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}.0
UninstallFilesDir={app}\Uninstall

; 不依赖可选语言包。Inno Setup 将使用内置默认语言，
; 中文任务名和说明仍由本脚本直接提供。

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标:"; Flags: unchecked

[Files]
; onedir 的全部运行文件。安装后无需每次启动临时解压。
Source: "..\dist\SuperMaxSerialTool\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\USER_GUIDE.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\USER GUIDE"; Filename: "{app}\USER_GUIDE.md"
Name: "{group}\README 说明"; Filename: "{app}\README.md"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "现在启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

; 用户配置、产品 JSON、日志和原始数据位于 %LOCALAPPDATA%\SuperMaxSerialTool，卸载时保留。
