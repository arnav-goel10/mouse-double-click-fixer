"""The app's window, in each platform's own idiom.

macOS: a Settings window with a toolbar of panes (General, Exceptions,
History, About), titled with the pane shown, sized to it, with the zoom
button dimmed and the last pane restored; the menu bar menu is where the app
is used day to day.
Windows: one window with a left NavigationView (Home, Exceptions, History,
and Settings pinned at the bottom) on Mica, as Windows Settings is.

Test and Tune is a task, not a setting, so it has a window of its own
(tune.py); the macOS first run too (first_run.py). This window still owns
the filter's life: the permission poll, the retries at login, the health
check and the pause while Test and Tune measures.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QByteArray, QEvent, QPoint, QTimer, Qt, Signal
from PySide6.QtGui import QGuiApplication, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .. import DISPLAY_NAME, __version__, diagnostics, notices, permissions, startup
from .. import settings as settings_store
from ..controller import AppController
from ..core import MAX_THRESHOLD_MS, MIN_THRESHOLD_MS, Button, ClickEvent
from ..inputs import button_name
from . import icons, menus, native, ui_state
from .base import (
    COLUMN_MAX,
    COLUMN_MIN,
    Page,
    card_icon,
    controls,
    label,
    make_button,
    set_primary,
)
from .fluent import Expander, InfoBar, NavigationView
from .panes import ExceptionsPage, HistoryPage
from .theme import IS_MAC, current_look
from .toolbar_mac import NativeToolbar, PaneBar
from .tune import TuneWindow
from .widgets import (
    AppIconView,
    CheckRow,
    Row,
    Switch,
    SymbolView,
    TextLabel,
    look,
    pop_up_button,
    set_look,
)

log = logging.getLogger(__name__)

#: A background launch whose filter fails to start tries again this many
#: seconds later: at login the window server or the permission database can
#: answer a moment late.
RETRY_AT_S = (1, 3, 8, 20)
#: How often a running filter is checked for a tap that stopped working.
HEALTH_CHECK_MS = 5000

#: Room left around a first-launch window for its frame and title bar.
FRAME_ALLOWANCE = (16, 48)

#: The macOS Settings window's width; its height follows the pane.
SETTINGS_WIDTH = 620
#: Room the Settings window leaves for the menu bar, its title bar and
#: toolbar, and the Dock.
SETTINGS_SCREEN_ROOM = 150

DIAGNOSTICS_DETAIL = "Version, settings and the recent log, for a bug report. Never your clicks."
ACKNOWLEDGEMENTS_DETAIL = "Qt, Python and the other software it is built on, with their licences."


def privacy_line() -> str:
    """What the app sends over the network, in a sentence."""
    computer = "Mac" if IS_MAC else "PC"
    return (
        f"{DISPLAY_NAME} goes online only to check GitHub for updates. "
        f"Your clicks never leave this {computer}, and there is no account or tracking."
    )


def mac_pages() -> list[tuple[str, str]]:
    return [("general", "General"), ("exceptions", "Exceptions"), ("history", "History"), ("about", "About")]


def windows_pages() -> list[tuple[str, str]]:
    return [("home", "Home"), ("exceptions", "Exceptions"), ("history", "History"), ("settings", "Settings")]


def pages() -> list[tuple[str, str]]:
    """The panes, in order, for this platform: (key, title)."""
    return mac_pages() if IS_MAC else windows_pages()


#: Either platform's name for the same pane, so callers can name one.
PAGE_ALIASES = {"general": "home", "home": "general", "about": "settings", "settings": "about"}

#: What ui_state keeps the last Settings pane under (macOS).
LAST_PANE = "settings_pane"


def updates_page_key() -> str:
    """The pane with Software Update."""
    return "about" if IS_MAC else "settings"


# -- shared controls ----------------------------------------------------------------

WHEEL_DETAIL = "Drops a notch that jumps the wrong way. Smooth scrolling from trackpads and precision touchpads is left alone."
AUTOMATIC_NOTE = "Automatic uses what Test and Tune measured for each button, or a setting that suits most mice."
CUSTOM_NOTE = (
    "Each number is how soon after letting go a second click is ignored. Back and forward are never "
    "held back: a repeat that soon is dropped."
)
#: The Strength card's one line on Windows; the notes above sit inside it.
AUTOMATIC_SHORT = "Set by Test and tune, or what suits most mice."
CUSTOM_SHORT = "Your own window for each button."

#: The windows a button's pop-up offers, in milliseconds: the default (46)
#: and enough steps across the allowed range to choose by. A window outside
#: these (from calibration, or an older version's spin box) is listed too.
BUTTON_WINDOWS = (10, 15, 20, 25, 30, 35, 40, 46, 50, 55, 60, 70, 80, 90, 100, 120, 150, 200)
#: The same for the wheel's reversal window.
WHEEL_WINDOWS = (10, 20, 30, 40, 50, 60, 70, 80, 100, 120, 150)


class WindowPicker(QComboBox):
    """A window in milliseconds, chosen from a pop-up of values: a native
    pop-up button on macOS and a ComboBox on Windows 11. `valueChanged`
    carries every change, the user's and the code's, as a spin box's did."""

    valueChanged = Signal(int)

    def __init__(self, presets: tuple[int, ...], low: int, high: int, accessible_name: str) -> None:
        super().__init__()
        self._low, self._high = low, high
        self._presets = tuple(value for value in presets if low <= value <= high)
        self._value = self._presets[0]
        self.setAccessibleName(accessible_name)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._fill(self._presets)
        self.currentIndexChanged.connect(self._follow)
        # The pane scrolls, and these sit where the pointer passes.
        pop_up_button(self)

    def _fill(self, values) -> None:
        self.blockSignals(True)
        try:
            current = self._value
            self.clear()
            for value in values:
                self.addItem(f"{value} ms", value)
            self.setCurrentIndex(max(0, self.findData(current)))
        finally:
            self.blockSignals(False)

    def values(self) -> list[int]:
        return [int(self.itemData(index)) for index in range(self.count())]

    def value(self) -> int:
        return self._value

    def setValue(self, value: int) -> None:  # noqa: N802 - Qt naming, as QSpinBox
        """Show `value`, listing it in order among the presets if it isn't
        one: a calibrated 59 ms reads "59 ms", not the nearest preset."""
        value = max(self._low, min(self._high, int(value)))
        if self.findData(value) < 0:
            self._fill(sorted({*self._presets, value}))
        self.blockSignals(True)
        try:
            self.setCurrentIndex(self.findData(value))
        finally:
            self.blockSignals(False)
        self._follow()

    def _follow(self, _index: int = -1) -> None:
        data = self.currentData()
        if data is None or int(data) == self._value:
            return
        self._value = int(data)
        self.valueChanged.emit(self._value)


def window_box(low: int, high: int, accessible_name: str, presets: tuple[int, ...] = BUTTON_WINDOWS) -> WindowPicker:
    """A number of milliseconds, as a native pop-up of values."""
    return WindowPicker(presets, low, high, accessible_name)


