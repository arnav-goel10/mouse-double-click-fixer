"""Render every pane in light and dark, offscreen, for review and the docs.

    python tools/screenshots.py OUTPUT_DIR [--as-windows | --live]

`--as-windows` previews the Windows layout on another platform (fonts and
icons fall back, so use it for layout only). `--live` opens a real window and
captures it from the screen, so native materials (Mica, vibrancy) show; CI
uses it on the Windows runner. Settings are isolated in a temporary folder,
so this never touches a real configuration.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LIVE = "--live" in sys.argv
if not LIVE:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from run import _unhide_qt_plugins  # noqa: E402

_unhide_qt_plugins()


def wait(app, milliseconds: int) -> None:
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


def main() -> None:
    positional = [argument for argument in sys.argv[1:] if not argument.startswith("--")]
    out = Path(positional[0] if positional else "screenshots")
    out.mkdir(parents=True, exist_ok=True)
    as_windows = "--as-windows" in sys.argv

    from app import settings

    folder = Path(tempfile.mkdtemp())
    settings.config_dir = lambda: folder  # type: ignore[assignment]
    settings.LEGACY_PATH = folder / "none.json"

    if as_windows:
        from app.ui import theme

        theme.IS_MAC, theme.IS_WINDOWS = False, True

    from PySide6.QtWidgets import QApplication

    app = QApplication([])
    from app import permissions
    from app.controller import AppController
    from app.ui import widgets, window as window_module
    from app.ui.theme import current_look

    if as_windows:
        for module in (widgets, window_module):
            module.IS_MAC = False
        permissions.needs_accessibility = lambda: False  # type: ignore[assignment]
        from app.ui import symbols

        symbols.IS_MAC, symbols.IS_WINDOWS = False, False

    controller = AppController()
    controller.settings["filtered_total"] = 1284
    controller.session_filtered = 37
    main_window = window_module.MainWindow(controller)
    main_window.resize(780, 660)
    main_window.show()
    main_window.raise_()
    main_window.activateWindow()
    app.processEvents()
    if LIVE:
        wait(app, 1500)

    test = main_window.test_page
    for gap in [410, 520, 9, 380, 460, 12, 590, 350, 470, 7, 520, 400, 610, 380, 11, 500, 430, 560]:
        test._on_pad_press(float(gap), float(gap + 60))
    test.pad.set_flash_level(0.0)

    suffix = "-windows" if as_windows else ""
    # Live captures use the system's own appearance, because native controls
    # follow it and cannot be switched from here.
    for dark in ([None] if LIVE else [False, True]):
        widgets.set_look(current_look(dark))
        dark = widgets.look().dark
        for label in main_window.findChildren(widgets.TextLabel):
            label.restyle()
        for index, (key, _title) in enumerate(window_module.PAGES):
            main_window._show_page(index)
            if key == "calibrate":
                page = main_window.calibrate
                page.restart()
                page._advance()
                for _ in range(5):
                    page._on_pad_press(900.0, 960.0)
                page.pad.set_flash_level(0.0)
            app.processEvents()
            # Offscreen, animations never advance; settle them before grabbing.
            from PySide6.QtCore import QPropertyAnimation

            for animation in main_window.findChildren(QPropertyAnimation):
                animation.stop()
            for pad in (main_window.test_page.pad, main_window.calibrate.pad):
                pad.set_flash_level(0.0)
            name = f"{key}-{'dark' if dark else 'light'}{suffix}.png"
            if LIVE:
                wait(app, 600)
                frame = main_window.frameGeometry()
                screen = main_window.screen()
                screen.grabWindow(0, frame.x(), frame.y(), frame.width(), frame.height()).save(
                    str(out / name.replace(".png", "-live.png"))
                )
            else:
                main_window.grab().save(str(out / name))
    if LIVE:
        # The narrowest the window can get, to check nothing is clipped.
        main_window._show_page(0)
        main_window.resize(main_window.minimumSize())
        wait(app, 800)
        frame = main_window.frameGeometry()
        main_window.screen().grabWindow(0, frame.x(), frame.y(), frame.width(), frame.height()).save(
            str(out / "filter-minimum-live.png")
        )
    print(f"Wrote screenshots to {out}")


if __name__ == "__main__":
    main()
