# Build DoubleClick Fixer for Windows: the portable dist\DoubleClickFixer.exe
# (one file) and dist\onedir\DoubleClickFixer\ (the folder the installer
# ships). Then compile installer\windows.iss with Inno Setup for the installer.
$ErrorActionPreference = "Stop"
# Build with exactly the pinned packages and build tools, each file checked
# against its hash (requirements-build.txt; see requirements-build.in).
python -m pip install --require-hashes --only-binary :all: -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw "pip could not install requirements-build.txt" }

# Separate work folders: both builds make an exe called DoubleClickFixer.
Remove-Item Env:\DCF_ONEDIR -ErrorAction SilentlyContinue
python -m PyInstaller --clean --noconfirm doubleclick-fixer.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed to build the portable exe" }

$env:DCF_ONEDIR = "1"
try {
    python -m PyInstaller --clean --noconfirm --workpath build\onedir --distpath dist\onedir doubleclick-fixer.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed to build the installed folder" }
} finally {
    Remove-Item Env:\DCF_ONEDIR -ErrorAction SilentlyContinue
}
Write-Host "Built dist\DoubleClickFixer.exe and dist\onedir\DoubleClickFixer\."