def set_checked(control: QWidget, value: bool) -> None:
    """Show a state from code, without an animation or a `clicked`."""
    if isinstance(control, Switch):
        control.setChecked(bool(value), animate=False)
    else:
        control.setChecked(bool(value))


class LoginControl:
    """Open at login (macOS: a checkbox) or Start with Windows (a toggle),
    which the system can also switch off behind the app's back."""

    def __init__(self, controller: AppController, owner: QWidget) -> None:
        self.controller = controller
        self.owner = owner
        self.mac = IS_MAC
        # Shown when the user switched the app off in System Settings › Login
        # Items: only there can it be switched back on.
        self.items_button = make_button("Open Login Items…")
        self.items_button.clicked.connect(lambda: startup.open_login_items_settings())
        self.items_button.setVisible(False)
        if IS_MAC:
            self.row = CheckRow("Open at login", "Open at login", trailing=self.items_button)
            self.toggle = self.row.check
        else:
            self.toggle = Switch(accessible_name="Start with Windows")
            self.row = Row("Start with Windows", self.detail(startup.ON), controls(self.items_button, self.toggle),
                           card_icon("power"))
        self.toggle.clicked.connect(self._on_login)

    @staticmethod
    def detail(state: str) -> str:
        if state == startup.BLOCKED:
            if IS_MAC:
                return "Turned off in System Settings. Turn it on again in Login Items."
            return "Turned off in Task Manager › Startup apps. Turn it on here to allow it again."
        return "" if IS_MAC else "Starts in the notification area when you sign in."

    def refresh(self) -> None:
        set_checked(self.toggle, bool(self.controller.settings["start_at_login"]))
        state = self.controller.login_item_state
        self.row.set_detail(self.detail(state))
        # On macOS only Login Items can switch it back on; the box here
        # would tick and untick again.
        blocked_by_system = self.mac and state == startup.BLOCKED
        self.items_button.setVisible(blocked_by_system)
        self.toggle.setEnabled(not blocked_by_system)

    def _on_login(self, checked: bool) -> None:
        error = self.controller.set_start_at_login(checked)
        if error:
            set_checked(self.toggle, not checked)
            QMessageBox.warning(self.owner, "Couldn’t change the login item", error)
            return
        # Still off if macOS keeps it switched off in Login Items; say so.
        self.refresh()


class PauseControls:
    """The pause shortcut's switch. (Pausing during full-screen games is an
    exception, on the Exceptions pane.)"""

    def __init__(self, controller: AppController) -> None:
        self.controller = controller
        shortcut = controller.pause_shortcut_text
        self.shortcut_toggle = Switch(accessible_name="Pause shortcut")
        self.shortcut_row = Row(
            "Pause shortcut",
            f"{shortcut} pauses filtering for {menus.PAUSE_MINUTES} minutes, and resumes it.",
            self.shortcut_toggle,
            card_icon("keyboard"),
        )
        # A system with no shortcut of its own has nothing to switch.
        self.shortcut_row.setVisible(bool(shortcut))
        self.shortcut_toggle.clicked.connect(lambda checked: controller.set_pause_shortcut_enabled(bool(checked)))

    @property
    def rows(self) -> list[QWidget]:
        return [self.shortcut_row]

    def refresh(self) -> None:
        set_checked(self.shortcut_toggle, bool(self.controller.settings.get("pause_shortcut_enabled", True)))


# -- panes -----------------------------------------------------------------------

