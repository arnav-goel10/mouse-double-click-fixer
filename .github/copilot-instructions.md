# Project Instructions

- Cross-platform Python desktop utility: Qt (PySide6) interface, native mouse
  hooks underneath (a macOS event tap, a Windows low-level mouse hook).
- The app is Mouse Double-Click Fixer (DoubleClick Fixer before 1.0). Text
  people see names it through `DISPLAY_NAME` in `app/__init__.py`, never a
  literal. Identifiers installed copies depend on keep the old spelling: the
  bundle id `com.doubleclickfixer.app`, the `DoubleClickFixer` executable,
  the settings folders, the login item, the release file names and the
  repository's `doubleclick-fixer.spec`.
- Keep click classification and calibration platform-neutral in `app/core.py`.
- Keep OS-specific capture in `app/platform.py`, startup integration in
  `app/startup.py`, and permission checks in `app/permissions.py`. The UI must
  not call the platform layer directly; it goes through `app/controller.py`.
- The hook callback runs on a hook thread and must stay fast: no disk writes,
  no UI calls. Windows silently drops a low-level hook that takes too long, and
  macOS disables a slow tap. Log through `logging` (written from a queue),
  never per click. On Windows, never send input from the hook's own thread.
- Filtering is defined by the gap between a release and the next press. Do not
  reintroduce press-to-press timing; it cannot separate bounce from a fast
  double-click.
- Updates must stay signature-checked (`app/update_signature.py`); never add a
  path that installs an update without the minisign check.
- Use the standard library for tests unless a dependency is essential. UI tests
  run with `QT_QPA_PLATFORM=offscreen`. Every test module imports
  `tests/_isolation.py` first, so no test touches the user's settings, login
  item or running copy; tests never post input or open apps, except the
  end-to-end tests that CI runs with `DCF_E2E=1`
  (`tests/test_windows_hook.py`, `tests/test_macos_tap_e2e.py`).
- Validate with `python -m unittest discover -s tests`.
