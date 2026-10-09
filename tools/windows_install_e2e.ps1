# End-to-end check of the Windows install, quit and update paths, on a real
# Windows machine (CI). It installs, launches and replaces the app for real, so
# never run it on a machine whose copy of Mouse Double-Click Fixer you care about.
#
#   tools\windows_install_e2e.ps1 -OldSetup old\0.2.6\DoubleClickFixer-Setup.exe,old\0.5.3\DoubleClickFixer-Setup.exe -NewSetup installer\Output\DoubleClickFixer-Setup.exe
#
# Each old installer is installed in turn, left running and upgraded from
# (the app is uninstalled between them). Run it from the folder the build was
# made in: it compares the installed files with dist\onedir and build\notices.
#
# The app installs only signed updates, so the update leg needs a build that
# trusts a key this run made: CI builds with DCF_CI_UPDATE_KEY and passes the
# secret half as -SigningKey (see tools/ci_update_key.py). Without one, only
# the leg that checks an unsigned update is refused runs.
param(
    [Parameter(Mandatory)] [string[]] $OldSetup,
    [Parameter(Mandatory)] [string] $NewSetup,
    [string] $SigningKey = ""
)
$ErrorActionPreference = "Stop"
$OldSetup = @($OldSetup | ForEach-Object { (Resolve-Path $_).Path })
$NewSetup = (Resolve-Path $NewSetup).Path
if ($SigningKey) { $SigningKey = (Resolve-Path $SigningKey).Path }
$UninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{6B0E2F4C-3D7A-4E51-9A0B-DC1F1C5E7A21}_is1"