class HomePage(Page):
    """General (macOS) / Home (Windows): the filter's switch and what it is
    doing, the buttons it covers, its strength, Test and Tune, and the wheel
    fix. On macOS also the pause shortcut, Open at login and the menu bar
    icon, which Windows keeps on its Settings page."""

    #: Test and Tune, for a button or for any (None).
    tune_requested = Signal(object)
    #: macOS: the "Show in menu bar" switch.
    menu_bar_toggled = Signal(bool)

    def __init__(self, controller: AppController, parent: Optional[QWidget] = None) -> None:
        super().__init__("General" if IS_MAC else "Home", parent)
        self.controller = controller
        # The layout it was built in, which its refreshes follow.
        self.mac = IS_MAC
        self._loading = False
        self.info_bar: Optional[InfoBar] = None
        if not IS_MAC:
            self._first_run_bar()

        self.permission = self.section()
        self.permission_button = make_button("Open Settings…")
        self.permission_button.setAccessibleName(f"Open {permissions.pane_name()} settings")
        # The same path as the switch: once access is granted, the filter starts.
        self.permission_button.clicked.connect(lambda: self.window().request_filter(True))
        self.permission_row = self.permission.add(
            Row(
                "Permission required",
                f"Allow {DISPLAY_NAME} in Privacy & Security › {permissions.pane_name()}.",
                self.permission_button,
                SymbolView("warning", 22, "symbol"),
            )
        )
        self.permission_gap = QWidget()
        self.permission_gap.setFixedHeight(20 if IS_MAC else 4)
        self.body.addWidget(self.permission_gap)

        hero = self.section()
        self.switch = Switch(accessible_name="Bounce filter")
        self.status_row = hero.add(Row(label("Bounce Filter"), " ", self.switch, AppIconView(36 if IS_MAC else 32)))

        # Another mouse utility that may take clicks over first.
        self.conflict_gap = QWidget()
        self.conflict_gap.setFixedHeight(10 if IS_MAC else 4)
        self.body.addWidget(self.conflict_gap)
        self.conflicts = self.section()
        self._conflicts_shown: Optional[list] = None

        if not IS_MAC:
            # Windows: the task as a card with an accent button.
            self.gap(4)
            tune_card = self.section()
            self.test_button = make_button("Test and Tune", default=True)
            self.test_button.setAccessibleName("Test and tune your mouse")
            self.test_button.clicked.connect(lambda: self.tune_requested.emit(None))
            tune_card.add(
                Row("Test your mouse", "Find out whether it clicks twice on its own, and set the right strength.",
                    self.test_button, card_icon("tune"))
            )

        self._buttons_section()
        self._strength_section()

        self.header("Other fixes" if not IS_MAC else "Other Fixes")
        wheel = self.section()
        self.wheel_switch = Switch(accessible_name="Fix scroll-wheel reversals")
        self.wheel_switch.clicked.connect(self._on_wheel)
        self.wheel_row = wheel.add(Row("Fix wheel reversals", WHEEL_DETAIL, self.wheel_switch, card_icon("wheel")))

        self.pause: Optional[PauseControls] = None
        self.login: Optional[LoginControl] = None
        self.menu_bar_switch: Optional[Switch] = None
        if IS_MAC:
            # How the app fits in: its shortcut, login and the menu bar. One
            # section, so the pane fits a laptop's screen without scrolling.
            self.gap(20)
            launch = self.section()
            self.pause = PauseControls(controller)
            for row in self.pause.rows:
                launch.add(row)
            self.login = LoginControl(controller, self)
            launch.add(self.login.row)
            self.menu_bar_switch = Switch(accessible_name="Show in menu bar")
            self.menu_bar_switch.clicked.connect(lambda checked: self.menu_bar_toggled.emit(bool(checked)))
            self.menu_bar_row = launch.add(Row("Show in menu bar", "", self.menu_bar_switch))
            if permissions.macos_major() >= 26:
                self.menu_bar_note = self.footnote(
                    f"If the icon doesn’t appear, allow {DISPLAY_NAME} in System Settings › Menu Bar. "
                    "Opening the app again always opens Settings."
                )
            else:
                self.menu_bar_note = self.footnote("Opening the app again always opens Settings.")
        self.body.addStretch(1)

    # -- sections ---------------------------------------------------------------
    def _first_run_bar(self) -> None:
        """Windows, until closed once: where the icon lives, and a nudge to
        Test and tune."""
        self.info_bar = InfoBar(
            "Pin the icon to the taskbar corner so it’s always visible",
            "Select Show hidden icons (^) on the taskbar and drag the mouse icon next to the clock. "
            "Then test your mouse below to set the right strength.",
        )
        self.info_bar.closed.connect(self.controller.note_tray_hint_shown)
        self.body.addWidget(self.info_bar)
        self.info_gap = QWidget()
        self.info_gap.setFixedHeight(12)
        self.body.addWidget(self.info_gap)

    def _buttons_section(self) -> None:
        self.button_checks: dict[Button, QCheckBox] = {}
        self.button_rows: dict[Button, CheckRow] = {}
        if IS_MAC:
            self.header("Buttons")
            section = self.section()
            for button in Button:
                name = button_name(button)
                row = section.add(CheckRow(name, f"Filter the {name.lower()} button"))
                row.check.clicked.connect(self._on_buttons)
                self.button_checks[button] = row.check
                self.button_rows[button] = row
            return
        # Windows: one card, its checkboxes under its header.
        self.gap(4)
        section = self.section()
        card = QWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 8)
        layout.setSpacing(0)
        self.buttons_row = Row("Buttons", "Tick each button that clicks twice on its own.", None, card_icon("mouse"))
        layout.addWidget(self.buttons_row)
        for button in Button:
            name = button_name(button)
            row = CheckRow(name, f"Filter the {name.lower()} button")
            row.setContentsMargins(36, 0, 0, 0)
            row.setMinimumHeight(36)
            row.check.clicked.connect(self._on_buttons)
            layout.addWidget(row)
            self.button_checks[button] = row.check
            self.button_rows[button] = row
        section.add(card)

    def _strength_section(self) -> None:
        self.strength_picker = QComboBox()
        self.strength_picker.addItem("Automatic", menus.AUTOMATIC)
        self.strength_picker.addItem("Custom", menus.CUSTOM)
        self.strength_picker.setAccessibleName("Strength")
        pop_up_button(self.strength_picker)
        self.strength_picker.activated.connect(self._on_strength)
        self.window_boxes: dict[Button, WindowPicker] = {}
        self.window_rows: dict[Button, Row] = {}
        rows = []
        for button in Button:
            name = button_name(button)
            box = window_box(MIN_THRESHOLD_MS, MAX_THRESHOLD_MS, f"{name} button: ignore a second click within")
            box.valueChanged.connect(lambda value, which=button: self._on_window(which, value))
            row = Row(f"{name} button", "", box)
            self.window_boxes[button] = box
            self.window_rows[button] = row
            rows.append(row)
        self.wheel_box = window_box(
            settings_store.WHEEL_MIN_MS, settings_store.WHEEL_MAX_MS, "Scroll wheel: drop a reversal within", WHEEL_WINDOWS
        )
        self.wheel_box.valueChanged.connect(self._on_wheel_window)
        self.wheel_window_row = Row("Scroll wheel", "", self.wheel_box)
        rows.append(self.wheel_window_row)
        self.strength_expander: Optional[Expander] = None
        self.strength_note: Optional[TextLabel] = None
        if IS_MAC:
            self.gap(20)
            section = self.section()
            self.strength_row = section.add(Row("Strength", "", self.strength_picker))
            for row in rows:
                section.add(row)
            self.strength_note = self.footnote("")
            actions = QHBoxLayout()
            actions.setContentsMargins(0, 12, 0, 0)
            actions.addStretch(1)
            self.test_button = make_button("Test and Tune…")
            self.test_button.setAccessibleName("Test and tune your mouse")
            self.test_button.clicked.connect(lambda: self.tune_requested.emit(None))
            actions.addWidget(self.test_button)
            self.body.addLayout(actions)
            return
        self.gap(4)
        self.strength_expander = Expander(
            "Strength", AUTOMATIC_SHORT, self.strength_picker, card_icon("stopwatch"), "Strength for each button"
        )
        self.strength_row = self.strength_expander.header
        for row in rows:
            self.strength_expander.add(row)
        self.strength_note = TextLabel("", "caption", "secondary")
        self.strength_note.setWordWrap(True)
        self.strength_expander.add(self.strength_note)
        # Lined up with the rows' titles, under the last of them.
        self.strength_note.setContentsMargins(52, 10, 16, 14)
        self.body.addWidget(self.strength_expander)

    # -- state ------------------------------------------------------------------
    def refresh(self, granted: bool, waiting_for_permission: bool = False) -> None:
        self._loading = True
        try:
            self._refresh(granted, waiting_for_permission)
        finally:
            self._loading = False

    def status_sentence(self) -> str:
        """The hero row's sentence: the controller's, with a failure's full
        detail, since the menus' line only had room for its title."""
        controller = self.controller
        if controller.failure and not controller.active and not controller.waiting_for_permission:
            # Kept here too: the failure may have happened while the window
            # was closed, with only the menu's status line to show it.
            return f"{controller.failure}. {controller.failure_detail}".strip()
        return controller.status_sentence()

    def _refresh(self, granted: bool, waiting_for_permission: bool) -> None:
        controller = self.controller
        # The user's choice, as the menus show it: on while it waits for
        # permission, is paused, or Test and Tune has it paused.
        self.switch.setChecked(controller.wanted, animate=self.isVisible())
        self.status_row.set_detail(self.status_sentence())
        filtered = set(controller.buttons)
        for button in Button:
            set_checked(self.button_checks[button], button in filtered)
            calibrated = controller.is_calibrated(button)
            self.button_rows[button].set_value("Calibrated" if calibrated else "")
            self.button_checks[button].setAccessibleDescription("Calibrated" if calibrated else "")
        custom = menus.strength(controller) == menus.CUSTOM
        self.strength_picker.setCurrentIndex(1 if custom else 0)
        for button in Button:
            self.window_boxes[button].setValue(controller.threshold_for(button))
            row = self.window_rows[button]
            if self.mac:
                # Custom shows the filtered buttons' windows; Automatic none.
                row.setVisible(custom and button in filtered)
            else:
                row.setVisible(button in filtered)
                self.window_boxes[button].setEnabled(custom)
        self.wheel_box.setValue(controller.wheel_window_ms)
        self.strength_note.setText(CUSTOM_NOTE if custom else AUTOMATIC_NOTE)
        if self.mac:
            self.wheel_window_row.setVisible(custom and controller.wheel_fix)
        else:
            self.wheel_window_row.setVisible(controller.wheel_fix)
            self.wheel_box.setEnabled(custom)
            self.strength_expander.header.set_detail(CUSTOM_SHORT if custom else AUTOMATIC_SHORT)
        set_checked(self.wheel_switch, controller.wheel_fix)
        show = permissions.needs_accessibility() and not granted
        self.permission.setVisible(show)
        self.permission_gap.setVisible(show)
        self._refresh_conflicts()
        if self.pause is not None:
            self.pause.refresh()
        if self.login is not None:
            self.login.refresh()
        if self.info_bar is not None:
            first_run = not controller.tray_hint_shown
            self.info_bar.setVisible(first_run)
            self.info_gap.setVisible(first_run)

    def _refresh_conflicts(self) -> None:
        conflicts = list(self.controller.conflicts())
        shown = [(conflict.name, tuple(conflict.takes)) for conflict in conflicts]
        if shown != self._conflicts_shown:
            # Built again only when they change, so focus stays put.
            self._conflicts_shown = shown
            self.conflicts.clear()
            for conflict in conflicts:
                self.conflicts.add(
                    Row(menus.conflict_title(conflict), menus.conflict_detail(conflict), None,
                        SymbolView("warning", 20, "symbol"))
                )
        self.conflicts.setVisible(bool(conflicts))
        self.conflict_gap.setVisible(bool(conflicts))

    def set_menu_bar_shown(self, shown: bool) -> None:
        if self.menu_bar_switch is not None:
            self.menu_bar_switch.setChecked(shown, animate=False)

    def note_global_event(self, event: ClickEvent) -> None:
        """A blocked bounce: the status sentence counts them."""
        if event.is_bounce and self.isVisible():
            self.status_row.set_detail(self.status_sentence())

    # -- changes ----------------------------------------------------------------
    def _on_buttons(self, _checked: bool = False) -> None:
        if self._loading:
            return
        selected = [button for button, check in self.button_checks.items() if check.isChecked()]
        if not selected:
            # Something has to stay protected; keep the left button on.
            set_checked(self.button_checks[Button.LEFT], True)
            selected = [Button.LEFT]
        self.controller.set_buttons(selected)

    def _on_strength(self, index: int) -> None:
        value = self.strength_picker.itemData(index)
        if self._loading or value == menus.strength(self.controller):
            return
        self.controller.set_strength(value)
        if value == menus.CUSTOM and self.strength_expander is not None:
            self.strength_expander.set_expanded(True)

    def _on_window(self, button: Button, value: int) -> None:
        # Each step reaches the filter at once; the file is written once the
        # steps stop, not on every one.
        if not self._loading:
            self.controller.set_threshold(button, value, deferred=True)

    def _on_wheel(self, checked: bool) -> None:
        if not self._loading:
            self.controller.set_wheel_fix(checked)

    def _on_wheel_window(self, value: int) -> None:
        if not self._loading:
            self.controller.set_wheel_fix(self.controller.wheel_fix, value, deferred=True)


