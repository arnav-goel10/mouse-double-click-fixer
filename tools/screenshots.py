"""Render every pane in light and dark, offscreen, for review and the docs.

    python tools/screenshots.py OUTPUT_DIR [--as-windows | --live [--scheme=light|dark]] [--docs | --social]

`--as-windows` previews the Windows layout on another platform (fonts and
icons fall back, so use it for layout only). Offscreen, Qt draws no native
control: buttons, pop-up buttons and the rest come out in Qt's own Fusion
style, so only a live capture shows what users see.

`--live` opens a real window on the real window server and captures every
pane from the screen, so native controls, materials (Mica, vibrancy) and the
title bar show as they do for users (`NN-pane-SCHEME.png`, and `-2.png` on
for the rest of a pane taller than the window), then the pop-ups and menus
open (`popup-*.png`) and the narrowest window. `--scheme` asks for the light
or dark appearance. CI's `screenshots` job runs it on macOS and Windows, in
both (the macos-screenshots and windows-screenshots artifacts). It opens a
window and, on macOS, presses Escape to close each menu, so run it on a
machine where that is welcome. Settings are isolated in a temporary folder,
so this never touches a real configuration.

`--docs` captures only the Bounce Filter pane for the README, in the same
state on both platforms (the filter on, three buttons with windows of their own,
the wheel fix on) so the two screenshots match side by side. Nothing is
scrolled or cut through: a pane the window can't show whole ends below the last
section that fits.

    docs/images/windows.png  CI's `--live --docs` run on Windows (the
                             windows-screenshots artifact's docs-filter.png): a
                             real window, title bar included, as tall as the
                             runner's 1024 x 768 screen allows.
    docs/images/macos.png    CI's `--live --docs --size=WIDTHxHEIGHT` run on
                             macOS (the macos-screenshots artifact's
                             docs-filter.png), dark, with the Windows picture's
                             size so the two match. Offscreen, `--docs` draws
                             the window with its corners and window buttons
                             instead, but Qt draws no native control there.

Without `--size` the window is as tall as the pane needs (a live window is held
to what the screen shows).

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


DOCS_WIDTH = 860
#: A Windows window's title bar, which the live capture includes.
WINDOWS_TITLE_BAR = 31


def docs_state(controller) -> None:
    """The Filter pane as the README shows it: on, with three buttons
    filtered, each with a window of its own, the wheel fix on and the counts a
    few months of use leaves."""
    from app.core import Button

    controller.set_buttons([Button.LEFT, Button.RIGHT, Button.BACK])
    controller.set_threshold(Button.LEFT, 46)
    controller.set_calibrated(Button.LEFT)
    controller.set_threshold(Button.BACK, 30)
    controller.set_wheel_fix(True)
    controller.settings["filtered_total"] = 8324
    controller.session_filtered = 945


def parse_size(argv) -> tuple[int, int] | None:
    for argument in argv:
        if argument.startswith("--size="):
            width, _, height = argument[len("--size="):].partition("x")
            return int(width), int(height)
    return None


def fit_pane(app, window, room: int | None = None) -> tuple[int, int | None]:
    """The window height that shows the current pane whole, no scrolling, and
    the index of the last item of the page to keep (None: all of them).

    Squeezed to its shortest the pane is as tall as its content, so the
    difference to the viewport is what the rest of the window adds. With
    `room` (the most the screen can show) and a pane taller than that, the
    height ends below the last section or note that fits, never through a
    row and never between a heading and what it heads.
    """
    from PySide6.QtCore import QPoint

    window.resize(window.width(), window.minimumHeight())
    app.processEvents()
    area = window.stack.currentWidget()
    page = area.widget()
    chrome = window.height() - area.viewport().height()
    full = chrome + page.height()
    if room is None or full <= room:
        return full, None
    pad = 14
    fits = []
    for index in range(page.body.count()):
        item = page.body.itemAt(index).widget()
        if item is None or getattr(item, "role", "") in ("headline", "title"):
            continue
        bottom = chrome + item.mapTo(page, QPoint(0, item.height())).y() + pad
        if bottom <= room:
            fits.append((bottom, index))
    return max(fits)


def dark_palette():
    """What macOS's Dark appearance gives the native controls."""
    from PySide6.QtGui import QColor, QPalette

    palette = QPalette()
    Role, Group = QPalette.ColorRole, QPalette.ColorGroup
    for role, colour in (
        (Role.Window, "#1e1e1e"), (Role.WindowText, "#ececec"), (Role.Base, "#3b3b3b"),
        (Role.AlternateBase, "#2a2a2a"), (Role.Text, "#ececec"), (Role.Button, "#3b3b3b"),
        (Role.ButtonText, "#ececec"), (Role.ToolTipBase, "#2a2a2a"), (Role.ToolTipText, "#ececec"),
        (Role.PlaceholderText, "#8a8a8a"), (Role.Highlight, "#0a84ff"),
        (Role.Accent, "#0a84ff"), (Role.HighlightedText, "#ffffff"), (Role.Light, "#5a5a5a"), (Role.Mid, "#2d2d2d"),
        (Role.Dark, "#151515"), (Role.Midlight, "#4a4a4a"), (Role.Shadow, "#000000"),
    ):
        palette.setColor(role, QColor(colour))
    for role in (Role.WindowText, Role.Text, Role.ButtonText):
        palette.setColor(Group.Disabled, role, QColor("#6e6e6e"))
    palette.setColor(Group.Disabled, Role.Base, QColor("#2c2c2c"))
    palette.setColor(Group.Disabled, Role.Button, QColor("#2c2c2c"))
    return palette


