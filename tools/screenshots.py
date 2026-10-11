"""Picture every surface of the app, in light and dark, for review and the docs.

    python tools/screenshots.py OUTPUT_DIR [--as-windows | --live [--scheme=light|dark]] [--docs | --social]

The surfaces, by platform:

    macOS    the Settings window's panes (General, Exceptions, History,
             About), General with Custom strength and with a note about
             another mouse utility, the menu bar menu (filtering and paused),
             the Dock menu, Test and Tune at each step, the first run's three
             states, and the pop-up buttons and menus the panes open.
    Windows  the window's pages (Home with its first-run InfoBar, then
             without; Exceptions, History, Settings), Home with the Strength
             expander open, the tray's flyout and its menu, Test and tune at
             each step, and the pop-ups.

Offscreen (the default) every window is drawn with `grab()`: Qt draws no
native control there (buttons and pop-ups come out in Fusion), menus can't
be opened, and the Mac's toolbar is Qt's stand-in, so use it for layout only.
`--as-windows` previews the Windows layout on another platform the same way.

`--live` opens real windows on the real window server and photographs the
screen, so native controls, the toolbar, materials (Mica) and title bars show
as users see them, and the menus open. `--scheme` asks for the light or dark
appearance. CI's `screenshots` job runs it on macOS and Windows, in both (the
macos-screenshots and windows-screenshots artifacts). It opens windows, a
menu bar item and menus, so run it only where that is welcome: a CI runner,
never someone's own Mac. Settings live in a temporary folder throughout.

`--docs` captures only the General / Home pane for the README, in the same
state on both platforms, so the two pictures match side by side; `--size`
sets the window's size. `--social` draws docs/images/social-preview.png,
the card GitHub shows for links to the repository.

Names: `NN-surface-SCHEME.png`, `-2.png` and on for the rest of a pane
taller than its window.
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
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
    from PySide6.QtCore import QPointF, QRectF
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


def parse_size(argv) -> tuple[int, int] | None:
    for argument in argv:
        if argument.startswith("--size="):
            width, _, height = argument[len("--size="):].partition("x")
            return int(width), int(height)
    return None


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
    buttons."""
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


# -- the state shown --------------------------------------------------------------

def sample_state(controller) -> None:
    """Something to show everywhere: a few buttons filtered (the left one
    calibrated), the wheel fix, excluded apps, the devices a computer usually
    has, and a month of wear on a switch that is getting worse."""
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
    # The Apps menu offers these as running, whatever this computer runs.
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
    controller.settings["filtered_total"] = 1284
    controller.session_filtered = 14


def docs_state(controller) -> None:
    """The General / Home pane as the README shows it: on, three buttons
    filtered (the left one calibrated), the wheel fix on."""
    from app.core import Button

    controller.set_buttons([Button.LEFT, Button.RIGHT, Button.BACK])
    controller.set_threshold(Button.LEFT, 46)
    controller.set_calibrated(Button.LEFT)
    controller.set_wheel_fix(True)
    controller.settings["filtered_total"] = 8324
    controller.session_filtered = 14


@contextlib.contextmanager
def suggesting(controller):
    """The health check as History shows it when bounce creeps up on the
    left button's window: a verdict, and the change it suggests. The
    controller's own check, in its own words, on the left button's recent
    bounces reaching the high 50s of its 59 ms window, with a 200 ms
    double-click measured by Test and Tune (the wear history and that
    measurement are what change). The stand-in, until the controller has
    one, is worded the same way: the detail carries the suggestion's reason,
    question and all."""
    from unittest import mock

    from _contract_standin import Health, Suggestion, standing_in

    from app.core import Button
    from app.inputs import as_button
    from app.wear import Histogram

    if standing_in():
        reason = "Recent bounces on the left button reached about 58 ms, close to its 59 ms window. Raise it to 83 ms?"
        health = Health(
            "Your left button double-clicks on its own: caught 412 of 412",
            "Over the last 30 days the filter ignored every extra click. The left button is bouncing more often "
            f"than two weeks ago: the switch is wearing out, and in time it will need replacing. {reason}",
            Suggestion(Button.LEFT, 59, 83, reason),
            "bouncing",
        )
        with mock.patch.object(controller, "health", return_value=health):
            yield
        return

    histogram = controller.wear.histogram

    def crowded(button, *args, **kwargs):
        found = histogram(button, *args, **kwargs)
        if as_button(button) is not Button.LEFT:
            return found
        bins = [(low, count + (12 if 50 <= low <= 56 else 0)) for low, count in found.bins]
        return Histogram(bins, found.overflow, found.window_ms)

    speeds = {**controller.double_click_speeds(), Button.LEFT: 200}
    with mock.patch.object(controller.wear, "histogram", crowded), \
            mock.patch.object(controller, "double_click_speeds", return_value=speeds):
        yield


