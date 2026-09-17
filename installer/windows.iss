#ifndef AppVersion
#define AppVersion "0.1.0"
#endif

[Setup]
AppName=DoubleClick Fixer
AppVersion={#AppVersion}
DefaultDirName={autopf}\DoubleClick Fixer
DefaultGroupName=DoubleClick Fixer
OutputBaseFilename=DoubleClickFixer-Setup
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "..\dist\DoubleClickFixer.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\DoubleClick Fixer"; Filename: "{app}\DoubleClickFixer.exe"
Name: "{userstartup}\DoubleClick Fixer"; Filename: "{app}\DoubleClickFixer.exe"; Parameters: "--minimized"; Tasks: startup

[Tasks]
Name: "startup"; Description: "Start DoubleClick Fixer when I sign in"; Flags: unchecked

[UninstallDelete]
Type: regvalue; Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; Name: "DoubleClickFixer"
Type: filesandordirs; Name: "{userappdata}\DoubleClickFixer"
Type: files; Name: "{%USERPROFILE}\.doubleclick-fixer.json"
