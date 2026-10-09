# Contributing

Thanks for helping. Bug reports, fixes and improvements are all welcome.

## Reporting bugs

[Open an issue](https://github.com/arnav-goel10/mouse-double-click-fixer/issues/new/choose)
using the bug template, and paste what **General › Troubleshooting › Copy
Diagnostics** (Windows: **Copy diagnostics**) gives you: it has the app
version, OS, the filter's state, the settings and the app's recent log. Your
mouse model and what the **Test** pane shows help too. Security issues go
through [SECURITY.md](SECURITY.md) instead.

## Development setup

Python 3.11 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py                      # run the app
python -m unittest discover -s tests
```

UI tests run offscreen (`QT_QPA_PLATFORM=offscreen`, set by the tests).

## Keep tests off the real machine

The app reads and writes your settings, your login item and its
single-instance channel, and a test that reaches them can overwrite your
settings or quit the copy you have running. So:

- Every test module starts with this block, before any other import. It
  points the home, temporary and app-data folders at a throwaway directory
  and gives the single-instance channel a test-only name
  (`tests/_isolation.py`). `tests/test_isolation.py` fails if a module
  doesn't.

  ```python
  try:
      import _isolation  # noqa: F401  (first: keeps tests off the real machine)
  except ImportError:  # run as tests.<module> from the repository root
      from tests import _isolation  # noqa: F401
  ```

- Anything else that imports app code, such as a scratch script or a
  one-off check, runs with a temporary home and temporary folder:

  ```bash
  HOME=$(mktemp -d) TMPDIR=$(mktemp -d)/ python my_check.py
  ```

  On Windows, point `USERPROFILE`, `APPDATA`, `LOCALAPPDATA`, `TEMP` and `TMP`
  at temporary folders instead.
- Tests never post input, move the pointer, open System Settings or start
  the app. Patch the call that would, and check the patch is reached.

Some tests touch the system on purpose, and only in CI:

- `DCF_E2E=1` turns on the end-to-end tests, which send real input. On
  Windows, `tests/test_windows_hook.py` installs the real low-level hook and
  injects real clicks and moves. On macOS, `tests/test_macos_tap_e2e.py`
  starts the real event tap, moves the pointer and posts real clicks. Never
  set it on your own computer. CI runs them on its Windows machines and on
  macOS 14 and 26.
- The macOS test also runs only where `GITHUB_ACTIONS=true` (CI sets it) or
  `DCF_E2E_ALLOW_LOCAL=1`; never set either on your own Mac. Asked for with
  `DCF_E2E=1` where it can't run, it fails instead of skipping.
- `tools/windows_install_e2e.ps1` installs, upgrades, updates and uninstalls
  the app for real. CI runs it; never run it on a PC whose copy of
  Mouse Double-Click Fixer you care about.

On a Mac where the Python running the tests is allowed to filter input, two
unit tests start the real event tap and stop it again. While the first one
runs, for a moment, it filters left clicks with a 60 ms window, as the app
would; the second filters no button and only watches. Neither posts any
input of its own.

## How the code is laid out

| Path | Role |
| --- | --- |
| `app/core.py` | Click classification and calibration. Platform-neutral and fully unit-tested. |
| `app/platform.py` | The system-wide hooks (Windows and macOS). Keep the hook callback fast: no disk or UI work. |
| `app/controller.py` | App state; the UI talks to this, never to the hook directly. |
| `app/ui/` | The window, menu bar and tray, and platform styling. |
| `app/updater.py`, `app/update_signature.py` | Updates from GitHub Releases, and the minisign check every update must pass. |
| `app/permissions.py`, `app/startup.py`, `app/settings.py` | The macOS permission, open at login, and the settings file. |
| `app/diagnostics.py` | The log, crash.log and the Copy Diagnostics report. |
| `app/selftest.py` | `--self-test`, which checks that a built app can run. |
| `installer/` | Build scripts, the Inno Setup script, the DMG layout and the macOS entitlements. |
| `tools/` | Release signing, release notes, third-party notices and the end-to-end scripts. |

Filtering is defined by the gap between a release and the next press; please
don't reintroduce press-to-press timing, which cannot tell bounce from a fast
double-click.

The hook threads never wait on the disk or the UI: log through `logging`
(the app writes the log from a queue), and never log per click. On Windows,
input is never sent from the hook's own thread; it goes through the sender
thread in `app/platform.py`.

## Pull requests

- Keep each pull request to one change, and describe what changed, why, and
  how you tested it (the template asks).
- Changes to click handling need tests in `tests/`.
- Say in the description what a user would notice, so it can go in the
  changelog.
- Don't commit build output (`build/`, `dist/`), settings files, keys or
  other credentials.

By contributing, you agree that your contributions are licensed under the
[MIT License](LICENSE). Please follow the [Code of Conduct](CODE_OF_CONDUCT.md).
