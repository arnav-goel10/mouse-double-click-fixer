#ifndef AppVersion
#define AppVersion "0.0.0"
#endif

[Setup]
; Fixed forever: this is how an update finds and replaces the installed copy.
AppId={{6B0E2F4C-3D7A-4E51-9A0B-DC1F1C5E7A21}
AppName=DoubleClick Fixer
AppVersion={#AppVersion}
DefaultDirName={autopf}\DoubleClick Fixer
DefaultGroupName=DoubleClick Fixer
OutputBaseFilename=DoubleClickFixer-Setup
; The app is a 64-bit build, and Qt 6 needs Windows 10 1809 or later: say so
; up front rather than install something that can't start.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
AppPublisher=DoubleClick Fixer
AppSupportURL=https://github.com/arnav-goel10/doubleclick-fixer
UninstallDisplayIcon={app}\DoubleClickFixer.exe
VersionInfoVersion={#AppVersion}
AppVerName=DoubleClick Fixer {#AppVersion}
AppPublisherURL=https://github.com/arnav-goel10/doubleclick-fixer
AppUpdatesURL=https://github.com/arnav-goel10/doubleclick-fixer/releases/latest
WizardStyle=modern
SetupIconFile=assets\icon.ico
PrivilegesRequired=lowest
; Ask the running copy to close, so the file is never locked during an update.
CloseApplications=yes

[InstallDelete]
; The runtime beside the exe is replaced whole, so an update that moves to a
; newer Qt or Python leaves none of the old one behind.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
; A folder, not the portable one-file exe: launching it (and every sign-in)
; unpacks nothing.
Source: "..\dist\onedir\DoubleClickFixer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\DoubleClick Fixer"; Filename: "{app}\DoubleClickFixer.exe"
Name: "{autodesktop}\DoubleClick Fixer"; Filename: "{app}\DoubleClickFixer.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
; Offered on the first install only: afterwards "Open at login" in the app
; owns the setting, and an update must not switch it back on.
Name: "startup"; Description: "Start DoubleClick Fixer when I sign in"; Flags: unchecked; Check: not IsUpgrade

[Run]
Filename: "{app}\DoubleClickFixer.exe"; Description: "Open DoubleClick Fixer"; Flags: nowait postinstall skipifsilent
; The in-app updater installs silently and asks for the app to come back.
Filename: "{app}\DoubleClickFixer.exe"; Parameters: "--updated {code:RelaunchArguments}"; Flags: nowait runasoriginaluser; Check: RelaunchRequested

[Code]
// Inno Setup's own uninstall entry for this AppId.
function UninstallKey: String;
begin
  Result := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{6B0E2F4C-3D7A-4E51-9A0B-DC1F1C5E7A21}_is1';
end;

function IsUpgrade: Boolean;
begin
  Result := RegKeyExists(HKCU, UninstallKey) or RegKeyExists(HKLM, UninstallKey);
end;

// The version installed now, or '' when there is none.
function InstalledVersion: String;
var
  Version: String;
begin
  Result := '';
  if RegQueryStringValue(HKCU, UninstallKey, 'DisplayVersion', Version) then
    Result := Version
  else if RegQueryStringValue(HKLM, UninstallKey, 'DisplayVersion', Version) then
    Result := Version;
end;

// Whether a version like "0.2.11" is Major.Minor.Patch or later.
function VersionAtLeast(const Version: String; Major, Minor, Patch: Integer): Boolean;
var
  Rest: String;
  Parts: array[0..2] of Integer;
  I, Dot: Integer;
begin
  Rest := Version;
  for I := 0 to 2 do
  begin
    Dot := Pos('.', Rest);
    if Dot = 0 then
    begin
      Parts[I] := StrToIntDef(Rest, 0);
      Rest := '';
    end
    else
    begin
      Parts[I] := StrToIntDef(Copy(Rest, 1, Dot - 1), 0);
      Rest := Copy(Rest, Dot + 1, Length(Rest));
    end;
  end;
  if Parts[0] <> Major then
    Result := Parts[0] > Major
  else if Parts[1] <> Minor then
    Result := Parts[1] > Minor
  else
    Result := Parts[2] >= Patch;
end;

// A running copy lives in the notification area and ignores window-close
// requests (closing only hides it), so ask it to quit through its own
// single-instance channel before files are replaced or removed. "--quit"
// never starts a copy of its own.
procedure AskRunningCopyToQuit(const Exe: String);
var
  ResultCode: Integer;
begin
  if FileExists(Exe) then
  begin
    Exec(Exe, '--quit', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Sleep(800);
  end;
  // A copy older than 0.2.7 doesn't know "--quit" (it shows its window
  // instead), and one that is hung can't answer: end it, so no file stays
  // in use. Its mouse hook goes with it.
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM DoubleClickFixer.exe /F', '', SW_HIDE,
    ewWaitUntilTerminated, ResultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  // The installed copy knows how to reach a running one of its own version.
  // (The new exe can't run on its own from {tmp}: it needs its folder.) A
  // copy before 0.2.7 doesn't know "--quit": run with it, it may start a
  // second copy that never exits, and this installer would wait on it for
  // ever. So that one, like a copy installed elsewhere, is left to taskkill.
  if VersionAtLeast(InstalledVersion, 0, 2, 7) then
    AskRunningCopyToQuit(ExpandConstant('{app}\DoubleClickFixer.exe'))
  else
    AskRunningCopyToQuit('');
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  AskRunningCopyToQuit(ExpandConstant('{app}\DoubleClickFixer.exe'));
  Result := True;
end;

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
; Turning the app off in Task Manager's Startup apps is kept here, and outlives
; the Run value. Ticking "Start when I sign in" clears an old "off" (updates
; leave the user's choice alone), and uninstalling leaves nothing behind.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"; ValueType: none; ValueName: "DoubleClickFixer"; Flags: deletevalue dontcreatekey; Tasks: startup
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"; ValueType: none; ValueName: "DoubleClickFixer"; Flags: dontcreatekey uninsdeletevalue

[UninstallDelete]
Type: filesandordirs; Name: "{userappdata}\DoubleClickFixer"
Type: files; Name: "{%USERPROFILE}\.doubleclick-fixer.json"
