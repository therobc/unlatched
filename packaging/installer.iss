; Inno Setup script for the Unlatched Windows installer.
;
; Produces a per-user Setup wizard: Welcome, License, install location,
; optional desktop icon, Ready to Install, Installing, Finish with an
; optional "launch now" checkbox, plus a standard uninstaller registered
; in Add/Remove Programs. No administrator rights are required because
; everything installs under the current user's LocalAppData.
;
; Expects packaging/build_release.py to have already assembled
; dist/portable/Unlatched/ (Unlatched.exe, engine/, LICENSE, README.md)
; before this script is compiled. Build with:
;
;     ISCC.exe packaging\installer.iss
;
; (packaging/build_release.py drives this for a full release build.)

#define MyAppName "Unlatched"
#define MyAppVersion "0.1.1"
#define MyAppPublisher "Unlatched contributors"
#define MyAppExeName "Unlatched.exe"
#define PortableDir SourcePath + "..\dist\portable\Unlatched"

[Setup]
AppId={{6F1E7C2A-6C0B-4B9E-9C7D-3B7B7C1F9A02}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#SourcePath}..\dist
OutputBaseFilename=Unlatched-Setup-{#MyAppVersion}
SetupIconFile={#SourcePath}unlatched.ico
UninstallDisplayIcon={app}\unlatched.ico
LicenseFile={#PortableDir}\LICENSE
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#PortableDir}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PortableDir}\engine\*"; DestDir: "{app}\engine"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#PortableDir}\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PortableDir}\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourcePath}unlatched.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\unlatched.ico"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"; IconFilename: "{app}\unlatched.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; IconFilename: "{app}\unlatched.ico"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
