# Installation

## Windows installer

1. Open the repository's **Releases** page.
2. Download `DoubleClickFixer-Setup.exe`.
3. Run the installer and choose whether to start at login.
4. Launch DoubleClick Fixer from the Start menu.
5. Complete calibration before enabling the fix.

The portable `DoubleClickFixer.exe` can be run without installation. The installer includes an uninstaller that removes the app, shortcuts, startup entry, and saved settings.

## Windows from source

```powershell
cd C:\path\to\doubleclick-fixer
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python run.py
```

If PowerShell blocks activation, run `Set-ExecutionPolicy -Scope Process Bypass` in that terminal only.

## macOS release

1. Download `DoubleClickFixer.dmg` from **Releases**.
2. Open the DMG and drag `DoubleClickFixer.app` to Applications.
3. Open the app.
4. In System Settings, grant Accessibility permission when prompted.
5. Complete calibration, then enable the fix.

For a local build:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
bash installer/build_macos.sh
open dist/DoubleClickFixer.app
```

## Uninstall

Windows: use **Apps > Installed apps > DoubleClick Fixer > Uninstall**.

macOS: run `bash installer/uninstall_macos.sh`, then remove the app from Applications.
