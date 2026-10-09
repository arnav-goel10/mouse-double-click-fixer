"""Render every pane in light and dark, offscreen, for review and the docs.

    python tools/screenshots.py OUTPUT_DIR [--as-windows | --live] [--docs | --social]

`--as-windows` previews the Windows layout on another platform (fonts and
icons fall back, so use it for layout only). `--live` opens a real window and
captures it from the screen, so native materials (Mica, vibrancy) show; CI
uses it on the Windows runner. Settings are isolated in a temporary folder,
so this never touches a real configuration.

`--docs` captures only the Bounce Filter pane for the README, in the same state
and at the same window size as docs/images/macos.png (filter on, 46 ms,
calibrated, the same counts), so the two screenshots match side by side.

`--social` draws docs/images/social-preview.png, the 1280 x 640 card GitHub
shows for links to the repository (upload it under Settings › General ›
Social preview): the app icon, its name and what it does. It draws into an
image, offscreen, with the system font (SF Pro on a Mac).
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


def patch_taskbar(light: bool):
    from unittest import mock

    return mock.patch("app.ui.icons.taskbar_is_light", return_value=light)


def wait(app, milliseconds: int) -> None:
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


def social_preview():
    """The repository's social preview card, as a QImage."""
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetricsF, QImage, QLinearGradient, QPainter

    from app import DISPLAY_NAME
    from app.ui.icons import render_app_icon

    width, height = 1280, 640
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    background = QLinearGradient(0, 0, width, height)
    background.setColorAt(0.0, QColor("#f5f7ff"))
    background.setColorAt(1.0, QColor("#e3e9ff"))
    painter.fillRect(QRectF(0, 0, width, height), background)

    # The icon's 824-unit tile is 450 px across, 50 px clear of the text.
    size = round(450 * 1024 / 824)
    painter.drawImage(QPointF(350 - size / 2, 320 - size / 2), render_app_icon(size))

    family = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()

    def font(pixels: float, weight: QFont.Weight) -> QFont:
        face = QFont(family)
        face.setPixelSize(round(pixels))
        face.setWeight(weight)
        return face

    left, right = 624.0, width - 52.0
    # The name on two lines, the second the longer, as large as fits.
    first, _, second = DISPLAY_NAME.partition(" ")
    lines = [first, second] if second else [first]
    title = font(76, QFont.Weight.Bold)
    widest = max(QFontMetricsF(title).horizontalAdvance(line) for line in lines)
    if widest > right - left:
        title = font(76 * (right - left) / widest, QFont.Weight.Bold)
    subtitle = font(32, QFont.Weight.Normal)
    note = font(24, QFont.Weight.DemiBold)
    blocks = [
        (title, QColor("#141a33"), lines, 1.08),
        (subtitle, QColor("#3a4466"), ["Fix a mouse that double-clicks when you", "click once."], 1.25),
        (note, QColor("#2f57e0"), ["Free for macOS and Windows"], 1.0),
    ]
    gaps = [30.0, 40.0]
    heights = [QFontMetricsF(face).height() * spacing * len(text) for face, _colour, text, spacing in blocks]
    y = (height - sum(heights) - sum(gaps)) / 2
    for index, (face, colour, text, spacing) in enumerate(blocks):
        metrics = QFontMetricsF(face)
        painter.setFont(face)
        painter.setPen(colour)
        for line in text:
            painter.drawText(QPointF(left, y + metrics.ascent()), line)
            y += metrics.height() * spacing
        if index < len(gaps):
            y += gaps[index]
    painter.end()
    return image


def main() -> None:
    positional = [argument for argument in sys.argv[1:] if not argument.startswith("--")]
    out = Path(positional[0] if positional else "screenshots")
    out.mkdir(parents=True, exist_ok=True)
    if "--social" in sys.argv:
        from PySide6.QtGui import QGuiApplication

        _app = QGuiApplication([])
        social_preview().save(str(out / "social-preview.png"))
        print(f"Wrote {out / 'social-preview.png'}")
        return
    as_windows = "--as-windows" in sys.argv
    docs = "--docs" in sys.argv

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

    if docs:
        # Shown as on, without installing a real mouse hook.
        AppController.active = property(lambda self: True)  # type: ignore[assignment]
    controller = AppController()
    if docs:
        controller._store(threshold_ms=46, calibrated=True, fix_enabled=True)
        controller.settings["filtered_total"] = 8324
        controller.session_filtered = 945
    else:
        controller.settings["filtered_total"] = 1284
        controller.session_filtered = 37
    main_window = window_module.MainWindow(controller)
    main_window.resize(*((1010, 680) if docs else (780, 660)))
    main_window.show()
    main_window.raise_()
    main_window.activateWindow()
    app.processEvents()
    if LIVE:
        wait(app, 1500)

    if docs:
        main_window._show_page(0)
        main_window.refresh()
        app.processEvents()
        wait(app, 800 if LIVE else 50)
        if LIVE:
            frame = main_window.frameGeometry()
            main_window.screen().grabWindow(0, frame.x(), frame.y(), frame.width(), frame.height()).save(
                str(out / "docs-filter.png")
            )
        else:
            main_window.grab().save(str(out / "docs-filter.png"))
        print(f"Wrote {out / 'docs-filter.png'}")
        return

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
    # The notification area / menu bar icon, both states, on dark and light,
    # at the sizes the tray actually uses.
    from PySide6.QtGui import QColor, QImage, QPainter

    from app.ui import icons

    sizes = (16, 20, 24, 32)
    sheet = QImage(sum(sizes) * 2 + 16 * 9, 96, QImage.Format.Format_ARGB32)
    sheet.fill(QColor("#1c1c1c"))
    painter = QPainter(sheet)
    painter.fillRect(0, 48, sheet.width(), 48, QColor("#f3f3f3"))
    for row, light in enumerate((False, True)):
        with patch_taskbar(light):
            x = 16
            for size in sizes:
                for active in (False, True):
                    pixmap = icons.tray_icon(active).pixmap(size, size)
                    painter.drawPixmap(x, row * 48 + (48 - size) // 2, pixmap)
                    x += size + 16
    painter.end()
    sheet.scaled(sheet.width() * 3, sheet.height() * 3).save(str(out / "tray-icons.png"))
    print(f"Wrote screenshots to {out}")


if __name__ == "__main__":
    main()
