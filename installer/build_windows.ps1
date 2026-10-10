# Build Mouse Double-Click Fixer for Windows: the portable
# dist\DoubleClickFixer.exe (one file) and dist\onedir\DoubleClickFixer\ (the
# folder the installer ships). Then compile installer\windows.iss with Inno
# Setup for the installer.
$ErrorActionPreference = "Stop"
# Build with exactly the pinned packages and build tools, each file checked
# against its hash (requirements-build.txt; see requirements-build.in).
python -m pip install --require-hashes --only-binary :all: -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw "pip could not install requirements-build.txt" }

# Separate work folders: both builds make an exe called DoubleClickFixer.
Remove-Item Env:\DCF_ONEDIR -ErrorAction SilentlyContinue
python -m PyInstaller --clean --noconfirm doubleclick-fixer.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed to build the portable exe" }
# Each build writes build\notices\THIRD_PARTY_NOTICES.md for what it bundles.
$notices = "build\notices\THIRD_PARTY_NOTICES.md"
$portableNotices = "build\notices\THIRD_PARTY_NOTICES-portable.md"
Copy-Item $notices $portableNotices -Force

$env:DCF_ONEDIR = "1"
try {
    python -m PyInstaller --clean --noconfirm --workpath build\onedir --distpath dist\onedir doubleclick-fixer.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed to build the installed folder" }
} finally {
    Remove-Item Env:\DCF_ONEDIR -ErrorAction SilentlyContinue
}
# A release lists one notices file for both Windows downloads (the one the
# installer puts beside the app), so the two must bundle the same software.
if ((Get-FileHash $portableNotices).Hash -ne (Get-FileHash $notices).Hash) {
    throw "the portable exe and the installed folder ship different third-party notices"
}
Write-Host "Built dist\DoubleClickFixer.exe and dist\onedir\DoubleClickFixer\."