def mac_frame(grabbed, width: int, height: int):
    """A window grab drawn as a Mac window, since offscreen there is no window
    server to supply one: rounded corners, a hairline edge and the three window
    buttons over the sidebar."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen

    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    outline = QRectF(0.5, 0.5, width - 1, height - 1)
    clip = QPainterPath()
    clip.addRoundedRect(outline, 20, 20)
    painter.setClipPath(clip)
    painter.drawImage(0, 0, grabbed)
    painter.setClipping(False)
    painter.setPen(QPen(QColor(255, 255, 255, 46), 1))
    painter.drawPath(clip)
    painter.setPen(Qt.PenStyle.NoPen)
    for index, colour in enumerate(("#ff5f57", "#febc2e", "#28c840")):
        painter.setBrush(QColor(colour))
        painter.drawEllipse(QRectF(20 + index * 20, 20, 13, 13))
    painter.end()
    return image


def sample_state(controller) -> None:
    """Something to show on every pane: a few buttons with their own windows
    (the left one calibrated to a window none of the presets has), the wheel
    fix, excluded apps, the devices a Mac usually has, and a month of wear on
    a switch that is getting worse."""
    import time

    from app.core import Button, ClickEvent
    from app.platform import DeviceInfo

    controller.set_buttons([Button.LEFT, Button.RIGHT, Button.BACK])
    controller.set_threshold(Button.LEFT, 59)
    controller.set_calibrated(Button.LEFT)
    controller.set_threshold(Button.BACK, 30)
    controller.set_wheel_fix(True)
    controller.add_excluded_app("com.valvesoftware.steam", "Steam")
    controller.add_excluded_app("cs2.exe", "Counter-Strike 2")
    controller.set_device_ignored("usb:046D:C08B:0F3A1B", "Logitech G502 HERO", True)
    # The Apps pane's menu offers these as running, whatever this computer runs.
    from app import app_keys

    running = [("com.apple.Safari", "Safari"), ("com.apple.mail", "Mail"), ("com.spotify.client", "Spotify")]
    app_keys.running_apps = lambda: [app_keys.AppChoice(key, name) for key, name in running]
    now = time.time()
    controller._devices = {
        "usb:03F0:1F4A:HP": DeviceInfo("usb:03F0:1F4A:HP", "HP 2.4G Wireless and BT Mouse", "mouse", True, now - 30),
        "bt:05AC:0269:A1": DeviceInfo("bt:05AC:0269:A1", "Magic Mouse", "mouse", True, now - 900),
        "usb:05AC:0342:T": DeviceInfo("usb:05AC:0342:T", "Apple Internal Keyboard / Trackpad", "trackpad", False, now),
    }
    wear = controller.wear
    clock = wear._clock
    gaps = [7, 9, 11, 8, 12, 10, 14, 9, 6, 17, 10, 13, 8, 22, 11, 9, 15, 12, 26, 10]
    for day in range(30):
        wear._clock = lambda day=day: now - (29 - day) * 86400
        clicks = 900 + (day * 137) % 700
        bounces = 3 + day // 3 + day % 4
        for _ in range(clicks):
            wear.note_event(ClickEvent(Button.LEFT, True, True, 400.0, None), 46)
        for index in range(bounces):
            wear.note_event(ClickEvent(Button.LEFT, True, False, float(gaps[(index + day) % len(gaps)]), None), 46)
        if day % 5 == 0:
            wear.note_event(ClickEvent(Button.LEFT, True, False, 5.0, None, cancels_held=True), 46)
        for index in range(400):
            wear.note_wheel(1, index % 97 == 0)
    wear._clock = clock


def capture_docs(app, main_window, target: Path, size: tuple[int, int] | None) -> None:
    """The Filter pane into `target`: whole if the window can show it whole."""
    from PySide6.QtCore import Qt

    main_window._show_page(0)
    main_window.refresh()
    # What is cut off is not worth a scroll bar in a picture.
    main_window.stack.currentWidget().setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    app.processEvents()
    if not LIVE:
        # Offscreen, the picture is the window as the Mac draws one: the
        # whole window, so `size` counts the title bar's room too.
        width, height = size or (DOCS_WIDTH, 0)
        wanted, last = fit_pane(app, main_window, height or None)
        height = height or wanted
        page = main_window.stack.currentWidget().widget()
        for index in range(page.body.count() if last is not None else 0):
            if index > last and page.body.itemAt(index).widget() is not None:
                page.body.itemAt(index).widget().hide()
        main_window.resize(width, height)
        app.processEvents()
        wait(app, 50)
        mac_frame(main_window.grab().toImage(), width, height).save(str(target))
        return
    # A live window is a real one, held to what the screen can show.
    screen = main_window.screen()
    available = screen.availableGeometry()
    print(f"Screen {screen.geometry().width()} x {screen.geometry().height()}, "
          f"available {available.width()} x {available.height()}, scale {screen.devicePixelRatio()}")
    main_window.move(available.x(), available.y())
    wait(app, 300)
    # A Mac window draws its title bar inside itself; Windows adds one.
    title_bar = 0 if sys.platform == "darwin" else WINDOWS_TITLE_BAR
    room = available.height() - title_bar - 8
    height = min(size[1] - title_bar, room) if size else room
    wanted, last = fit_pane(app, main_window, height)
    # As offscreen: end below the last section that fits, never through a row.
    page = main_window.stack.currentWidget().widget()
    for index in range(page.body.count() if last is not None else 0):
        if index > last and page.body.itemAt(index).widget() is not None:
            page.body.itemAt(index).widget().hide()
    height = height if size else min(wanted, room)
    print(f"The pane shows {wanted} px of the {room} the screen leaves; the window is {height} tall")
    main_window.resize(size[0] if size else DOCS_WIDTH, height)
    main_window.move(available.x(), available.y())
    app.processEvents()
    wait(app, 800)
    frame = main_window.frameGeometry()
    print(f"Window frame {frame.width()} x {frame.height()}: {grab_window_live(app, main_window, target)}")


def parse_scheme(argv) -> str | None:
    """`--scheme=light` or `--scheme=dark`: the appearance a live capture
    asks the system for (Qt's QStyleHints.setColorScheme, which sets the
    app's own appearance on macOS and the app's theme on Windows)."""
    for argument in argv:
        if argument.startswith("--scheme="):
            scheme = argument[len("--scheme="):]
            if scheme not in ("light", "dark"):
                raise SystemExit(f"--scheme must be light or dark, not {scheme!r}")
            return scheme
    return None


def sample_updater(controller):
    """An updater that says the app is up to date, so General shows its
    Software update rows as an installed copy does. It never starts, so it
    never goes online."""
    from app.updater import Updater

    updater = Updater(controller)
    updater.kind = "installed"
    updater.state = updater.CURRENT
    updater.message = "Up to date"
    return updater


def settle(app, main_window) -> None:
    """Stop animations (offscreen they never advance) and clear the pads' flash."""
    from PySide6.QtCore import QPropertyAnimation

    from app.ui.widgets import Switch

    app.processEvents()
    for animation in main_window.findChildren(QPropertyAnimation):
        animation.stop()
    for pad in (main_window.test_page.pad, main_window.calibrate.pad):
        pad.set_flash_level(0.0)
    for switch in main_window.findChildren(Switch):
        switch.set_position(1.0 if switch.isChecked() else 0.0)
    app.processEvents()


