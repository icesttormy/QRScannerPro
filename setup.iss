; ============================================================
; 截图识码工具 —— Inno Setup 安装包脚本
; ============================================================
; 使用方法:
;   1. 先运行 build.bat，生成 dist_candidate\截图识码工具.exe
;   2. 安装 Inno Setup: https://jrsoftware.org/isdl.php (免费)
;   3. 用 Inno Setup 打开这个 .iss 文件，点 Build -> Compile
;   4. 编译完成后，installer_candidate 文件夹里就是候选安装包
; ============================================================

#define MyAppName "截图识码工具"
#define MyAppVersion "1.0"
#define MyAppPublisher "个人开发"
#define MyAppExeName "截图识码工具.exe"

[Setup]
; 每个软件的这个 GUID 必须唯一，随便在网上搜"GUID生成器"生成一个替换即可，
; 换软件时要换新的，同一个软件升级版本时要保持不变
AppId={{A1B2C3D4-E5F6-4A5B-9C8D-1234567890AB}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL=https://github.com/icesttormy/QRScannerPro
AppSupportURL=https://github.com/icesttormy/QRScannerPro
LicenseFile=LICENSE
; 默认安装到 Program Files 下的同名文件夹
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
; 允许用户在安装向导里修改安装路径
DisableProgramGroupPage=yes
; 候选安装包单独输出，避免覆盖已经验证过的旧安装包
OutputDir=installer_candidate
OutputBaseFilename={#MyAppName}_Setup_v{#MyAppVersion}
; 用你已有的图标作为安装包本身的图标
SetupIconFile=icon.ico
Compression=lzma
SolidCompression=yes
WizardStyle=modern
; 写入 Program Files 需要管理员权限，这一行会让安装时自动弹 UAC 提权
PrivilegesRequired=admin

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
; 安装向导里给用户一个"创建桌面快捷方式"的勾选框，默认不勾
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加选项:"; Flags: unchecked

[Files]
; 使用 PyInstaller --onefile 生成的单文件 EXE
Source: "dist_candidate\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; 开始菜单快捷方式
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
; 桌面快捷方式(只有用户勾选了上面的 Task 才会创建)
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; 安装完成后勾选框："立即运行程序"
Filename: "{app}\{#MyAppExeName}"; Description: "立即运行 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 卸载时一并清理程序自己生成的配置和日志文件夹(%APPDATA%\ScreenQRScanner)
; 如果不想卸载时清掉用户配置(比如自定义快捷键)，可以删掉这一段
Type: filesandordirs; Name: "{userappdata}\ScreenQRScanner"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'ScreenQRScanner');
end;