function Step($text) { Write-Host "`n=== $text" }
function Fail($text) { Write-Host "FAIL: $text"; Get-Running | Format-Table ProcessId, CommandLine -AutoSize | Out-String | Write-Host; exit 1 }
function Get-Running { @(Get-CimInstance Win32_Process -Filter "Name='DoubleClickFixer.exe'") }
function Wait-For([scriptblock] $Condition, [int] $Seconds, [string] $What) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (& $Condition) { return }
        Start-Sleep -Milliseconds 500
    }
    Fail "timed out after $Seconds s waiting for: $What"
}
# Runs an installer silently. Bounded: a setup that waits on something for
# ever fails the run instead of hanging it.
function Install($Setup, $Log, [int] $Seconds = 300, [string[]] $Extra = @()) {
    $process = Start-Process $Setup -ArgumentList (@("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG=`"$Log`"") + $Extra) -PassThru
    $null = $process.Handle  # keeps the exit code readable after WaitForExit
    if (-not $process.WaitForExit($Seconds * 1000)) {
        Get-CimInstance Win32_Process | Format-Table ProcessId, ParentProcessId, Name, CommandLine -AutoSize | Out-String -Width 300 | Write-Host
        if (Test-Path $Log) { Get-Content $Log -Tail 40 | Write-Host }
        Fail "$Setup was still running after $Seconds s"
    }
    if ($process.ExitCode -ne 0) { Get-Content $Log -Tail 40 | Write-Host; Fail "$Setup exited with $($process.ExitCode)" }
}
function App-Path { (Get-ItemProperty $UninstallKey).InstallLocation.TrimEnd('\') + "\DoubleClickFixer.exe" }
# What the installer logged about closing the running copy (see
# AskRunningCopyToQuit in installer\windows.iss).
function Quit-Lines($Log) { @(Select-String -Path $Log -Pattern "Quit: " | ForEach-Object { $_.Line }) }
function Expect-Quit($Lines, [string[]] $Wanted, [string[]] $Unwanted = @()) {
    foreach ($pattern in $Wanted) {
        if (-not ($Lines -match $pattern)) { Fail "the setup log has no '$pattern' in:`n$($Lines -join "`n")" }
    }
    foreach ($pattern in $Unwanted) {
        if ($Lines -match $pattern) { Fail "the setup log has '$pattern' in:`n$($Lines -join "`n")" }
    }
}
$version = (python -c "import app; print(app.__version__)").Trim()
# Every old installer here is from before 1.0, when the app was DoubleClick
# Fixer: the Start menu folder its shortcut went in by default, in the user's
# Programs folder. The first leg is installed into a folder of the user's
# choosing instead, which the update keeps.
$Programs = [Environment]::GetFolderPath("Programs")
$OldGroup = "DoubleClick Fixer"
$NewGroup = "Mouse Double-Click Fixer"
$ChosenGroup = "Mouse Tools"
# The new build is installed whole: the folder build (not the portable
# one-file exe), its runtime, and the notices beside it. Nothing it replaced
# was in use.
function Assert-NewBuild($Log) {
    if (Select-String -Path $Log -Pattern "in use|DeleteFile failed|Retrying" -Quiet) {
        Get-Content $Log | Select-String "in use|failed|Retry" | Write-Host
        Fail "the installer met a file in use"
    }
    $app = App-Path
    $folder = Split-Path $app
    $expected = (Get-FileHash "dist\onedir\DoubleClickFixer\DoubleClickFixer.exe").Hash
    if ((Get-FileHash $app).Hash -ne $expected) { Fail "the installed exe is not the new build" }
    if (-not (Test-Path (Join-Path $folder "_internal"))) { Fail "the installed app has no _internal folder" }
    $notices = Join-Path $folder "THIRD_PARTY_NOTICES.md"
    if (-not (Test-Path $notices)) { Fail "THIRD_PARTY_NOTICES.md is not in the install folder" }
    if ((Get-FileHash $notices).Hash -ne (Get-FileHash "build\notices\THIRD_PARTY_NOTICES.md").Hash) {
        Fail "the installed THIRD_PARTY_NOTICES.md is not the one this build wrote"
    }
    $fileVersion = (Get-Item $app).VersionInfo.ProductVersion
    $described = (Get-Item $app).VersionInfo.FileDescription
    if ($described -ne "Mouse Double-Click Fixer") { Fail "the exe describes itself as '$described'" }
    $listed = (Get-ItemProperty $UninstallKey).DisplayVersion
    if ($fileVersion -ne $version) { Fail "the exe says version '$fileVersion', expected $version" }
    if ($listed -ne $version) { Fail "Installed apps lists version '$listed', expected $version" }
}
# Runs the uninstaller silently. Bounded like Install: it waits for the
# uninstaller and for the copy of itself the uninstaller runs from the temp
# folder (its child, which outlives it), and fails the run if either is
# still going after $Seconds.
function Uninstall-App([int] $Seconds = 120) {
    $app = App-Path
    $uninstaller = (Get-ItemProperty $UninstallKey).UninstallString.Trim('"')
    $process = Start-Process $uninstaller -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -PassThru
    $null = $process.Handle  # no other process can take its ID while this holds it
    $deadline = (Get-Date).AddSeconds($Seconds)
    while (-not $process.HasExited -or @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($process.Id)").Count) {
        if ((Get-Date) -gt $deadline) {
            Get-CimInstance Win32_Process | Format-Table ProcessId, ParentProcessId, Name, CommandLine -AutoSize | Out-String -Width 300 | Write-Host
            Fail "the uninstaller was still running after $Seconds s"
        }
        Start-Sleep -Milliseconds 500
    }
    Wait-For { (Get-Running).Count -eq 0 } 30 "the app to be closed by the uninstaller"
    Wait-For { -not (Test-Path $app) } 30 "the exe to be removed"
    Wait-For { -not (Test-Path $UninstallKey) } 30 "the app to leave Installed apps"
}

$leg = 0
foreach ($old in $OldSetup) {
    $leg += 1
    if (Test-Path $UninstallKey) { Uninstall-App }

    Step "Install an old version and leave it running in the notification area ($old)"
    if ($leg -eq 1) {
        $group = $ChosenGroup
        Install $old "$env:TEMP\dcf-old-$leg.log" -Extra @("/GROUP=`"$group`"")
    } else {
        $group = $OldGroup
        Install $old "$env:TEMP\dcf-old-$leg.log"
    }
    $oldVersion = (Get-ItemProperty $UninstallKey).DisplayVersion
    $app = App-Path
    $OldShortcut = Join-Path $Programs "$group\DoubleClick Fixer.lnk"
    if (-not (Test-Path $OldShortcut)) { Fail "$oldVersion made no $OldShortcut" }
    Start-Process $app -ArgumentList "--minimized"
    Wait-For { (Get-Running).Count -gt 0 } 30 "the old copy to start"
    Start-Sleep -Seconds 5
    $oldIds = (Get-Running).ProcessId
    Write-Host "$oldVersion running: $oldIds"

    Step "Install the new version over the running $oldVersion"
    $log = "$env:TEMP\dcf-new-$leg.log"
    Install $NewSetup $log
    $still = Get-Running | Where-Object { $oldIds -contains $_.ProcessId }
    if ($still) { Fail "the old copy is still running after the upgrade" }
    Assert-NewBuild $log
    # The new name: the update stays in the folder the old copy was in, and
    # its Start menu entry and Installed apps name are the new ones. The
    # entry moves out of the old default folder, which goes, but stays in a
    # folder the user chose.
    if ((App-Path) -ne $app) { Fail "the upgrade moved the app from $app to $(App-Path)" }
    if (Test-Path $OldShortcut) { Fail "the Start menu still has $OldShortcut" }
    if ($group -eq $OldGroup) {
        if (Test-Path (Join-Path $Programs $OldGroup)) { Fail "the old Start menu folder is still there" }
        $NewShortcut = Join-Path $Programs "$NewGroup\Mouse Double-Click Fixer.lnk"
    } else {
        if (Test-Path (Join-Path $Programs $NewGroup)) { Fail "the update left the Start menu folder '$group' for '$NewGroup'" }
        $NewShortcut = Join-Path $Programs "$group\Mouse Double-Click Fixer.lnk"
    }
    if (-not (Test-Path $NewShortcut)) { Fail "the Start menu has no $NewShortcut" }
    $listed = (Get-ItemProperty $UninstallKey).DisplayName
    if ($listed -notlike "Mouse Double-Click Fixer*") { Fail "Installed apps lists the app as '$listed'" }
    $quit = Quit-Lines $log
    $quit | Write-Host
    if ($oldVersion -and [version] $oldVersion -ge [version] "0.2.7") {
        # The old copy's own --quit closed it: nothing was left for taskkill.
        Expect-Quit $quit @("asking the installed copy", "--quit finished", "taskkill exit code 128 ") @("taskkill only")
    } else {
        # Too old to know --quit: taskkill closed it.
        Expect-Quit $quit @("taskkill only", "taskkill exit code 0 ") @("asking the installed copy")
    }
    Write-Host "the upgrade from a running $oldVersion replaced it cleanly; exe and Installed apps both say $version"
}

$app = App-Path
$folder = Split-Path $app

Step "The installed app's self-test passes, its TLS going through Schannel"
# It loads Qt and every module of the app and starts the TLS backend, without
# touching the network or any running copy (app/selftest.py).
$selfTest = Join-Path $env:TEMP "dcf-self-test.txt"
$process = Start-Process $app -ArgumentList "--self-test" -Wait -PassThru -NoNewWindow `
    -RedirectStandardOutput $selfTest -RedirectStandardError "$selfTest.err"
Get-Content $selfTest, "$selfTest.err" | Write-Host
if ($process.ExitCode -ne 0) { Fail "the installed app's self-test failed (exit $($process.ExitCode))" }
if (-not (Select-String -Path $selfTest -Pattern "^tls: ok \(schannel, " -Quiet)) { Fail "the self-test reported no TLS through Schannel" }
# Left out of every Windows build (tools/make_notices.py, unused_qt_file).
$leftOut = '^(qopensslbackend|lib(crypto|ssl)-\d+-(x64|arm64|arm)|ucrtbase|api-ms-win-.+)\.dll$'
$shipped = @(Get-ChildItem $folder -Recurse -File | Where-Object Name -match $leftOut)
if ($shipped) { Fail "the installed app ships what the build leaves out: $(($shipped | ForEach-Object Name) -join ', ')" }
Write-Host "self-test passed; no OpenSSL for Qt and no Universal C Runtime copy in the install folder"

Step "A folder install that lost its runtime is closed with taskkill, not run"
# A failed update can leave the exe without _internal: [InstallDelete] runs
# first, and rollback doesn't put it back. Such an exe can't start; run with
# --quit, it would show "Failed to load Python DLL" and wait for a click. The
# installer runs the installed copy only when it is a one-file build (0.5.3
# and earlier) or still has _internal. Listed as 0.5.4, the first folder
# release, it is a folder build without its runtime.
Remove-Item -Recurse -Force (Join-Path $folder "_internal")
Set-ItemProperty $UninstallKey -Name DisplayVersion -Value "0.5.4"
$log = "$env:TEMP\dcf-no-runtime.log"
$started = Get-Date
Install $NewSetup $log 120
$seconds = ((Get-Date) - $started).TotalSeconds
$quit = Quit-Lines $log
$quit | Write-Host
Expect-Quit $quit @("has no _internal folder") @("asking the installed copy")
if ((Get-Running).Count) { Fail "a copy was left running" }
Assert-NewBuild $log
Write-Host ("setup took {0:N1} s and put the runtime back" -f $seconds)

Step "An installed copy that hangs on --quit holds the installer up for 20 s at most"
# Listed as 0.5.3, the last one-file release, the installed copy is run to ask
# for a quit whatever its folder holds: a one-file build has no _internal.
# This one is a folder build without it, so it shows "Failed to load Python
# DLL" and waits for a click that never comes (its window is hidden, too).
Remove-Item -Recurse -Force (Join-Path $folder "_internal")
Set-ItemProperty $UninstallKey -Name DisplayVersion -Value "0.5.3"
$log = "$env:TEMP\dcf-hangs.log"
$started = Get-Date
Install $NewSetup $log 180
$seconds = ((Get-Date) - $started).TotalSeconds
$quit = Quit-Lines $log
$quit | Write-Host
Expect-Quit $quit @("asking the installed copy", "did not finish within 20 s; stopped it")
if ($seconds -lt 20) { Fail ("setup took {0:N1} s: the hung copy wasn't what held it" -f $seconds) }
if ((Get-Running).Count) { Fail "the hung copy was left running" }
Assert-NewBuild $log
Write-Host ("setup stopped the hung copy and finished in {0:N1} s" -f $seconds)

Step "Opening the app twice in a row leaves one copy"
Start-Process $app -ArgumentList "--minimized"
Start-Process $app -ArgumentList "--minimized"
Start-Sleep -Seconds 15
# Count copies, not processes: a one-file exe would be a launcher plus the app.
$copies = @(Get-Running | Where-Object { $_.ParentProcessId -notin (Get-Running).ProcessId }).Count
if ($copies -ne 1) { Fail "expected one copy running, found $copies" }
Start-Process $app -ArgumentList "--quit" -Wait
Wait-For { (Get-Running).Count -eq 0 } 20 "the copy to exit"
Write-Host "a second launch hands over to the first"

Step "--quit ends a copy that is still starting, and starts nothing when none runs"
Start-Process $app -ArgumentList "--minimized"
Wait-For { (Get-Running).Count -gt 0 } 30 "the new copy to start"
Start-Sleep -Seconds 1  # still starting: perhaps not listening yet
Start-Process $app -ArgumentList "--quit" -Wait
Wait-For { (Get-Running).Count -eq 0 } 20 "every DoubleClickFixer.exe to exit after --quit"
Start-Process $app -ArgumentList "--quit" -Wait
Start-Sleep -Seconds 3
if ((Get-Running).Count -ne 0) { Fail "--quit with nothing running started a copy" }
Write-Host "--quit works"

# A stand-in release server: GitHub's API answer, the installer, its checksums
# and (signed leg) their signature, as http.server serves a folder. Its log
# shows what the app asked for.
$serve = Join-Path $env:TEMP "dcf-release"
$base = "http://127.0.0.1:8765"
function Serve-Release([bool] $Signed) {
    Remove-Item -Recurse -Force $serve -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force $serve | Out-Null
    Copy-Item $NewSetup "$serve\DoubleClickFixer-Setup.exe"
    $hash = (Get-FileHash "$serve\DoubleClickFixer-Setup.exe" -Algorithm SHA256).Hash.ToLower()
    "$hash  DoubleClickFixer-Setup.exe" | Set-Content "$serve\SHA256SUMS.txt" -Encoding ascii
    $assets = @(
        @{ name = "DoubleClickFixer-Setup.exe"; browser_download_url = "$base/DoubleClickFixer-Setup.exe"; state = "uploaded" },
        @{ name = "SHA256SUMS.txt"; browser_download_url = "$base/SHA256SUMS.txt"; state = "uploaded" }
    )
    if ($Signed) {
        python tools\ci_update_key.py sign 9.9.9 "$serve\SHA256SUMS.txt" $SigningKey | Out-Null
        if ($LASTEXITCODE -ne 0) { Fail "couldn't sign the stand-in release" }
        $assets += @{ name = "SHA256SUMS.txt.minisig"; browser_download_url = "$base/SHA256SUMS.txt.minisig"; state = "uploaded" }
    }
    @{ tag_name = "v9.9.9"; html_url = "$base/release"; draft = $false; prerelease = $false; assets = $assets } |
        ConvertTo-Json -Depth 4 | Set-Content "$serve\latest" -Encoding utf8
    $log = Join-Path $env:TEMP "dcf-release-server.log"
    Remove-Item $log -ErrorAction SilentlyContinue
    $process = Start-Process python -ArgumentList "-m", "http.server", "8765", "--bind", "127.0.0.1", "--directory", $serve `
        -PassThru -WindowStyle Hidden -RedirectStandardError $log
    return @{ Process = $process; Log = $log }
}
function Requests($server) {
    # The server still has its log open; read it without getting in its way.
    try {
        $stream = [System.IO.File]::Open($server.Log, "Open", "Read", "ReadWrite")
        try { (New-Object System.IO.StreamReader($stream)).ReadToEnd() } finally { $stream.Dispose() }
    } catch { "" }
}

Step "The in-app updater refuses an unsigned update"
$server = Serve-Release $false
try {
    $env:DCF_UPDATE_URL = "$base/latest"
    Start-Process $app -ArgumentList "--minimized"
    Wait-For { (Get-Running).Count -gt 0 } 30 "the copy that will check for updates to start"
    $before = (Get-Running).ProcessId
    # The first check runs 20 s after launch. A release without a signature
    # stops there: nothing more is fetched, nothing installed.
    Wait-For { (Requests $server) -match "GET /latest" } 90 "the app to check for the unsigned release"
    Start-Sleep -Seconds 15
    $requests = Requests $server
    if ($requests -match "GET /DoubleClickFixer-Setup.exe") { Fail "the app downloaded an unsigned update:`n$requests" }
    if (@(Get-Running | Where-Object { $_.CommandLine -like "*--updated*" }).Count) { Fail "an unsigned update was installed" }
    if (@(Get-Running | Where-Object { $before -contains $_.ProcessId }).Count -ne @($before).Count) {
        Fail "the copy that was offered an unsigned update stopped running"
    }
    Write-Host "unsigned update refused; the app kept running"
    Start-Process $app -ArgumentList "--quit" -Wait
    Wait-For { (Get-Running).Count -eq 0 } 20 "the copy to exit"
} finally {
    Remove-Item Env:\DCF_UPDATE_URL -ErrorAction SilentlyContinue
    Stop-Process -Id $server.Process.Id -Force -ErrorAction SilentlyContinue
}

if ($SigningKey) {
    Step "The in-app updater downloads, installs and relaunches a signed update"
    $server = Serve-Release $true
    try {
        $env:DCF_UPDATE_URL = "$base/latest"
        Start-Process $app -ArgumentList "--minimized"
        Wait-For { (Get-Running).Count -gt 0 } 30 "the copy that will update to start"
        $before = (Get-Running).ProcessId
        # The first check runs 20 s after launch; then download, install, relaunch.
        Wait-For { @(Get-Running | Where-Object { $_.CommandLine -like "*--updated*" -and $before -notcontains $_.ProcessId }).Count -gt 0 } 180 "the updated copy to relaunch"
        $leftover = Get-Running | Where-Object { $before -contains $_.ProcessId }
        if ($leftover) { Fail "the copy that started the update never exited" }
        Write-Host "update installed and relaunched: $((Get-Running).CommandLine)"
    } finally {
        Remove-Item Env:\DCF_UPDATE_URL -ErrorAction SilentlyContinue
        Stop-Process -Id $server.Process.Id -Force -ErrorAction SilentlyContinue
    }
} else {
    Write-Host "`nNo -SigningKey: the signed update leg is skipped"
}

Step "Uninstall closes the running copy and removes the app"
Uninstall-App
if (Test-Path (Join-Path $folder "THIRD_PARTY_NOTICES.md")) { Fail "the uninstaller left THIRD_PARTY_NOTICES.md behind" }
Write-Host "`nALL WINDOWS INSTALL CHECKS PASSED"
