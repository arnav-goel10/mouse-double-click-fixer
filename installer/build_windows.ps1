# Build the portable DoubleClickFixer.exe from Windows.
$ErrorActionPreference = "Stop"
python -c "import PyInstaller, PySide6" 2>$null
if ($LASTEXITCODE -ne 0) { python -m pip install -r requirements.txt pyinstaller }
python -m PyInstaller --clean --noconfirm doubleclick-fixer.spec
Write-Host "Built dist\DoubleClickFixer.exe. Compile installer\windows.iss with Inno Setup for the installer."
