; ==============================================================================
; VoxPilot - Inno Setup Installer Script
; Packages the PyInstaller standalone one-folder distribution into VoxPilot-Setup.exe
; ==============================================================================

#define MyAppName "VoxPilot"
#define MyAppVersion "0.5.1"
#define MyAppPublisher "VoxPilot Team"
#define MyAppURL "https://github.com/VoxPilot"
#define MyAppExeName "VoxPilot.exe"
#define MyAppIcon "..\assets\icons\voxpilot.ico"
#define MyAppWizardSmallImage "..\assets\icons\voxpilot-icon-128x128.png"

[Setup]
; Unique stable application GUID for Inno Setup upgrade detection
AppId={{8F97B634-9A25-45C0-943C-65935E17F193}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}

; Default installation location: Per-user under Local AppData Programs
; Does not require administrative elevation (PrivilegesRequired=lowest)
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=Output
OutputBaseFilename=VoxPilot-Setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DisableProgramGroupPage=yes
UsedUserAreasWarning=no

; Pre-installation informational notice
InfoBeforeFile=INSTALL_NOTICE.txt

; Official VoxPilot Installer, Wizard, and Uninstaller Icons & File Properties
SetupIconFile={#MyAppIcon}
WizardSmallImageFile={#MyAppWizardSmallImage}
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
VersionInfoVersion=0.5.1.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} â€” Personal AI Desktop Control Agent Setup
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
VersionInfoCopyright=Copyright (c) 2026 {#MyAppPublisher}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Install the full PyInstaller standalone one-folder distribution
Source: "..\dist\VoxPilot\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Install official application icon into installation root for explicit shortcut & shell branding
Source: "{#MyAppIcon}"; DestDir: "{app}"; DestName: "voxpilot.ico"; Flags: ignoreversion

[Icons]
; Start Menu shortcuts (all branded with the official VoxPilot icon)
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; AppUserModelID: "VoxPilotTeam.VoxPilot.DesktopAgent.0.5.0"
Name: "{group}\Install Playwright Browser"; Filename: "{app}\install_browser.bat"; IconFilename: "{app}\voxpilot.ico"
Name: "{group}\VoxPilot Readme"; Filename: "{app}\README.txt"; IconFilename: "{app}\voxpilot.ico"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"; IconFilename: "{app}\voxpilot.ico"

; Optional Desktop shortcut
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; AppUserModelID: "VoxPilotTeam.VoxPilot.DesktopAgent.0.5.0"; Tasks: desktopicon

[Run]
; Option to launch VoxPilot immediately after setup completes
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[Code]
// Inform user during uninstallation that personal databases and logs remain preserved
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    MsgBox('VoxPilot program files have been successfully uninstalled.' + #13#10 + #13#10 +
           'Data Retention Notice:' + #13#10 +
           'Your task database, conversation history, and logs have been safely preserved in:' + #13#10 +
           ExpandConstant('{localappdata}\VoxPilot') + #13#10 + #13#10 +
           'If you reinstall VoxPilot in the future, your history will be retained automatically.' + #13#10 +
           'You can safely delete that folder manually if you do not plan to use VoxPilot again.',
           mbInformation, MB_OK);
  end;
end;