def measuring_state(page) -> None:
    """Calibrate part-way through its single clicks."""
    page.restart()
    page._advance()
    for _ in range(5):
        page._on_pad_press(900.0, 960.0)


def result_state(page) -> None:
    """Calibrate finished, with a result and the note it adds."""
    from app.core import REQUIRED_DOUBLE_CLICKS, REQUIRED_SINGLE_CLICKS

    page.restart()
    page._advance()
    for _ in range(REQUIRED_SINGLE_CLICKS):
        page._on_pad_press(900.0, 960.0)
    for _ in range(REQUIRED_DOUBLE_CLICKS):
        page._on_pad_press(900.0, 960.0)
        page._on_pad_press(150.0, 210.0)


def _on_real_screen() -> bool:
    """True with the real window system. Offscreen (a dry run of `--live`)
    nothing may reach this computer's screen or keyboard."""
    from PySide6.QtGui import QGuiApplication

    return QGuiApplication.platformName() in ("cocoa", "windows")


def _uniform(image) -> bool:
    """Whether `image` is one colour throughout (a capture that saw nothing)."""
    if image.isNull() or image.width() < 4 or image.height() < 4:
        return True
    first = image.pixel(0, 0)
    step_x, step_y = max(1, image.width() // 23), max(1, image.height() // 19)
    return all(
        image.pixel(x, y) == first for x in range(0, image.width(), step_x) for y in range(0, image.height(), step_y)
    )


def _screencapture(arguments: list[str], target: Path) -> bool:
    import subprocess

    target.unlink(missing_ok=True)
    result = subprocess.run(["screencapture", "-x", *arguments, str(target)], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"screencapture {' '.join(arguments)}: {result.stderr.strip() or result.returncode}")
    return target.exists() and target.stat().st_size > 0


def _mac_window_number(widget) -> int | None:
    try:
        import objc

        view = objc.objc_object(c_void_p=int(widget.winId()))
        return int(view.window().windowNumber())
    except Exception as error:  # noqa: BLE001 - a fallback follows
        print(f"No window number: {error}")
        return None


def grab_screen(app, rect, target: Path) -> str:
    """What the screen shows inside `rect` (global coordinates), menus and
    pop-ups included. Returns how it was captured."""
    screen = app.primaryScreen()
    rect = rect.intersected(screen.geometry())
    pixmap = screen.grabWindow(0, rect.x(), rect.y(), rect.width(), rect.height())
    if not pixmap.isNull() and not _uniform(pixmap.toImage()):
        pixmap.save(str(target))
        return "QScreen"
    if sys.platform == "darwin" and _on_real_screen():
        region = f"-R{rect.x()},{rect.y()},{rect.width()},{rect.height()}"
        if _screencapture([region], target):
            return "screencapture"
    if not pixmap.isNull():
        pixmap.save(str(target))
        return "QScreen (uniform)"
    return "nothing"


def grab_window_live(app, main_window, target: Path) -> str:
    """The real window as the screen shows it: title bar, materials and
    native controls as the window server composites them."""
    if sys.platform == "darwin" and _on_real_screen():
        number = _mac_window_number(main_window)
        if number is not None and _screencapture(["-o", f"-l{number}"], target):
            return "screencapture -l"
    return grab_screen(app, main_window.frameGeometry(), target)


def dismiss_popups(app) -> None:
    """Close whatever pop-up or menu is open: Qt's own, and on macOS a native
    menu (NSMenu tracking), with the Escape key."""
    from PySide6.QtWidgets import QApplication

    popup = QApplication.activePopupWidget()
    if popup is not None:
        popup.close()
    if sys.platform == "darwin" and _on_real_screen():
        try:
            import Quartz

            for down in (True, False):
                event = Quartz.CGEventCreateKeyboardEvent(None, 53, down)  # Escape
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        except Exception as error:  # noqa: BLE001
            print(f"Couldn't post Escape: {error}")


def capture_popup(app, main_window, open_popup, target: Path) -> None:
    """Open a pop-up (an AppKit menu on macOS runs its own event loop until
    it closes), photograph the screen around the window, then close it."""
    from PySide6.QtCore import QMargins, QTimer
    from PySide6.QtWidgets import QApplication

    returned = []

    def photograph() -> None:
        area = main_window.frameGeometry().marginsAdded(QMargins(40, 40, 40, 360))
        how = grab_screen(app, area, target)
        print(f"{target.name}: {how}")
        dismiss_popups(app)

    def still_open() -> None:
        # Should the first Escape miss, never wait for ever; but a stray
        # Escape would reach the window and show up in the next picture.
        if not returned or QApplication.activePopupWidget() is not None:
            print(f"{target.name}: closing it again")
            dismiss_popups(app)

    timers = []
    for delay, run in ((1200, photograph), (4000, still_open), (7000, still_open)):
        timer = QTimer(main_window)
        timer.setSingleShot(True)
        timer.timeout.connect(run)
        timer.start(delay)
        timers.append(timer)
    open_popup()
    returned.append(True)
    wait(app, 1500)
    for timer in timers:  # this pop-up's, not the next one's
        timer.stop()
        timer.deleteLater()


def capture_live(app, main_window, out: Path, scheme: str) -> None:
    """Every pane in a real window, as the screen shows it (`NN-pane-scheme.png`,
    then `-2.png` and on for the rest of a long one); then the pop-ups and
    menus, the narrowest window, and the window switched to the other
    appearance while open."""
    from PySide6.QtCore import QPoint, Qt

    from app.ui import window as window_module

    screen = main_window.screen()
    available = screen.availableGeometry()
    print(f"Screen {screen.geometry().width()} x {screen.geometry().height()}, "
          f"available {available.width()} x {available.height()}, scale {screen.devicePixelRatio()}, "
          f"platform {app.platformName()}, style {app.style().name()}, scheme {scheme}")
    main_window.resize(780, min(660, available.height() - 60))
    main_window.move(available.topLeft() + QPoint(20, 20))
    wait(app, 800)
    size = main_window.size()

    def shoot(name: str) -> None:
        area = main_window.stack.currentWidget()
        bar = area.verticalScrollBar()
        bar.setValue(0)
        settle(app, main_window)
        wait(app, 500)
        how = grab_window_live(app, main_window, out / f"{name}-{scheme}.png")
        # The rest of a pane taller than the window, a screen at a time.
        part = 2
        while bar.value() < bar.maximum():
            bar.setValue(min(bar.maximum(), bar.value() + area.viewport().height() - 80))
            settle(app, main_window)
            wait(app, 300)
            grab_window_live(app, main_window, out / f"{name}-{scheme}-{part}.png")
            part += 1
        bar.setValue(0)
        print(f"{name}-{scheme}: {how}, {part - 1} part(s)")

    for index, (key, _title) in enumerate(window_module.PAGES):
        main_window._show_page(index)
        if key == "calibrate":
            measuring_state(main_window.calibrate)
        shoot(f"{index + 1:02d}-{key}")
    calibrate = window_module.PAGES.index(("calibrate", "Calibrate"))
    main_window._show_page(calibrate)
    result_state(main_window.calibrate)
    shoot(f"{calibrate + 1:02d}-calibrate-result")
    main_window.calibrate.restart()

    # Pop-ups and menus, which only the screen shows.
    from app.core import Button

    main_window.show_page("filter")
    settle(app, main_window)
    filter_page = main_window.filter_page
    left = getattr(filter_page, "window_boxes", {}).get(Button.LEFT)
    if left is not None and hasattr(left, "showPopup"):
        capture_popup(app, main_window, left.showPopup, out / f"popup-window-{scheme}.png")
    main_window.show_page("calibrate")
    settle(app, main_window)
    capture_popup(app, main_window, main_window.calibrate.button_picker.showPopup, out / f"popup-calibrate-{scheme}.png")
    main_window.show_page("apps")
    settle(app, main_window)
    capture_popup(app, main_window, main_window.apps.add_button.click, out / f"popup-add-app-{scheme}.png")

    # The narrowest the window can get, to check nothing is clipped.
    main_window.show_page("filter")
    main_window.resize(main_window.minimumSize())
    settle(app, main_window)
    wait(app, 800)
    grab_window_live(app, main_window, out / f"filter-minimum-{scheme}.png")
    main_window.resize(size)

    # The other appearance, switched to while the window is open (as the
    # system does at sunset), to compare with a window opened in it.
    other = "light" if scheme == "dark" else "dark"
    hints = app.styleHints()
    if hasattr(hints, "setColorScheme"):
        hints.setColorScheme(Qt.ColorScheme.Dark if other == "dark" else Qt.ColorScheme.Light)
        wait(app, 800)
        main_window.apply_look()  # what the app does on colorSchemeChanged (app/main.py)
        for key in ("filter", "calibrate"):
            main_window.show_page(key)
            settle(app, main_window)
            wait(app, 500)
            grab_window_live(app, main_window, out / f"switched-{key}-{scheme}-to-{other}.png")


def tray_sheet(out: Path) -> None:
    """The notification area / menu bar icon, both states, on dark and light,
    at the sizes the tray actually uses."""
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
    scheme = parse_scheme(sys.argv)
    if LIVE:
        # A capture that hangs (a menu nobody closed) ends the run instead of
        # holding a CI runner until it times out.
        import faulthandler

        faulthandler.dump_traceback_later(900, exit=True)

    from app import settings

    folder = Path(tempfile.mkdtemp())
    settings.config_dir = lambda: folder  # type: ignore[assignment]
    settings.LEGACY_PATH = folder / "none.json"

    if as_windows:
        from app.ui import theme

        theme.IS_MAC, theme.IS_WINDOWS = False, True

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from app.ui.theme import application_font

    app = QApplication([])
    body = application_font()
    if body is not None:
        app.setFont(body)  # as the app itself does (app/main.py)
    if scheme and hasattr(app.styleHints(), "setColorScheme"):
        app.styleHints().setColorScheme(Qt.ColorScheme.Dark if scheme == "dark" else Qt.ColorScheme.Light)
        wait(app, 300)
    if docs and not LIVE and not as_windows:
        # The README's Mac picture is dark, like the Windows one. Offscreen
        # there is no system appearance, so the native controls get a dark
        # palette by hand.
        app.setPalette(dark_palette())
    from app import permissions
    from app.controller import AppController
    from app.ui import widgets, window as window_module
    from app.ui.theme import current_look

    if as_windows:
        from app.ui import base, panes

        for module in (widgets, window_module, base, panes):
            module.IS_MAC = False
        permissions.needs_accessibility = lambda: False  # type: ignore[assignment]
        from app.ui import symbols

        symbols.IS_MAC, symbols.IS_WINDOWS = False, False

    if docs:
        # Shown as on, without installing a real mouse hook, and allowed, as
        # it is once set up (a CI Mac may not have granted Accessibility).
        AppController.active = property(lambda self: True)  # type: ignore[assignment]
        permissions.has_accessibility = lambda: True  # type: ignore[assignment]
        permissions.event_tap_allowed = lambda: True  # type: ignore[assignment]
    controller = AppController()
    if docs:
        docs_state(controller)
    else:
        sample_state(controller)
        controller.settings["filtered_total"] = 1284
        controller.session_filtered = 37
    updater = None if docs else sample_updater(controller)
    main_window = window_module.MainWindow(controller, updater)
    size = parse_size(sys.argv)
    main_window.resize(*((size[0] if size else DOCS_WIDTH, 700) if docs else (780, 660)))
    main_window.show()
    main_window.raise_()
    main_window.activateWindow()
    app.processEvents()
    if LIVE:
        wait(app, 1500)

    if docs:
        capture_docs(app, main_window, out / "docs-filter.png", size)
        print(f"Wrote {out / 'docs-filter.png'}")
        return

    test = main_window.test_page
    for gap in [410, 520, 9, 380, 460, 12, 590, 350, 470, 7, 520, 400, 610, 380, 11, 500, 430, 560]:
        test._on_pad_press(float(gap), float(gap + 60))
    test.pad.set_flash_level(0.0)

    if LIVE:
        capture_live(app, main_window, out, scheme or ("dark" if widgets.look().dark else "light"))
        tray_sheet(out)
        print(f"Wrote screenshots to {out}")
        return

    suffix = "-windows" if as_windows else ""
    for dark in [False, True]:
        widgets.set_look(current_look(dark))
        dark = widgets.look().dark
        for label in main_window.findChildren(widgets.TextLabel):
            label.restyle()
        for index, (key, _title) in enumerate(window_module.PAGES):
            main_window._show_page(index)
            if key == "calibrate":
                measuring_state(main_window.calibrate)
            settle(app, main_window)
            name = f"{key}-{'dark' if dark else 'light'}{suffix}.png"
            main_window.grab().save(str(out / name))
            # The whole pane, however long, for review.
            size = main_window.size()
            page = main_window.pages[index]
            main_window.resize(size.width(), max(size.height(), page.sizeHint().height() + 40))
            app.processEvents()
            main_window.grab().save(str(out / name.replace(".png", "-full.png")))
            main_window.resize(size)
            app.processEvents()
    tray_sheet(out)
    print(f"Wrote screenshots to {out}")


if __name__ == "__main__":
    main()
