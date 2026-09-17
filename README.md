# DoubleClick Fixer

Cross-platform desktop utility for diagnosing mouse double-click behavior and filtering switch bounce without disabling normal double-clicks.

## What it does

- Learns from labeled calibration: isolated clicks first, intentional double-click pairs second.
- Separates normal double-click diagnosis from the shorter switch-bounce filter.
- Provides an opt-in system-wide fix on Windows and macOS.
- Runs from the Windows notification area or macOS menu bar.
- Remembers threshold, fix, startup, and calibration settings.
- Hides to the tray on close; use tray/menu-bar Quit to exit completely.

The fix is disabled during first-run setup and must be enabled explicitly after calibration.

## Windows

Run the portable build:

```text
dist\\DoubleClickFixer.exe
```

Run from source:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python run.py
```

## macOS

Build and open the app bundle on macOS:

```bash
bash installer/build_macos.sh
open dist/DoubleClickFixer.app
```

Grant Accessibility permission before enabling the system-wide fix. Remove the app with `bash installer/uninstall_macos.sh`.

## Calibration

1. Complete 10 isolated clicks: click once, wait, repeat.
2. Advance to double-click calibration.
3. Complete at least 3 intentional double-click pairs. Extra pairs improve the estimate.
4. Apply the suggestion, or edit the bounce filter manually.

The filter targets duplicate switch events, not ordinary human double-clicks. Values around 80-120 ms are common, but hardware varies.

## Tests

```text
python -m unittest discover -s tests -v
python -m compileall -q app run.py
```

## Packaging and releases

- Windows executable: `installer\\build_windows.ps1`
- Windows installer: compile `installer\\windows.iss` with Inno Setup
- macOS app and DMG: `bash installer/build_macos.sh`

CI tests Windows and macOS across supported Python versions. Pushing a tag such as `v0.1.0` builds both platform artifacts and publishes a GitHub Release with generated notes.

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), [CHANGELOG.md](CHANGELOG.md), and [installer/README.md](installer/README.md).