def with_conflict(controller):
    """Logi Options+ running beside the app."""
    from unittest import mock

    from _contract_standin import Conflict

    return mock.patch.object(controller, "conflicts", return_value=[Conflict("Logi Options+", ["side buttons", "wheel"])])


# -- capturing -------------------------------------------------------------------

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
    if sys.platform == "darwin" and _on_real_screen():
        # QScreen can't see other apps' windows (the menu bar, the Dock).
        region = f"-R{rect.x()},{rect.y()},{rect.width()},{rect.height()}"
        if _screencapture([region], target):
            return "screencapture -R"
    pixmap = screen.grabWindow(0, rect.x(), rect.y(), rect.width(), rect.height())
    if not pixmap.isNull():
        pixmap.save(str(target))
        return "QScreen" if not _uniform(pixmap.toImage()) else "QScreen (uniform)"
    return "nothing"


def grab_window_live(app, window, target: Path) -> str:
    """The real window as the screen shows it: title bar, toolbar, materials
    and native controls as the window server composites them."""
    if sys.platform == "darwin" and _on_real_screen():
        number = _mac_window_number(window)
        if number is not None and _screencapture(["-o", f"-l{number}"], target):
            return "screencapture -l"
    return grab_screen(app, window.frameGeometry(), target)


def _post_escape(system: bool = False) -> None:
    """Close a native menu that is tracking with one Escape: into the app's
    own queue (no permission needed), or with `system`, the system's (for a
    menu the first didn't close).

    Only while a menu is still open: a stray Escape goes to the window in
    front instead, and closes Test and Tune, a dialog."""
    if sys.platform != "darwin" or not _on_real_screen():
        return
    if not system:
        try:
            import AppKit

            for kind in (AppKit.NSEventTypeKeyDown, AppKit.NSEventTypeKeyUp):
                event = AppKit.NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(  # noqa: E501
                    kind, (0, 0), 0, 0, 0, None, "\x1b", "\x1b", False, 53
                )
                AppKit.NSApp.postEvent_atStart_(event, True)
            return
        except Exception as error:  # noqa: BLE001
            print(f"Couldn't queue Escape: {error}")
    try:
        import Quartz

        for down in (True, False):
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, Quartz.CGEventCreateKeyboardEvent(None, 53, down))
    except Exception as error:  # noqa: BLE001
        print(f"Couldn't post Escape: {error}")


class Popups:
    """Open a menu or pop-up, photograph it, close it. A native menu on
    macOS runs its own loop until it closes, so the picture is taken from a
    timer, which Qt's event loop keeps running while the menu tracks."""

    def __init__(self, app) -> None:
        self.app = app
        self.menus: list = []  # NSMenus opened, for cancelTracking

    def close_all(self, tracking: bool, system: bool = False) -> None:
        """Close what is open. `tracking`: a native menu is still running
        its loop (the call that opened it hasn't returned). One that
        cancelTracking can't reach gets an Escape, sent only then; with
        `system`, a later try, it gets one even after cancelTracking."""
        from PySide6.QtWidgets import QApplication

        popup = QApplication.activePopupWidget()
        if popup is not None:
            popup.close()
        cancelled = False
        for menu in self.menus:
            try:
                menu.cancelTracking()
                cancelled = True
            except Exception:  # noqa: BLE001
                pass
        if tracking and (system or not cancelled):
            _post_escape(system)

    def capture(self, open_popup, area, target: Path) -> None:
        """`area()` gives the screen rectangle to photograph once open."""
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication

        returned: list = []

        def photograph() -> None:
            how = grab_screen(self.app, area(), target)
            print(f"{target.name}: {how}")
            self.close_all(tracking=not returned)

        def still_open() -> None:
            if not returned or QApplication.activePopupWidget() is not None:
                print(f"{target.name}: closing it again")
                self.close_all(tracking=not returned, system=True)

        timers = []
        for delay, run in ((1400, photograph), (4000, still_open), (7000, still_open)):
            timer = QTimer()
            timer.setSingleShot(True)
            timer.timeout.connect(run)
            timer.start(delay)
            timers.append(timer)
        open_popup()
        returned.append(True)
        wait(self.app, 1800)
        for timer in timers:
            timer.stop()
        self.menus.clear()


