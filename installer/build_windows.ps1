$ErrorActionPreference = "Stop"
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --clean --noconfirm doubleclick-fixer.spec
Write-Host "Built dist\DoubleClickFixer.exe. Compile installer\windows.iss with Inno Setup to create the installer."
