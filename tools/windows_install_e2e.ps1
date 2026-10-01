# End-to-end check of the Windows install, quit and update paths, on a real
# Windows machine (CI). It installs, launches and replaces the app for real, so
# never run it on a machine whose copy of DoubleClick Fixer you care about.
#
#   tools\windows_install_e2e.ps1 -OldSetup old\DoubleClickFixer-Setup.exe -NewSetup installer\Output\DoubleClickFixer-Setup.exe
param(
    [Parameter(Mandatory)] [string] $OldSetup,
    [Parameter(Mandatory)] [string] $NewSetup
)
$ErrorActionPreference = "Stop"
$OldSetup = (Resolve-Path $OldSetup).Path
$NewSetup = (Resolve-Path $NewSetup).Path
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
function Install($Setup, $Log) {
    $process = Start-Process $Setup -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG=`"$Log`"" -Wait -PassThru
    if ($process.ExitCode -ne 0) { Get-Content $Log -Tail 40 | Write-Host; Fail "$Setup exited with $($process.ExitCode)" }
}
function App-Path { (Get-ItemProperty $UninstallKey).InstallLocation.TrimEnd('\') + "\DoubleClickFixer.exe" }

Step "Install the old version and leave it running in the notification area"
Install $OldSetup "$env:TEMP\dcf-old.log"
$app = App-Path
Start-Process $app -ArgumentList "--minimized"
Wait-For { (Get-Running).Count -gt 0 } 30 "the old copy to start"
Start-Sleep -Seconds 5
$oldIds = (Get-Running).ProcessId
Write-Host "old copy running: $oldIds"

Step "Install the new version over the running copy"
Install $NewSetup "$env:TEMP\dcf-new.log"
$still = Get-Running | Where-Object { $oldIds -contains $_.ProcessId }
if ($still) { Fail "the old copy is still running after the upgrade" }
if (Select-String -Path "$env:TEMP\dcf-new.log" -Pattern "in use|DeleteFile failed|Retrying" -Quiet) {
    Get-Content "$env:TEMP\dcf-new.log" | Select-String "in use|failed|Retry" | Write-Host
    Fail "the installer met a file in use"
}
$expected = (Get-FileHash "dist\DoubleClickFixer.exe").Hash
if ((Get-FileHash $app).Hash -ne $expected) { Fail "the installed exe is not the new build" }
Write-Host "upgrade replaced the running copy cleanly"

Step "Opening the app twice in a row leaves one copy"
Start-Process $app -ArgumentList "--minimized"
Start-Process $app -ArgumentList "--minimized"
Start-Sleep -Seconds 15
# A one-file exe is two processes: the launcher and the app it unpacks.
$copies = @(Get-Running | Where-Object { $_.ParentProcessId -notin (Get-Running).ProcessId }).Count
if ($copies -ne 1) { Fail "expected one copy running, found $copies" }
Start-Process $app -ArgumentList "--quit" -Wait
Wait-For { (Get-Running).Count -eq 0 } 20 "the copy to exit"
Write-Host "a second launch hands over to the first"

Step "--quit ends a copy that is still starting, and starts nothing when none runs"
Start-Process $app -ArgumentList "--minimized"
Wait-For { (Get-Running).Count -gt 0 } 30 "the new copy to start"
Start-Sleep -Seconds 1  # still unpacking: not listening yet
Start-Process $app -ArgumentList "--quit" -Wait
Wait-For { (Get-Running).Count -eq 0 } 20 "every DoubleClickFixer.exe to exit after --quit"
Start-Process $app -ArgumentList "--quit" -Wait
Start-Sleep -Seconds 3
if ((Get-Running).Count -ne 0) { Fail "--quit with nothing running started a copy" }
Write-Host "--quit works"

Step "The in-app updater downloads, installs and relaunches"
$serve = Join-Path $env:TEMP "dcf-release"
New-Item -ItemType Directory -Force $serve | Out-Null
Copy-Item $NewSetup "$serve\DoubleClickFixer-Setup.exe"
$hash = (Get-FileHash "$serve\DoubleClickFixer-Setup.exe" -Algorithm SHA256).Hash.ToLower()
"$hash  DoubleClickFixer-Setup.exe" | Set-Content "$serve\SHA256SUMS.txt" -Encoding ascii
$base = "http://127.0.0.1:8765"
@{
    tag_name = "v9.9.9"; html_url = "$base/release"; draft = $false; prerelease = $false
    assets = @(
        @{ name = "DoubleClickFixer-Setup.exe"; browser_download_url = "$base/DoubleClickFixer-Setup.exe"; state = "uploaded" },
        @{ name = "SHA256SUMS.txt"; browser_download_url = "$base/SHA256SUMS.txt"; state = "uploaded" }
    )
} | ConvertTo-Json -Depth 4 | Set-Content "$serve\latest" -Encoding utf8
$server = Start-Process python -ArgumentList "-m", "http.server", "8765", "--bind", "127.0.0.1", "--directory", $serve -PassThru -WindowStyle Hidden
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
    Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
}

Step "Uninstall closes the running copy and removes the app"
$uninstaller = (Get-ItemProperty $UninstallKey).UninstallString.Trim('"')
Start-Process $uninstaller -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -Wait
Wait-For { (Get-Running).Count -eq 0 } 30 "the app to be closed by the uninstaller"
Wait-For { -not (Test-Path $app) } 30 "the exe to be removed"
Write-Host "`nALL WINDOWS INSTALL CHECKS PASSED"