def settle(app, *windows) -> None:
    """Stop animations (offscreen they never advance) and clear the pads'
    flashes, so a picture shows the state, not a moment of a transition."""
    from PySide6.QtCore import QPropertyAnimation

    from app.ui.widgets import ClickPad, Switch

    app.processEvents()
    for window in windows:
        if window is None:
            continue
        for animation in window.findChildren(QPropertyAnimation):
            animation.stop()
        for pad in window.findChildren(ClickPad):
            pad.set_flash_level(0.0)
        for switch in window.findChildren(Switch):
            switch.set_position(1.0 if switch.isChecked() else 0.0)
    app.processEvents()


class Camera:
    """Takes the pictures: from the screen (`--live`) or by drawing each
    window (offscreen)."""

    def __init__(self, app, out: Path, scheme: str, suffix: str = "") -> None:
        self.app = app
        self.out = out
        self.scheme = scheme
        self.suffix = suffix
        self.popups = Popups(app)
        self.taken: list[str] = []

    def path(self, name: str, part: int = 1) -> Path:
        extra = f"-{part}" if part > 1 else ""
        return self.out / f"{name}-{self.scheme}{self.suffix}{extra}.png"

    def window(self, name: str, window, scroll_area=None) -> None:
        """A whole window; with `scroll_area`, the rest of a pane taller
        than the window as further parts, a screen at a time."""
        if not window.isVisible():
            print(f"{name}: the window isn't on screen")
        if LIVE:
            # In front and active, as the user has it when looking at it.
            window.raise_()
            window.activateWindow()
        settle(self.app, window)
        if LIVE:
            wait(self.app, 500)
        self._one(window, self.path(name))
        parts = 1
        if scroll_area is not None:
            bar = scroll_area.verticalScrollBar()
            bar.setValue(0)
            while bar.value() < bar.maximum():
                bar.setValue(min(bar.maximum(), bar.value() + scroll_area.viewport().height() - 80))
                parts += 1
                settle(self.app, window)
                if LIVE:
                    wait(self.app, 300)
                self._one(window, self.path(name, parts))
            bar.setValue(0)
        self.taken.append(name)
        print(f"{name}-{self.scheme}: {parts} part(s)")

    def _one(self, window, target: Path) -> None:
        if LIVE:
            grab_window_live(self.app, window, target)
        else:
            window.grab().save(str(target))

    def popup(self, name: str, open_popup, area) -> None:
        if not LIVE or not _on_real_screen():
            return  # nothing to photograph off the screen
        self.popups.capture(open_popup, area, self.path(name))
        self.taken.append(name)


def around(widget, margins=(40, 40, 40, 360)):
    """The screen rectangle around `widget`'s window, room below for a
    pop-up."""
    from PySide6.QtCore import QMargins

    left, top, right, bottom = margins
    return lambda: widget.window().frameGeometry().marginsAdded(QMargins(left, top, right, bottom))


# -- the surfaces ----------------------------------------------------------------

def window_module_is_mac() -> bool:
    from app.ui import window as window_module

    return window_module.IS_MAC


def place(app, window, x: int = 20, y: int = 20) -> None:
    screen = app.primaryScreen().availableGeometry()
    window.move(screen.x() + x, screen.y() + y)


def show(app, window) -> None:
    window.show()
    window.raise_()
    window.activateWindow()
    app.processEvents()
    if LIVE:
        wait(app, 700)


