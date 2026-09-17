# Project Instructions

- Cross-platform Python desktop utility: Qt (PySide6) interface, native mouse
  hooks underneath.
- Keep click classification and calibration platform-neutral in `app/core.py`.
- Keep OS-specific capture in `app/platform.py`, startup integration in
  `app/startup.py`, and permission checks in `app/permissions.py`. The UI must
  not call the platform layer directly; it goes through `app/controller.py`.
- The hook callback runs on a hook thread and must stay fast: no disk writes,
  no UI calls. Windows silently drops a low-level hook that takes too long.
- Filtering is defined by the gap between a release and the next press. Do not
  reintroduce press-to-press timing; it cannot separate bounce from a fast
  double-click.
- Use the standard library for tests unless a dependency is essential. UI tests
  run with `QT_QPA_PLATFORM=offscreen`.
- Validate with `python -m unittest discover -s tests`.
