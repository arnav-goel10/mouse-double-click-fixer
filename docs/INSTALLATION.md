# Installation

## Windows

1. Download `DoubleClickFixer-Setup.exe` from the [releases page](https://github.com/arnav-goel10/doubleclick-fixer/releases).
2. Run it. SmartScreen may warn about an unsigned publisher; choose **More
   info > Run anyway** if you trust the download.
3. Optionally tick "Start DoubleClick Fixer when I sign in".
4. Launch it from the Start menu, calibrate, then turn the filter on.

`DoubleClickFixer.exe` is the same app without an installer.

## macOS

1. Download `DoubleClickFixer.dmg`, open it, and drag DoubleClick Fixer onto Applications.
2. The build is not notarized yet, so the first launch needs
   **right-click > Open**, or **System Settings > Privacy & Security > Open
   Anyway**.
3. Turn the filter on. macOS will refuse until the app is listed under
   **Privacy & Security > Accessibility**; the app links straight to that pane.
4. Calibrate, then turn the filter on again.

## From source

Python 3.11 or newer, on either platform.

```bash
git clone https://github.com/arnav-goel10/doubleclick-fixer
cd doubleclick-fixer
python -m venv .venv
source .venv/bin/activate         # Windows: .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python run.py
```

If PowerShell blocks activation, run `Set-ExecutionPolicy -Scope Process Bypass`
in that terminal only.

macOS note: when running from source, the Accessibility permission belongs to
the *interpreter* that owns the process — usually Terminal or your editor — so
grant it to that app, or build the bundle with `bash installer/build_macos.sh`
and grant it to DoubleClick Fixer itself.

## Uninstall

- **Windows**: Settings > Apps > Installed apps > DoubleClick Fixer > Uninstall.
  This removes the app, its shortcuts, the startup entry and saved settings.
- **macOS**: `bash installer/uninstall_macos.sh`, which removes the app, the
  LaunchAgent and saved settings. Also remove the entry from the Accessibility
  list in System Settings.
