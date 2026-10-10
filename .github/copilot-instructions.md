# Project Instructions

- Cross-platform Python desktop utility: Qt (PySide6) interface, native mouse
  hooks underneath (a macOS event tap, a Windows low-level mouse hook).
- The app is Mouse Double-Click Fixer (DoubleClick Fixer before 1.0). Text
  people see names it through `DISPLAY_NAME` in `app/__init__.py`, never a
  literal. Identifiers installed copies depend on keep the old spelling: the
  bundle id `com.doubleclickfixer.app`, the `DoubleClickFixer` executable,
  the settings folders, the login item, the release file names and the
  repository's `doubleclick-fixer.spec`.
- Keep click classification, the scroll-wheel rule and calibration
  platform-neutral in `app/core.py`.
- Keep OS-specific capture in `app/platform.py`, startup integration in
  `app/startup.py`, and permission checks in `app/permissions.py`. Which device
  a click came from is `app/devices_mac.py` and `app/devices_win.py`, and which
  app is in front is `app/frontmost.py`. The UI must not call the platform
  layer directly; it goes through `app/controller.py`.
- The filter takes one `FilterConfig` (a window for every button, the filtered
  buttons, the wheel fix, the excluded apps and the ignored devices). Back and
  forward use the drop rule only and never hold a release back. Clicks from
  trackpads, touchscreens and pens, from an ignored device, or while an
  excluded app is in front pass untouched (on Windows, only precision
  touchpads count, and a tap within a second of any mouse report is that
  mouse's click).
- The hook callback runs on a hook thread and must stay fast: no disk writes,
  no UI calls. Windows silently drops a low-level hook that takes too long, and
  macOS disables a slow tap. Log through `logging` (written from a queue),
  never per click. On Windows, never send input from the hook's own thread.
- Filtering is defined by the gap between a release and the next press. Do not
  reintroduce press-to-press timing; it cannot separate bounce from a fast
  double-click.
- Updates must stay signature-checked (`app/update_signature.py`); never add a
  path that installs an update without the minisign check.
- Text people read (README, release notes, the app's own strings) says what the
  app costs: releases of the left, right and middle buttons are held for the
  filter window, and the app re-sends some input. Don't write that it adds no
  delay, that it is safe with anti-cheat, or that the apps are code-signed:
  only the updates are signed.
- Use the standard library for tests unless a dependency is essential. UI tests
  run with `QT_QPA_PLATFORM=offscreen`. Every test module imports
  `tests/_isolation.py` first, so no test touches the user's settings, login
  item or running copy; tests never post input or open apps, except the
  end-to-end tests that CI runs with `DCF_E2E=1`
  (`tests/test_windows_hook.py`, `tests/test_macos_tap_e2e.py`).
- Validate with `python -m unittest discover -s tests`.