def capture_settings(app, camera: Camera, main_window, controller) -> None:
    """Every pane of the window, then the states worth a look."""
    from app.ui import menus
    from app.ui import window as window_module

    mac = window_module.IS_MAC
    if not mac:
        # The first run's InfoBar shows until it is closed once.
        controller.settings["tray_hint_shown"] = False
        main_window.refresh()
    place(app, main_window)
    show(app, main_window)
    if mac:
        # What each pane needs: it fits, unscrolled, on a screen whose
        # available height is this plus SETTINGS_SCREEN_ROOM.
        heights = {key: main_window.pane_height(index) for index, (key, _title) in enumerate(window_module.pages())}
        print(f"Pane heights {heights}, plus {window_module.SETTINGS_SCREEN_ROOM} for the window's chrome")
    for index, (key, _title) in enumerate(window_module.pages()):
        main_window._show_page(index)
        app.processEvents()
        area = main_window.stack.currentWidget()
        name = f"{index + 1:02d}-{key}"
        if key == "home":
            name += "-first-run"  # the InfoBar shows until it is closed once
        camera.window(name, main_window, area)
    if not mac and main_window.home.info_bar is not None:
        main_window.home.info_bar.close_button.click()
        main_window._show_page(0)
        camera.window("01-home", main_window, main_window.stack.currentWidget())

    home = main_window.home
    main_window.show_page("general")
    controller.set_strength(menus.CUSTOM)  # Custom shows each button's window
    if home.strength_expander is not None:
        home.strength_expander.set_expanded(True)
    app.processEvents()
    area = main_window.stack.currentWidget()
    if mac:
        # The window grows with the pane; what the screen can't show scrolls.
        camera.window("05-general-custom", main_window, area)
    else:
        area.ensureWidgetVisible(home.strength_expander, 0, 0)
        camera.window("05-home-strength", main_window)
    camera.popup(
        "popup-strength", home.strength_picker.showPopup, around(home.strength_picker)
    )
    from app.core import Button

    left = home.window_boxes[Button.LEFT]
    main_window.stack.currentWidget().ensureWidgetVisible(left)
    camera.popup("popup-window", left.showPopup, around(left))
    controller.set_strength(menus.AUTOMATIC)
    if home.strength_expander is not None:
        home.strength_expander.set_expanded(False)
    main_window.stack.currentWidget().verticalScrollBar().setValue(0)

    with with_conflict(controller):
        controller.conflicts_changed.emit()
        main_window._show_page(0)
        camera.window(f"06-{main_window.current_key}-note", main_window)
    controller.conflicts_changed.emit()

    with suggesting(controller):
        main_window.show_page("history")
        camera.window("07-history-suggestion", main_window)

    main_window.show_page("exceptions")
    apps = main_window.apps
    main_window.stack.currentWidget().ensureWidgetVisible(apps.add_button)
    capture_native_menu_of(camera, "popup-add-app", apps.show_add_menu, around(apps.add_button))
    main_window.show_page("general")


def capture_native_menu_of(camera: Camera, name: str, open_menu, area) -> None:
    """A button's menu: AppKit's on macOS (popup_mac, whose NSMenu is kept
    so it can be closed), Qt's elsewhere."""
    from unittest import mock

    from app.ui import popup_mac

    build = popup_mac.build

    def keep(entries):
        menu, target = build(entries)
        camera.popups.menus.append(menu)
        return menu, target

    with mock.patch.object(popup_mac, "build", keep):
        camera.popup(name, open_menu, area)


