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
; The licences of what the app bundles (Qt's LGPL among them), where anyone
; looking in the install folder finds them. The PyInstaller spec writes the
; file for the exact versions it bundled, and the app has its own copy.
Source: "..\build\notices\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

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

const
  // How long the installed copy gets to ask a running one to quit.
  QuitWaitSeconds = 20;

// The installed exe, when it can be run to ask a running copy to quit, or ''
// when it can't (the log says why). It knows how to reach a running copy of
// its own version; the new exe can't run on its own from {tmp}, as it needs
// its folder.
function InstalledQuitter: String;
var
  Version: String;
begin
  Result := '';
  Version := InstalledVersion;
  if not VersionAtLeast(Version, 0, 2, 7) then
    // Before 0.2.7 "--quit" is unknown: run with it, such a copy may start a
    // second one that never exits.
    Log('Quit: taskkill only: no installed copy, or one before 0.2.7 (' + Version + ')')
  else if VersionAtLeast(Version, 1, 0, 0) and not DirExists(ExpandConstant('{app}\_internal')) then
    // From 1.0 the installed app is a folder build, and its exe can't start
    // without _internal: a failed update may have removed it
    // ([InstallDelete] runs first, and rollback doesn't restore it). Run, it
    // would show "Failed to load Python DLL" and wait for a click. Before
    // 1.0 it was one self-contained file.
    Log('Quit: taskkill only: the installed ' + Version + ' has no _internal folder, so it can''t start')
  else
    Result := ExpandConstant('{app}\DoubleClickFixer.exe');
end;

// A running copy lives in the notification area and ignores window-close
// requests (closing only hides it), so ask it to quit through its own
// single-instance channel before files are replaced or removed. "--quit"
// never starts a copy of its own.
procedure AskRunningCopyToQuit(const Exe: String);
var
  ResultCode: Integer;
  Script: String;
begin
  if (Exe <> '') and FileExists(Exe) then
  begin
    // Through PowerShell, for a time limit: Exec waits for ever or not at
    // all, and a copy that can't start or hangs would hold this installer
    // (a silent update, with the app already closed) for ever. The exe is
    // found from the folder PowerShell starts in, so no path is ever
    // quoted into the command.
    Script := '$ErrorActionPreference = ''Stop''; ' +
      'try { $p = Start-Process -FilePath (Join-Path -Path (Get-Location).ProviderPath -ChildPath ''' +
      ExtractFileName(Exe) + ''') -ArgumentList ''--quit'' -WindowStyle Hidden -PassThru } catch { exit 4 }; ' +
      'if ($p.WaitForExit(' + IntToStr(QuitWaitSeconds * 1000) + ')) { exit 0 }; ' +
      'Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue; exit 3';
    Log('Quit: asking the installed copy: ' + Exe + ' --quit');
    if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
      '-NoProfile -NonInteractive -Command "' + Script + '"', ExtractFileDir(Exe), SW_HIDE,
      ewWaitUntilTerminated, ResultCode) then
      Log('Quit: couldn''t start PowerShell: ' + SysErrorMessage(ResultCode))
    else if ResultCode = 0 then
      Log('Quit: --quit finished')
    else if ResultCode = 3 then
      Log(Format('Quit: --quit did not finish within %d s; stopped it', [QuitWaitSeconds]))
    else
      Log(Format('Quit: --quit couldn''t be run (PowerShell exit code %d)', [ResultCode]));
    Sleep(800);
  end;
  // A copy older than 0.2.7 doesn't know "--quit" (it shows its window
  // instead), and one that is hung can't answer: end it, so no file stays
  // in use. Its mouse hook goes with it.
  if Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM DoubleClickFixer.exe /F', '', SW_HIDE,
    ewWaitUntilTerminated, ResultCode) then
    Log(Format('Quit: taskkill exit code %d (0 ended a copy, 128 found none running)', [ResultCode]));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  AskRunningCopyToQuit(InstalledQuitter);
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  AskRunningCopyToQuit(InstalledQuitter);
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