class AboutPage(Page):
    """About (macOS) / Settings (Windows): updates, diagnostics, the
    acknowledgements, what the app sends over the network, and the count's
    reset. Windows also keeps Start with Windows and the pause options here."""

    def __init__(self, controller: AppController, updater=None, parent: Optional[QWidget] = None) -> None:
        super().__init__("About" if IS_MAC else "Settings", parent)
        self.controller = controller
        self.updater = updater
        self.login: Optional[LoginControl] = None
        self.pause: Optional[PauseControls] = None

        if IS_MAC:
            hero = self.section()
            self.version_row = hero.add(Row(DISPLAY_NAME, f"Version {__version__}", None, AppIconView(40)))
        else:
            self.header("Startup")
            startup_section = self.section()
            self.login = LoginControl(controller, self)
            startup_section.add(self.login.row)
            self.header("Pausing")
            pausing = self.section()
            self.pause = PauseControls(controller)
            for row in self.pause.rows:
                pausing.add(row)

        self.permission_row: Optional[Row] = None
        if permissions.needs_accessibility():
            self.header("Permission")
            section = self.section()
            self.permission_icon = SymbolView("ok", 20, "symbol")
            button = make_button("Open Settings…")
            button.setAccessibleName(f"Open {permissions.pane_name()} settings")
            button.clicked.connect(lambda: permissions.open_accessibility_settings())
            self.permission_button = button
            # The name System Settings itself uses, which macOS 27 changed.
            self.permission_row = section.add(Row(permissions.pane_name(), "", button, self.permission_icon))

        self.update_header = self.header("Software Update" if IS_MAC else "Updates")
        self.update_section = self.section()
        self.auto_check_switch = Switch(accessible_name="Check for updates automatically")
        self.auto_check_switch.clicked.connect(self._on_auto_check)
        self.update_section.add(Row("Check for updates automatically", "", self.auto_check_switch, card_icon("sync")))
        self.auto_install_switch = Switch(accessible_name="Install updates automatically")
        self.auto_install_switch.clicked.connect(self._on_auto_install)
        self.update_section.add(Row("Install updates automatically", "", self.auto_install_switch,
                                    card_icon("download")))
        self.update_button = make_button("Check Now")
        self.update_button.setAccessibleName("Check for updates now")
        self.update_button.clicked.connect(self._on_update_button)
        self.update_row = self.update_section.add(
            Row(f"Version {__version__}", "", self.update_button, card_icon("info"))
        )
        supported = updater is not None and updater.supported
        self.update_header.setVisible(supported)
        self.update_section.setVisible(supported)
        if supported:
            updater.changed.connect(self.refresh_update)

        self.header("Troubleshooting")
        troubleshooting = self.section()
        self.diagnostics_button = make_button("Copy Diagnostics")
        self.diagnostics_button.clicked.connect(self._copy_diagnostics)
        self.diagnostics_row = troubleshooting.add(
            Row("Diagnostics", DIAGNOSTICS_DETAIL, self.diagnostics_button, card_icon("copy"))
        )
        self._diagnostics_timer = QTimer(self)
        self._diagnostics_timer.setSingleShot(True)
        self._diagnostics_timer.setInterval(4000)
        self._diagnostics_timer.timeout.connect(lambda: self.diagnostics_row.set_detail(DIAGNOSTICS_DETAIL))
        self.reset_button = make_button("Reset…")
        self.reset_button.setAccessibleName("Reset the count of blocked bounces")
        self.reset_button.clicked.connect(self._confirm_reset)
        self.stats_row = troubleshooting.add(Row("Blocked bounces", "", self.reset_button, card_icon("reset")))

        self.header("Privacy and Licences" if IS_MAC else "About")
        about = self.section()
        if not IS_MAC:
            about.add(Row(DISPLAY_NAME, f"Version {__version__} · Free and open source", None, AppIconView(28)))
        self.privacy_row = about.add(Row("Privacy", privacy_line(), None, card_icon("privacy")))
        self.acknowledgements_button = make_button("Acknowledgements…")
        self.acknowledgements_button.clicked.connect(self._open_acknowledgements)
        self.acknowledgements_row = about.add(
            Row("Open-source software", ACKNOWLEDGEMENTS_DETAIL, self.acknowledgements_button, card_icon("info"))
        )
        self.body.addStretch(1)

    def refresh(self, granted: bool) -> None:
        if self.login is not None:
            self.login.refresh()
        if self.pause is not None:
            self.pause.refresh()
        if self.permission_row is not None:
            self.permission_icon.name = "ok" if granted else "warning"
            self.permission_icon.update()
            self.permission_row.set_detail("Allowed" if granted else "Not allowed")
        total = self.controller.filtered_total
        self.stats_row.set_detail(f"{total:,} blocked so far. Reset starts the count again from zero.")
        self.refresh_update()

    def refresh_update(self) -> None:
        updater = self.updater
        if updater is None or not updater.supported:
            return
        set_checked(self.auto_check_switch, updater.auto_check)
        set_checked(self.auto_install_switch, updater.auto_install)
        # Only a background check installs anything by itself.
        self.auto_install_switch.setEnabled(updater.auto_check)
        release = updater.release
        states = {
            updater.CHECKING: ("Checking for updates…", "Check Now", False),
            updater.CURRENT: (updater.message or "Up to date", "Check Now", True),
            updater.AVAILABLE: (f"Version {release.version} is available" if release else "", "Update Now", True),
            updater.DOWNLOADING: (f"Downloading… {int(updater.progress * 100)}%", "Update Now", False),
            updater.READY: (
                f"Version {release.version} is ready. It installs when you close this window."
                if release else "",
                "Restart Now",
                True,
            ),
            updater.INSTALLING: (f"Installing. {DISPLAY_NAME} will reopen.", "Update Now", False),
            updater.FAILED: (updater.message, "Try Again", True),
        }
        detail, text, enabled = states.get(updater.state, ("", "Check Now", True))
        self.update_row.set_detail(detail)
        self.update_button.setText(label(text))
        self.update_button.setAccessibleName(
            "Check for updates now" if text == "Check Now" else label(text)
        )
        self.update_button.setEnabled(enabled)
        set_primary(self.update_button, updater.state in (updater.AVAILABLE, updater.READY))

    def _on_update_button(self) -> None:
        if self.updater is None:
            return
        if self.updater.state in (self.updater.AVAILABLE, self.updater.READY):
            self.updater.install()
        else:
            self.updater.check(user_initiated=True)

    def _on_auto_check(self, checked: bool) -> None:
        if self.updater is not None:
            self.updater.set_auto_check(checked)

    def _on_auto_install(self, checked: bool) -> None:
        # Through the updater, which also drops a download waiting to
        # install when this is turned off.
        if self.updater is not None:
            self.updater.set_auto_install(checked)

    def _copy_diagnostics(self) -> None:
        """Put a bug report's worth of detail on the clipboard: versions,
        state, settings and the end of the log."""
        window = self.window()
        state = self.controller.diagnostic_state()
        if permissions.needs_accessibility():
            state[f"{permissions.pane_name()} permission"] = getattr(window, "_permission_granted", "?")
        if self.updater is not None and self.updater.supported:
            state["update"] = f"{self.updater.state} {self.updater.message}".strip()
        text = diagnostics.report(__version__, state, self.controller.diagnostic_settings())
        QApplication.clipboard().setText(text)
        self.diagnostics_row.set_detail("Copied. Paste it into your bug report.")
        self._diagnostics_timer.start()

    def _open_acknowledgements(self) -> None:
        """Open the third-party notices (THIRD_PARTY_NOTICES.md) that ship
        with the app; say where they are if nothing on this computer opens
        that kind of file."""
        path = notices.notices_path()
        if path is not None and notices.open_file(path):
            return
        QMessageBox.information(
            self,
            "Acknowledgements",
            f"The licences of the software {DISPLAY_NAME} is built on are in:\n\n{path}"
            if path is not None
            else f"This copy has no {notices.NOTICES_FILE}. It is in {DISPLAY_NAME}'s source on GitHub.",
        )

    def _confirm_reset(self) -> None:
        answer = QMessageBox.question(
            self,
            "Reset the count?",
            "Blocked bounces will start again from zero. The History is kept.",
            QMessageBox.StandardButton.Reset | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Reset:
            self.controller.reset_statistics()


# -- window ----------------------------------------------------------------------

def transparent(widget: QWidget) -> None:
    """Let what is behind `widget` show through: no fill of its own. (Not a
    transparent palette either: a palette passes down to every control.)"""
    widget.setAutoFillBackground(False)


class MainWindow(QWidget):
    """Settings (macOS) or the app's window (Windows), over the panes
    (pages()). Also keeps the filter running: see the module's docstring."""

    closed_to_tray = Signal()
    #: macOS: "Show in menu bar" was switched (the app shows or hides the item).
    menu_bar_toggled = Signal(bool)

    def __init__(self, controller: AppController, updater=None) -> None:
        super().__init__()
        self.controller = controller
        self.updater = updater
        set_look(current_look())
        # The layout it was built in, which everything after follows.
        self.mac = IS_MAC
        self.items = pages()
        self.page_keys = [key for key, _title in self.items]
        self.setWindowTitle(DISPLAY_NAME)
        self.setWindowIcon(icons.app_icon())
        self._material = False
        self.translucent = False
        self.native_toolbar: Optional[NativeToolbar] = None
        self.pane_bar: Optional[PaneBar] = None

        self.home = HomePage(controller)
        self.exceptions = ExceptionsPage(controller)
        self.history = HistoryPage(controller)
        self.about = AboutPage(controller, updater)
        # The halves of Exceptions.
        self.apps = self.exceptions.apps
        self.devices = self.exceptions.devices
        by_key = {"general": self.home, "home": self.home, "exceptions": self.exceptions,
                  "history": self.history, "about": self.about, "settings": self.about}
        self.pages = [by_key[key] for key in self.page_keys]
        self.stack = QStackedWidget()
        for page in self.pages:
            area = QScrollArea()
            area.setWidgetResizable(True)
            # Not a Tab stop of its own: it shows no focus, so Tab would seem
            # to do nothing. The wheel still scrolls it, and so does moving
            # focus to a control inside it.
            area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            area.setFrameShape(QFrame.Shape.NoFrame)
            area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            area.setWidget(page)
            # The window's colour shows through; never a style sheet, which
            # would send every control below it through Qt's own drawing.
            transparent(area)
            transparent(area.viewport())
            transparent(page)  # setWidget turned its fill on
            self.stack.addWidget(area)

        # Test and Tune: its own window, kept for the app's life.
        self.tune = TuneWindow(controller)
        self.destroyed.connect(self.tune.deleteLater)

        if IS_MAC:
            self._build_mac()
        else:
            self._build_windows()

        self.navigation.current_changed.connect(self._show_page)
        # clicked, not toggled: refresh() sets the switch, and must not echo
        # back into a request.
        self.home.switch.clicked.connect(self._on_switch)
        self.home.tune_requested.connect(self.show_tune)
        self.home.menu_bar_toggled.connect(self.menu_bar_toggled)
        self.tune.state_changed.connect(self._sync_pause)
        self.tune.filter_requested.connect(self.request_filter)
        controller.filter_state_changed.connect(self._on_filter_state)
        controller.settings_changed.connect(self.refresh)
        controller.pause_changed.connect(self.refresh)
        controller.conflicts_changed.connect(self.refresh)
        controller.global_event.connect(self.home.note_global_event)
        controller.global_event.connect(self.tune.note_global_event)
        # From the hook's thread.
        controller.device_seen.connect(self.devices.refresh, Qt.ConnectionType.QueuedConnection)
        controller.hook_failed.connect(self._on_hook_failed)
        # Reported from the hook's thread; handled here, on the UI thread.
        controller.permission_lost.connect(self._on_permission_lost, Qt.ConnectionType.QueuedConnection)

        self._save_timer = QTimer(self)
        self._save_timer.setInterval(20000)
        self._save_timer.timeout.connect(controller.flush_stats)
        self._save_timer.start()

        # macOS sends no reliable notification when Accessibility is granted
        # or revoked, so poll it every second (see _check_permission). While
        # filtering, that poll is also the guard against a revoked grant.
        self._permission_granted = permissions.has_accessibility()
        self._enable_when_granted = False
        self._permission_timer = QTimer(self)
        self._permission_timer.setInterval(1000)
        self._permission_timer.timeout.connect(self._check_permission)
        if permissions.needs_accessibility():
            self._permission_timer.start()

        # A tap macOS disabled and couldn't re-arm lets every click through;
        # look now and then, and rebuild it.
        self._health_timer = QTimer(self)
        self._health_timer.setInterval(HEALTH_CHECK_MS)
        self._health_timer.timeout.connect(self._check_health)

        # Retries for a filter that failed to start in the background.
        self._retry_timer = QTimer(self)
        self._retry_timer.setSingleShot(True)
        self._retry_timer.timeout.connect(self._retry_start)
        self._retry_waits: list[int] = []
        # Set while a retry runs: its failure shows no dialog.
        self._quiet = False

        # A Mac Settings window opens on the pane last shown; Windows on Home.
        remembered = ui_state.load().get(LAST_PANE, "") if IS_MAC else ""
        self._show_page(self.page_keys.index(remembered) if remembered in self.page_keys else 0)
        if not self._restore_geometry() and not IS_MAC:
            self._fit_to_screen()
        self._fit_to_pane()

    # -- chrome -------------------------------------------------------------------
    def _build_mac(self) -> None:
        """A Settings window: a toolbar of panes, titled with the pane, sized
        to it, and no zoom."""
        self.setWindowFlags(
            Qt.WindowType.Window | Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowMinimizeButtonHint | Qt.WindowType.CustomizeWindowHint
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        # Drawn by Qt where AppKit's own toolbar can't be had.
        self.pane_bar = PaneBar(self.items)
        self.navigation = self.pane_bar
        root.addWidget(self.pane_bar)
        self.bar_line = QFrame()
        self.bar_line.setFrameShape(QFrame.Shape.HLine)
        self.bar_line.setFrameShadow(QFrame.Shadow.Sunken)
        root.addWidget(self.bar_line)
        root.addWidget(self.stack, 1)
        self.native_toolbar = NativeToolbar(self.items, lambda index: self._show_page(index))

    def _build_windows(self) -> None:
        """Windows Settings' layout: the navigation pane on the left, Settings
        pinned at its foot, the page on the right; Mica behind both."""
        self.translucent = native.prepare(self)
        side = look().sidebar_width
        page_margins = 72
        # Every pane scrolls, so the minimum height only has to fit the
        # navigation; Windows at 200% scaling leaves about 490 px.
        self.setMinimumSize(side + page_margins + COLUMN_MIN + 16, 480)
        self.resize(side + page_margins + COLUMN_MAX // 2 + 60, 700)
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.navigation = NavigationView(self.items[:-1], self.items[-1:])
        root.addWidget(self.navigation)
        root.addWidget(self.stack, 1)

    # -- size and position ---------------------------------------------------------
    def _restore_geometry(self) -> bool:
        """Reopen where the window was left, as Mac and Windows apps do.
        Qt keeps a restored window on a screen that is still there."""
        saved = self.controller.settings.get("window_geometry") or ""
        if saved:
            return bool(self.restoreGeometry(QByteArray.fromBase64(saved.encode("ascii"))))
        return False

    def _fit_to_screen(self) -> None:
        """First launch: the default size, but never more than the screen's
        work area. A 1080p laptop at 150% has about 670 px above the
        taskbar, less than the default, and Qt only centres a window that
        fits; one that doesn't hides its bottom edge under the taskbar."""
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        # The size set here leaves out the title bar and frame.
        width = min(self.width(), available.width() - FRAME_ALLOWANCE[0])
        height = min(self.height(), available.height() - FRAME_ALLOWANCE[1])
        self.setMinimumSize(min(self.minimumWidth(), width), min(self.minimumHeight(), height))
        self.resize(width, height)

    def pane_height(self, index: Optional[int] = None) -> int:
        """How tall pane `index` (the current one) is at the window's width,
        as its scroll area lays it out: the height for that width, never
        under the pane's minimum. (Its size hint is for a narrower width,
        where text wraps more, and would leave a gap below the pane.)"""
        page = self.pages[self.stack.currentIndex() if index is None else index]
        minimum = page.minimumSizeHint().height()
        if page.hasHeightForWidth():
            return max(page.heightForWidth(SETTINGS_WIDTH), minimum)
        return max(page.sizeHint().height(), minimum)

    def _fit_to_pane(self) -> None:
        """macOS: the Settings window takes its pane's height, up to what
        the screen can show (the pane scrolls beyond that), at a fixed width.
        The top edge stays where it is."""
        if not self.mac:
            return
        chrome = 0
        if self.pane_bar is not None and not self.pane_bar.isHidden():
            chrome = self.pane_bar.sizeHint().height() + self.bar_line.sizeHint().height()
        screen = self.screen() or QGuiApplication.primaryScreen()
        room = (screen.availableGeometry().height() if screen is not None else 800) - SETTINGS_SCREEN_ROOM
        height = max(260, min(self.pane_height() + chrome, room))
        self.setFixedSize(SETTINGS_WIDTH, height)

    def save_geometry(self) -> None:
        encoded = bytes(self.saveGeometry().toBase64()).decode("ascii")
        self.controller.set_window_geometry(encoded)

    # -- native chrome ---------------------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self.mac:
            toolbar = self.native_toolbar
            if toolbar is not None and not toolbar.installed and toolbar.available():
                if toolbar.install(self, self.stack.currentIndex()):
                    self.pane_bar.hide()
                    self.bar_line.hide()
                    self._fit_to_pane()
            self._rest_focus()
        elif self.translucent and not self._material:
            self._material = native.apply(self, look().dark)
        self._tint_title_bar()
        self._sync_pause()
        self.update()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._sync_pause()

    def paintEvent(self, _event) -> None:  # noqa: N802
        if self.mac:
            return  # the window's own background, as AppKit draws it
        if self.translucent and self._material:
            return  # Mica fills the whole window, as in Windows Settings
        QPainter(self).fillRect(self.rect(), look().pane)

    def changeEvent(self, event) -> None:  # noqa: N802
        if event.type() == QEvent.Type.ActivationChange:
            if hasattr(self.navigation, "set_window_active"):
                self.navigation.set_window_active(self.isActiveWindow())
            if self.isActiveWindow() and self._login_page_shown():
                # Back from System Settings or Task Manager, where the login
                # item may just have been switched.
                self.controller.refresh_login_item()
                self.refresh()
        if (
            event.type() == QEvent.Type.WindowStateChange
            and not self.mac
            and self.isMinimized()
            and QSystemTrayIcon.isSystemTrayAvailable()
        ):
            # Windows: minimizing goes to the notification area, like closing,
            # instead of leaving a taskbar button behind.
            QTimer.singleShot(0, self.close)
        super().changeEvent(event)

    def _rest_focus(self) -> None:
        """macOS: a pane opens with no control focused, as AppKit's do (Qt
        would otherwise ring the first one); Tab moves into it from here."""
        focused = QApplication.focusWidget()
        if focused is None or focused is self or self.stack.isAncestorOf(focused):
            self.setFocus(Qt.FocusReason.OtherFocusReason)

    def _login_page_shown(self) -> bool:
        return self.current_key in ("general", "settings")

    def apply_look(self) -> None:
        """Pick up a light/dark or accent change from the system."""
        set_look(current_look())
        self._tint_title_bar()
        for text in self.findChildren(TextLabel):
            text.restyle()
        for button in self.findChildren(QPushButton):
            if button.isDefault():
                set_primary(button, True)
        for widget in self.findChildren(QWidget):
            if hasattr(widget, "restyle") and not isinstance(widget, TextLabel):
                widget.restyle()
            widget.update()
        self.tune.apply_look()
        self.update()

    def _tint_title_bar(self) -> None:
        # Mica already runs through the title bar; a solid window tints it.
        native.set_dark_title_bar(self, look().dark, None if self._material else look().pane)

    # -- navigation --------------------------------------------------------------
    @property
    def current_key(self) -> str:
        return self.page_keys[self.stack.currentIndex()]

    def _show_page(self, index: int) -> None:
        self.navigation.set_current(index, emit=False)
        self.stack.setCurrentIndex(index)
        if self.mac:
            # The window is titled with the pane, as Settings windows are,
            # and opens on it next time.
            self.setWindowTitle(self.items[index][1])
            if self.native_toolbar is not None:
                self.native_toolbar.select(index)
            ui_state.remember(**{LAST_PANE: self.page_keys[index]})
            self._rest_focus()
        if self._login_page_shown():
            self.controller.refresh_login_item()
        self.refresh()
        self._fit_to_pane()

    def show_page(self, key: str) -> None:
        """Show the pane named `key` (either platform's name for it)."""
        if key not in self.page_keys:
            key = PAGE_ALIASES.get(key, key)
        if key in self.page_keys:
            self._show_page(self.page_keys.index(key))

    def show_tune(self, button: object = None) -> None:
        """Open Test and Tune, testing `button` if one is named: over this
        window when it is up, in the middle of the screen when not."""
        tune = self.tune
        tune.open_for(button)
        if not tune.isVisible():
            anchor = self.frameGeometry() if self.isVisible() else None
            if anchor is None:
                screen = QGuiApplication.primaryScreen()
                anchor = screen.availableGeometry() if screen is not None else None
            if anchor is not None:
                tune.move(anchor.center() - QPoint(tune.width() // 2, tune.height() // 2))
        if tune.isMinimized():
            tune.showNormal()
        else:
            tune.show()
        tune.raise_()
        tune.activateWindow()

    # -- the pause while Test and Tune measures ------------------------------------
    def _should_pause(self) -> bool:
        """Test and Tune has to see the raw clicks, so filtering pauses, but
        only while it is measuring in front of the user: not on the verdict
        or the tune step, and not while its window is hidden, minimized or
        behind another app, where those clicks would go unfiltered."""
        tune = self.tune
        return tune.measuring and tune.isVisible() and not tune.isMinimized() and tune.isActiveWindow()

    def _sync_pause(self) -> None:
        """Hold filtering while Test and Tune measures, and let go after.
        The hold is its own (suspend / end_suspension), apart from the user's
        pause and a game's: either may start or end while the pad measures,
        and filtering starts again only once neither holds it. resume()
        would end the user's pause instead."""
        if self._should_pause():
            self.controller.suspend()
        elif self.controller.suspended:
            self.controller.end_suspension()

    # -- state -------------------------------------------------------------------
    def refresh(self) -> None:
        granted = self._permission_granted
        waiting = self._enable_when_granted and not self.controller.active
        self.controller.set_waiting_for_permission(waiting)
        self.home.refresh(granted, waiting)
        self.history.refresh()
        self.exceptions.refresh()
        self.about.refresh(granted)
        self.tune.refresh()
        # A row shown or hidden (Custom's windows, a note) changes the
        # pane's height, which a Mac Settings window follows.
        self._fit_to_pane()

    def set_menu_bar_shown(self, shown: bool) -> None:
        self.home.set_menu_bar_shown(shown)

    # -- permission and the filter's life ----------------------------------------
    def _read_permission(self) -> bool:
        if not permissions.has_accessibility():
            return False
        if self.controller.active or self._enable_when_granted:
            # AXIsProcessTrusted can stay true after the app is removed from
            # the list, and a filtering tap left on a dead grant can stall
            # input everywhere. A throwaway tap tells the truth. Only asked
            # while it matters: filtering, or about to start.
            return permissions.event_tap_allowed()
        return True

    def _check_permission(self) -> None:
        if not self.controller.session_active:
            return  # nothing runs in another user's session; check on return
        granted = self._read_permission()
        if granted == self._permission_granted:
            return
        log.info("Permission %s", "granted" if granted else "withdrawn")
        self._permission_granted = granted
        if granted and self._enable_when_granted:
            # The user already asked for the filter; finish the job (after
            # calibration, if it is measuring).
            self._enable_when_granted = False
            self._on_switch(True, prompt=False)
        elif not granted and self.controller.active:
            self._wait_for_permission()
        self.refresh()

    def _wait_for_permission(self) -> None:
        """Access is gone while the user has the filter on. Without it the
        tap cannot block anything, so an "on" switch would be lying: stop
        it, and wait. The saved choice stays on, so the filter returns by
        itself once access is back, even after a restart."""
        self._permission_granted = False
        self._enable_when_granted = True
        self.controller.stop_for_permission()

    def _on_permission_lost(self, reporter: object = None) -> None:
        """The filter found its access withdrawn when macOS disabled its
        tap, let every click through and stopped. Wait for access to come
        back, as when the permission poll is first to notice."""
        if not self.controller.settings["fix_enabled"]:
            return
        if reporter is not None and reporter is not self.controller._filter:
            return  # from a filter since replaced (turned off and on again)
        log.warning("The filter lost its permission")
        self._wait_for_permission()
        self.refresh()

    def _access_withdrawn(self) -> bool:
        """macOS: whether a filtering tap would be refused right now. Asked
        when a start fails, since AXIsProcessTrusted can still say yes for an
        app removed from the list."""
        return permissions.needs_accessibility() and not permissions.event_tap_allowed()

    def check_permission_soon(self) -> None:
        """The Accessibility list just changed. macOS posts that a moment
        before the new answer can be read, so look shortly after."""
        QTimer.singleShot(300, self._check_permission)

    def request_filter(self, checked: bool, prompt: bool = True) -> None:
        """Turn the filter on or off from anywhere: the switch, the menu, launch.

        `prompt=False` is for background launches: with no window on screen,
        a missing permission waits quietly instead of raising System Settings.
        """
        self._on_switch(checked, prompt)

    def _on_switch(self, checked: bool, prompt: bool = True) -> None:
        if not checked:
            self._enable_when_granted = False
        elif permissions.needs_accessibility() and not self._permission_granted:
            # Ask macOS for access (it lists this app under Accessibility)
            # instead of failing with an error; the filter starts by itself
            # once the switch there is turned on.
            self._enable_when_granted = True
            self.refresh()
            if prompt:
                permissions.open_accessibility_settings()
            return
        if checked and self._should_pause():
            # Turned on from the menu while calibrating: the pad must keep
            # seeing raw clicks, so filtering starts when calibration ends.
            self.controller.enable_after_calibration()
            self.refresh()
            return
        self.controller.set_active(checked)
        self.refresh()

    def _on_filter_state(self, _active: bool, error: str) -> None:
        if error and self._access_withdrawn():
            # The tap was refused because access is gone, though macOS may
            # still report the app as allowed (at launch, say). That is a
            # wait for permission, which the permission row explains and the
            # poll ends, not a failure with a dialog and retries.
            log.warning("The filter couldn't start without access; waiting for it")
            self._permission_granted = False
            self._enable_when_granted = True
            self.controller.clear_failure()
            error = ""
        if self.controller.active:
            self._health_timer.start()
        else:
            self._health_timer.stop()
        if self.controller.active or not self.controller.settings["fix_enabled"]:
            self._retry_waits.clear()
            self._retry_timer.stop()
        self.refresh()
        if error and self._on_screen() and not self._quiet:
            QMessageBox.warning(self, "The filter couldn’t start", error)

    def _on_hook_failed(self, message: str) -> None:
        # Not set_active(False): that is the user's "off", and would keep the
        # filter off at every later login.
        self.controller.stop_after_failure(message)
        self.refresh()
        if self._on_screen():
            QMessageBox.warning(self, "The filter stopped", message)

    # -- keeping the filter alive ------------------------------------------------
    def restore_filter(self, background: bool) -> None:
        """Turn the saved "on" back on at launch. Opened by the user, it
        goes the usual way; at login, see _start_with_retries."""
        if background:
            self._start_with_retries()
        else:
            self.request_filter(True)

    def _start_with_retries(self) -> None:
        """Start the filter for the app's own reasons (a login, a wake, a
        session switch, a dead tap) rather than the user's. At those moments
        the window server or the permission database can answer a little
        late, so a refused tap is tried again at each of RETRY_AT_S before
        it is left to the user. The first failure shows as usual (a dialog
        only if the window is on screen); the retries show only in the
        menu's status line and the hero row."""
        self.request_filter(True, prompt=False)
        if self._start_failed():
            marks = (0, *RETRY_AT_S)
            self._retry_waits = [int((later - earlier) * 1000) for earlier, later in zip(marks, marks[1:])]
            self._retry_timer.start(self._retry_waits.pop(0))

    def _attempt_quietly(self) -> None:
        self._quiet = True
        try:
            self.request_filter(True, prompt=False)
        finally:
            self._quiet = False

    def _start_failed(self) -> bool:
        controller = self.controller
        return (
            bool(controller.failure)
            and controller.settings["fix_enabled"]
            and not controller.active
            and not controller.suspended
            and not controller.waiting_for_permission
        )

    def _retry_start(self) -> None:
        if not self._start_failed():
            self._retry_waits.clear()
            return
        log.info("Trying the filter again")
        self._attempt_quietly()
        if self._start_failed() and self._retry_waits:
            self._retry_timer.start(self._retry_waits.pop(0))

    def restart_filter(self) -> None:
        """Start the filter afresh if the user has it on: after sleep, on
        coming back to this login session, or when its tap stopped working.
        Taps made before sleep or a session switch can be left dead."""
        controller = self.controller
        if not controller.session_active or controller.suspended or self._enable_when_granted:
            return
        if not controller.settings["fix_enabled"]:
            return
        controller.stop_keeping_choice()
        self._start_with_retries()

    def _check_health(self) -> None:
        if self.controller.active and not self.controller.tap_alive():
            log.warning("The event tap stopped receiving events; rebuilding it")
            self.restart_filter()

    def system_woke(self) -> None:
        log.info("Woke from sleep")
        self.restart_filter()

    def session_resigned(self) -> None:
        """The user switched to another login session. A tap left running in
        an inactive session can stall the active one, so the controller stops
        it (stop() sends any release it was holding first) and starts none
        until the session is back. A retry still waiting is dropped too."""
        log.info("Session switched away")
        self._retry_timer.stop()
        self._retry_waits.clear()
        self.controller.set_session_active(False)

    def session_activated(self) -> None:
        log.info("Session active again")
        self.controller.set_session_active(True)
        self.restart_filter()

    def _on_screen(self) -> bool:
        """Whether a failure can be shown in a dialog. With the window closed
        or minimized the menu's status line carries it instead: a dialog
        nobody asked for would open over whatever the user is doing."""
        return self.isVisible() and not self.isMinimized()

    def closeEvent(self, event) -> None:  # noqa: N802
        event.ignore()
        self.save_geometry()
        self.controller.flush_settings()
        self.controller.flush_stats()
        self.hide()
        self._sync_pause()
        self.closed_to_tray.emit()