def capture_tune(app, camera: Camera, main_window, controller) -> None:
    """Test and Tune, each step: the pad before and during the test, the
    verdict, the tune step, and done."""
    tune = main_window.tune
    tune.restart()
    main_window.show_tune()
    # Beside the Settings window on a Mac; over the window on Windows, whose
    # pictures come from the screen.
    place(app, tune, 680 if window_module_is_mac() else 60, 20 if window_module_is_mac() else 30)
    app.processEvents()
    if LIVE:
        wait(app, 700)
    camera.window("10-tune-1-pad", tune)
    tune._on_pad_press(None, None)
    for _ in range(4):
        tune._on_pad_press(900.0, 960.0)
    tune._on_pad_press(11.0, 40.0)  # a bounce, caught
    camera.window("11-tune-1-pad-measuring", tune)
    while tune.phase == "single":
        tune._on_pad_press(900.0, 960.0)
    while tune.phase == "double":
        tune._on_pad_press(900.0, 960.0)
        tune._on_pad_press(150.0, 210.0)
    camera.window("12-tune-2-verdict", tune)
    with with_conflict(controller):
        tune._render()
        camera.window("12-tune-2-verdict-note", tune)
    tune._render()
    tune._primary()  # Continue
    camera.window("13-tune-3-tune", tune)
    camera.popup("popup-tune-window", tune.window_picker.showPopup, around(tune.window_picker))
    tune._primary()  # Apply
    camera.window("14-tune-4-done", tune)
    tune.close()
    app.processEvents()


def capture_first_run(app, camera: Camera, controller) -> None:
    """macOS's first run: before the system's request, waiting for the
    switch in System Settings, and ready. Nothing here asks macOS for
    anything: the states are set, not reached."""
    from app.ui import first_run

    window = first_run.FirstRunWindow(controller)
    place(app, window, 200, 120)
    show(app, window)
    for index, state in enumerate((first_run.ASK, first_run.WAITING, first_run.READY)):
        window.state = state
        window._render()
        app.processEvents()
        camera.window(f"20-first-run-{index + 1}-{state}", window)
    window.hide()
    window.deleteLater()


