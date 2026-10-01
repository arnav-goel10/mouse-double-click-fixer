# Contributing

Thanks for helping. Bug reports, fixes and improvements are all welcome.

## Reporting bugs

[Open an issue](https://github.com/arnav-goel10/doubleclick-fixer/issues/new/choose)
using the bug template. The app version, OS version, mouse model and filter
window make most problems quick to track down. Security issues go through
[SECURITY.md](SECURITY.md) instead.

## Development setup

Python 3.11 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py                      # run the app
python -m unittest discover -s tests
```

UI tests run offscreen (`QT_QPA_PLATFORM=offscreen`, set by the tests). The
Windows hook test only runs with `DCF_E2E=1`, because it injects real clicks;
CI runs it on a Windows machine, along with `tools/windows_install_e2e.ps1`,
which installs and replaces the app for real (never run it on your own PC).
Tests must not talk to a running copy of the app: patch `SERVER_NAME` in
`app/main.py` when testing the single-instance channel.

## How the code is laid out

| Path | Role |
| --- | --- |
| `app/core.py` | Click classification and calibration. Platform-neutral and fully unit-tested. |
| `app/platform.py` | The system-wide hooks (Windows and macOS). Keep the hook callback fast: no disk or UI work. |
| `app/controller.py` | App state; the UI talks to this, never to the hook directly. |
| `app/ui/` | The window, menu bar and tray, and platform styling. |
| `app/updater.py` | Updates from GitHub Releases. |
| `installer/` | Build scripts, the Inno Setup script and the DMG layout. |

Filtering is defined by the gap between a release and the next press; please
don't reintroduce press-to-press timing, which cannot tell bounce from a fast
double-click.

## Pull requests

- Keep each pull request to one change, and describe what changed, why, and
  how you tested it (the template asks).
- Changes to click handling need tests in `tests/`.
- Don't commit build output (`build/`, `dist/`), settings files or credentials.

By contributing, you agree that your contributions are licensed under the
[MIT License](LICENSE). Please follow the [Code of Conduct](CODE_OF_CONDUCT.md).
