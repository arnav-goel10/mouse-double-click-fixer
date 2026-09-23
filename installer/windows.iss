#ifndef AppVersion
#define AppVersion "0.2.0"
#endif

[Setup]
; Fixed forever: this is how an update finds and replaces the installed copy.
AppId={{6B0E2F4C-3D7A-4E51-9A0B-DC1F1C5E7A21}
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
SetupIconFile=assets\icon.ico
PrivilegesRequired=lowest
; Ask the running copy to close, so the file is never locked during an update.
CloseApplications=yes

[Files]
Source: "..\dist\DoubleClickFixer.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\DoubleClick Fixer"; Filename: "{app}\DoubleClickFixer.exe"
Name: "{autodesktop}\DoubleClick Fixer"; Filename: "{app}\DoubleClickFixer.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
Name: "startup"; Description: "Start DoubleClick Fixer when I sign in"; Flags: unchecked

[Run]
Filename: "{app}\DoubleClickFixer.exe"; Description: "Open DoubleClick Fixer"; Flags: nowait postinstall skipifsilent
; The in-app updater installs silently and asks for the app to come back.
Filename: "{app}\DoubleClickFixer.exe"; Parameters: "--updated {code:RelaunchArguments}"; Flags: nowait runasoriginaluser; Check: RelaunchRequested

[Code]
// /RELAUNCH=1 reopens the window; /RELAUNCH=2 comes back in the
// notification area only, as the app was before the update.
function RelaunchRequested: Boolean;
begin
  Result := ExpandConstant('{param:RELAUNCH|0}') <> '0';
end;

function RelaunchArguments(Param: String): String;
begin
  if ExpandConstant('{param:RELAUNCH|0}') = '2' then
    Result := '--minimized'
  else
    Result := '';
end;

[Registry]
; The same value the app's "Open at login" switch manages, so the two agree.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "DoubleClickFixer"; ValueData: """{app}\DoubleClickFixer.exe"" ""--minimized"""; Tasks: startup
; The app writes this itself when "start at login" is ticked; clear it on uninstall.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "DoubleClickFixer"; Flags: dontcreatekey uninsdeletevalue

[UninstallDelete]
Type: filesandordirs; Name: "{userappdata}\DoubleClickFixer"
Type: files; Name: "{%USERPROFILE}\.doubleclick-fixer.json"