def capture_mac_menus(app, camera: Camera, controller, main_window) -> None:
    """The menu bar item's menu, filtering and paused, and the Dock menu."""
    from PySide6.QtCore import QRect

    from app.ui import menu_bar_mac

    if not (LIVE and _on_real_screen()):
        return
    item = menu_bar_mac.MacMenuBarItem(
        controller, on_settings=lambda: None, on_tune=lambda: None, on_quit=lambda: None,
        on_toggle=main_window.request_filter,
    )
    item.show()
    wait(app, 1500)
    screen = app.primaryScreen().geometry()

    def below_item():
        try:
            frame = item._item.button().window().frame()
            x = int(frame.origin.x)
        except Exception:  # noqa: BLE001
            x = screen.width() - 300
        left = max(0, x - 340)
        return QRect(left, 0, min(screen.width() - left, 720), 420)

    def open_item_menu():
        camera.popups.menus.append(item.menu)
        item.open_menu()

    camera.popup("30-menu-bar", open_item_menu, below_item)
    controller.pause(15)
    camera.popup("31-menu-bar-paused", open_item_menu, below_item)
    controller.resume()
    app.processEvents()

    # The Dock menu as macOS asks Qt for it (applicationDockMenu:), opened
    # where the Dock is. The Dock adds its own items under these.
    dock = menu_bar_mac.DockMenu(controller, on_settings=lambda: None, on_tune=lambda: None,
                                 on_toggle=main_window.request_filter)
    app.processEvents()

    def open_dock_menu():
        import AppKit

        native = None
        delegate = AppKit.NSApp.delegate()
        if delegate is not None and delegate.respondsToSelector_(b"applicationDockMenu:"):
            native = delegate.applicationDockMenu_(AppKit.NSApp)
        if native is None:
            print("No Dock menu from Qt; showing the QMenu")
            dock.menu.popup(main_window.mapToGlobal(main_window.rect().center()))
            return
        camera.popups.menus.append(native)
        # Screen coordinates start at the bottom left in AppKit; the menu
        # hangs down from this point, above the Dock.
        native.popUpMenuPositioningItem_atLocation_inView_(None, (screen.width() // 2, 260), None)

    def above_dock():
        return QRect(screen.width() // 2 - 30, screen.height() - 300, 420, 300)

    camera.popup("32-dock-menu", open_dock_menu, above_dock)
    item.hide()


def capture_tray(app, camera: Camera, controller, main_window) -> None:
    """Windows' notification area icon: its flyout (left click) and its
    menu (right click)."""
    from PySide6.QtCore import QMargins, QPoint, QRect

    from app.ui import tray as tray_module

    tray = tray_module.Tray(controller, on_open=lambda: None, on_tune=lambda: None, on_quit=lambda: None,
                            on_toggle=main_window.request_filter)
    tray.show()
    app.processEvents()
    if LIVE:
        wait(app, 1200)
    print(f"Tray icon at {tray.geometry()}")
    flyout = tray.flyout

    def show_flyout() -> None:
        tray.show_flyout()
        app.processEvents()

    if LIVE and _on_real_screen():
        show_flyout()
        wait(app, 600)
        settle(app, flyout)
        grab_screen(app, flyout.frameGeometry().marginsAdded(QMargins(24, 24, 24, 24)), camera.path("30-tray-flyout"))
        camera.taken.append("30-tray-flyout")
        flyout.close()
        controller.pause(15)
        show_flyout()
        wait(app, 600)
        settle(app, flyout)
        grab_screen(app, flyout.frameGeometry().marginsAdded(QMargins(24, 24, 24, 24)),
                    camera.path("31-tray-flyout-paused"))
        camera.taken.append("31-tray-flyout-paused")
        flyout.close()
        controller.resume()
    else:
        flyout.refresh()
        flyout.adjustSize()
        settle(app, flyout)
        flyout.grab().save(str(camera.path("30-tray-flyout")))
        camera.taken.append("30-tray-flyout")

    screen = app.primaryScreen().availableGeometry()
    at = QPoint(screen.right() - 260, screen.bottom() - 220)
    menu = tray.contextMenu()

    def open_menu() -> None:
        tray._before_menu()
        menu.popup(at)

    camera.popup("32-tray-menu", open_menu, lambda: QRect(at.x() - 30, at.y() - 30, 330, 260))
    tray.hide()


def capture_switched(app, camera: Camera, main_window, scheme: str) -> None:
    """The other appearance, switched to while the window is open (as the
    system does at sunset), to compare with a window opened in it."""
    from PySide6.QtCore import Qt

    other = "light" if scheme == "dark" else "dark"
    hints = app.styleHints()
    if not LIVE or not hasattr(hints, "setColorScheme"):
        return
    hints.setColorScheme(Qt.ColorScheme.Dark if other == "dark" else Qt.ColorScheme.Light)
    wait(app, 800)
    main_window.apply_look()  # what the app does on colorSchemeChanged (app/main.py)
    main_window.show_page("general")
    show(app, main_window)
    settle(app, main_window)
    grab_window_live(app, main_window, camera.out / f"switched-general-{scheme}-to-{other}.png")


def capture_docs(app, main_window, target: Path, size: tuple[int, int] | None) -> None:
    """The General / Home pane into `target`, nothing scrolled or cut
    through: a pane the window can't show whole ends below the last section
    that fits."""
    from PySide6.QtCore import QPoint, Qt

    main_window.show_page("general")
    main_window.refresh()
    area = main_window.stack.currentWidget()
    area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    if main_window.home.info_bar is not None:
        main_window.home.info_bar.hide()
        main_window.home.info_gap.hide()
    app.processEvents()
    width = size[0] if size else (620 if sys.platform == "darwin" else DOCS_WIDTH)
    if size:
        main_window.setFixedSize(size[0], max(260, size[1] - (0 if sys.platform == "darwin" else WINDOWS_TITLE_BAR)))
    elif sys.platform != "darwin":
        main_window.resize(width, main_window.height())
    place(app, main_window, 0, 0)
    show(app, main_window)
    settle(app, main_window)
    # Hide what the window shows only part of.
    page, viewport = area.widget(), area.viewport()
    items = [page.body.itemAt(index).widget() for index in range(page.body.count())]
    items = [item for item in items if item is not None and item.isVisible()]
    for position, item in enumerate(items):
        if item.mapTo(viewport, QPoint(0, item.height())).y() + 14 <= viewport.height():
            continue
        cut = position
        while cut > 0 and getattr(items[cut - 1], "role", "") == "headline":
            cut -= 1
        for hidden in items[cut:]:
            hidden.hide()
        break
    settle(app, main_window)
    if LIVE:
        wait(app, 600)
        print(f"docs: {grab_window_live(app, main_window, target)}")
    else:
        grabbed = main_window.grab().toImage()
        mac_frame(grabbed, grabbed.width(), grabbed.height()).save(str(target))


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

        faulthandler.dump_traceback_later(1200, exit=True)

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
    app.setQuitOnLastWindowClosed(False)
    body = application_font()
    if body is not None:
        app.setFont(body)  # as the app itself does (app/main.py)
    if scheme and hasattr(app.styleHints(), "setColorScheme"):
        app.styleHints().setColorScheme(Qt.ColorScheme.Dark if scheme == "dark" else Qt.ColorScheme.Light)
        wait(app, 300)
    if docs and not LIVE and not as_windows and sys.platform == "darwin":
        # Offscreen there is no system appearance: give the native controls
        # a dark palette by hand, as the README's pictures are dark.
        app.setPalette(dark_palette())
    from app import permissions
    from app.ui import widgets, window as window_module
    from app.ui.theme import current_look

    if as_windows:
        from app.ui import base, menus, panes, symbols, tray, tune

        for module in (widgets, window_module, base, panes, menus, tray, tune):
            module.IS_MAC = False
        permissions.needs_accessibility = lambda: False  # type: ignore[assignment]
        symbols.IS_MAC, symbols.IS_WINDOWS = False, False

    from _contract_standin import controller_class

    controller_type = controller_class()
    # Shown as on, without a real mouse hook, and allowed, as it is once set
    # up (a CI Mac hasn't granted the permission). Off where a state says so.
    shown_on = [True]
    controller_type.active = property(lambda self: shown_on[0] and not self.paused)  # type: ignore[assignment]
    permissions.has_accessibility = lambda: True  # type: ignore[assignment]
    permissions.event_tap_allowed = lambda: True  # type: ignore[assignment]
    controller = controller_type()
    controller.settings["fix_enabled"] = True
    if docs:
        docs_state(controller)
    else:
        sample_state(controller)
    updater = None if docs else sample_updater(controller)
    main_window = window_module.MainWindow(controller, updater)
    if not window_module.IS_MAC:
        screen = app.primaryScreen().availableGeometry()
        main_window.resize(min(920, screen.width() - 40), min(720, screen.height() - 60))
    scheme = scheme or ("dark" if widgets.look().dark else "light")
    if LIVE:
        screen = app.primaryScreen()
        print(f"Screen {screen.geometry().width()} x {screen.geometry().height()}, available "
              f"{screen.availableGeometry().width()} x {screen.availableGeometry().height()}, scale "
              f"{screen.devicePixelRatio()}, platform {app.platformName()}, style {app.style().name()}, scheme {scheme}")

    if docs:
        capture_docs(app, main_window, out / "docs-filter.png", parse_size(sys.argv))
        print(f"Wrote {out / 'docs-filter.png'}")
        return

    passes = [(scheme, None)] if LIVE else [("light", False), ("dark", True)]
    for name, dark in passes:
        if dark is not None:
            # Offscreen there is no system appearance to follow: the window
            # takes the one asked for.
            window_module.current_look = lambda _dark=None, chosen=dark: current_look(chosen)
            main_window.apply_look()
        camera = Camera(app, out, name, "-windows" if as_windows else "")
        capture_settings(app, camera, main_window, controller)
        capture_tune(app, camera, main_window, controller)
        if window_module.IS_MAC:
            capture_first_run(app, camera, controller)
            capture_mac_menus(app, camera, controller, main_window)
        else:
            capture_tray(app, camera, controller, main_window)
        if LIVE:
            capture_switched(app, camera, main_window, name)
        print(f"{name}: {', '.join(camera.taken)}")
    tray_sheet(out)
    print(f"Wrote screenshots to {out}")


def sample_updater(controller):
    """An updater that says the app is up to date, so About / Settings
    shows its rows as an installed copy does. It never starts, so it never
    goes online."""
    from app.updater import Updater

    updater = Updater(controller)
    updater.kind = "installed"
    updater.state = updater.CURRENT
    updater.message = "Up to date"
    return updater


if __name__ == "__main__":
    main()
