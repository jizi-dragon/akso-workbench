; Akso Workbench 安装包脚本（Inno Setup 6）
; 由 tools/build.ps1 以 /DAppVersion=x.y.z 调用；版本号随构建演进

#define MyAppName "Akso Workbench"
#define MyAppExeName "AksoWorkbench.exe"

[Setup]
AppId={{7C1A2E9B-4F6D-4E2A-9B8C-AKSO0WORKB1}}
AppName={#MyAppName}
AppVersion={#AppVersion}
AppPublisher=jizi-dragon
DefaultDirName={autopf}\AksoWorkbench
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=AksoWorkbench-{#AppVersion}-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\AksoWorkbench\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
