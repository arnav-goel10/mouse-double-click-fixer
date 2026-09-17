#ifndef AppVersion
#define AppVersion "0.2.0"
#endif

[Setup]
AppName=DoubleClick Fixer
AppVersion={#AppVersion}
DefaultDirName={autopf}\DoubleClick Fixer
DefaultGroupName=DoubleClick Fixer
OutputBaseFilename=DoubleClickFixer-Setup
ArchitecturesInstallIn64BitMode=x64compatible
AppPublisher=DoubleClick Fixer
AppSupportURL=https://github.com/arnav-goel10/doubleclick-fixer
UninstallDisplayIcon={app}\DoubleClickFixer.exe
VersionInfoVersion={#AppVersion}
WizardStyle=modern
PrivilegesRequired=lowest
; Ask the running copy to close, so the file is never locked during an update.
CloseApplications=yes

[Files]
Source: "..\dist\DoubleClickFixer.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\DoubleClick Fixer"; Filename: "{app}\DoubleClickFixer.exe"
Name: "{userstartup}\DoubleClick Fixer"; Filename: "{app}\DoubleClickFixer.exe"; Parameters: "--minimized"; Tasks: startup

[Tasks]
Name: "startup"; Description: "Start DoubleClick Fixer when I sign in"; Flags: unchecked

[Run]
Filename: "{app}\DoubleClickFixer.exe"; Description: "Open DoubleClick Fixer"; Flags: nowait postinstall skipifsilent

[Registry]
; The app writes this itself when "start at login" is ticked; clear it on uninstall.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "DoubleClickFixer"; Flags: dontcreatekey uninsdeletevalue

[UninstallDelete]
Type: filesandordirs; Name: "{userappdata}\DoubleClickFixer"
Type: files; Name: "{%USERPROFILE}\.doubleclick-fixer.json"
