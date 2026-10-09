# Build DoubleClick Fixer for Windows: the portable dist\DoubleClickFixer.exe
# (one file) and dist\onedir\DoubleClickFixer\ (the folder the installer
# ships). Then compile installer\windows.iss with Inno Setup for the installer.
$ErrorActionPreference = "Stop"
python -c "import PyInstaller, PySide6" 2>$null
if ($LASTEXITCODE -ne 0) { python -m pip install -r requirements.txt pyinstaller }

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
