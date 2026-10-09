"""The main window: a sidebar and four panes, in the platform's own idiom."""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QByteArray, QEvent, QRect, QTimer, Qt, Signal
from PySide6.QtGui import QGuiApplication, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, diagnostics, permissions, startup
from ..controller import AppController
from ..core import (
    BOUNCE_CANDIDATE_MS,
    MAX_THRESHOLD_MS,
    MIN_THRESHOLD_MS,
    REQUIRED_DOUBLE_CLICKS,
    REQUIRED_SINGLE_CLICKS,
    Button,
    Calibrator,
    ClickEvent,
)
from . import icons, native
from .theme import IS_MAC, current_look
from .widgets import (
    AppIconView,
    ClickPad,
    GapTimeline,
    Row,
    Section,
    Sidebar,
    Switch,
    SymbolView,
    TextLabel,
    ValueLabel,
    look,
    set_look,
)

log = logging.getLogger(__name__)

#: The shortest release-to-press pause that still ends a double-click while
#: calibrating. A slower system double-click setting widens it.
PAIR_WINDOW_MS = 600.0

#: A background launch whose filter fails to start tries again this many
#: seconds later: at login the window server or the permission database can
#: answer a moment late.
RETRY_AT_S = (1, 3, 8, 20)
#: How often a running filter is checked for a tap that stopped working.
HEALTH_CHECK_MS = 5000

#: Content never stretches wider than this; extra window width becomes margin,
#: so a label always stays within reach of its control.
COLUMN_MAX = 640 if IS_MAC else 1000
#: The narrowest the content column may get before the window stops shrinking.
COLUMN_MIN = 440
#: Room left around a first-launch window for its frame and title bar.
FRAME_ALLOWANCE = (16, 48)

DIAGNOSTICS_DETAIL = "Version, settings and the recent log, for a bug report. Never your clicks."

PAGES = [
    ("filter", "Bounce Filter"),
    ("test", "Test"),
    ("calibrate", "Calibrate"),
    ("general", "General"),
]


def label(text: str) -> str:
    """Button and menu wording in the platform's own case: Title Case on
    macOS ("Check Now"), sentence case on Windows ("Check now")."""
    if IS_MAC:
        return text
    first, *rest = text.split(" ")
    keep = {"DoubleClick", "Fixer", "Accessibility"}
    return " ".join([first, *(word if word in keep else word.lower() for word in rest)])


def _button(text: str, default: bool = False) -> QPushButton:
    button = QPushButton(label(text))
    set_primary(button, default)
    return button


def set_primary(button: QPushButton, primary: bool) -> None:
    """Make `button` the default one. Windows 11 fills it with the accent
    colour (WinUI's AccentButton); macOS draws its own default button."""
    button.setDefault(primary)
    button.setAutoDefault(primary)
    if IS_MAC:
        return
    if not primary:
        button.setStyleSheet("")
        return
    lk = look()
    accent = lk.accent
    text = "#000000" if lk.dark else "#ffffff"
    hover = accent.lighter(110) if lk.dark else accent.darker(110)
    button.setStyleSheet(
        "QPushButton {"
        f" background: {accent.name()}; color: {text};"
        " border: 1px solid rgba(0, 0, 0, 20); border-radius: 4px;"
        " padding: 5px 16px; min-height: 20px; }"
        f"QPushButton:hover {{ background: {hover.name()}; }}"
        f"QPushButton:pressed {{ background: {accent.name()}; color: rgba({'0,0,0' if lk.dark else '255,255,255'},180); }}"
        "QPushButton:disabled { background: rgba(128,128,128,70); color: rgba(128,128,128,200); border: none; }"
    )


def card_icon(name: str) -> Optional[SymbolView]:
    """Windows 11 Settings leads every card with a Fluent icon; macOS rows
    inside a grouped box carry none."""
    return None if IS_MAC else SymbolView(name, 20, "text")


def centred_column(parent: QWidget, maximum: int = COLUMN_MAX) -> QWidget:
    """Give `parent` a column capped at `maximum` wide, centred in any extra space."""
    outer = QHBoxLayout(parent)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)
    column = QWidget()
    column.setMaximumWidth(maximum)
    outer.addStretch(1)
    outer.addWidget(column, 100)
    outer.addStretch(1)
    return column


class Page(QWidget):
    """A pane: stacked sections with headers and footnotes."""

    def __init__(self, title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.page_title = title
        self.column = centred_column(self)
        self.body = QVBoxLayout(self.column)
        self.body.setSpacing(0)
        if IS_MAC:
            self.body.setContentsMargins(20, 4, 20, 20)
        else:
            self.body.setContentsMargins(36, 0, 36, 28)
            heading = TextLabel(title, "title")
            heading.setContentsMargins(0, 24, 0, 20)
            self.body.addWidget(heading)

    def header(self, text: str) -> TextLabel:
        label = TextLabel(text, "headline")
        label.setContentsMargins(2 if IS_MAC else 0, 18, 0, 6 if IS_MAC else 8)
        self.body.addWidget(label)
        return label

    def section(self) -> Section:
        section = Section()
        self.body.addWidget(section)
        return section

    def footnote(self, text: str) -> TextLabel:
        label = TextLabel(text, "caption", "secondary")
        label.setWordWrap(True)
        label.setContentsMargins(2 if IS_MAC else 0, 6, 0, 0)
        self.body.addWidget(label)
        return label

    def gap(self, height: int = 20) -> None:
        self.body.addSpacing(height)


# -- panes -----------------------------------------------------------------------

class FilterPage(Page):
    calibrate_requested = Signal()

    def __init__(self, controller: AppController, parent: Optional[QWidget] = None) -> None:
        super().__init__("Bounce Filter", parent)
        self.controller = controller
        self._loading = False

        self.permission = self.section()
        self.permission_button = _button("Open Settings…")
        # The same path as the switch: once access is granted, the filter starts.
        self.permission_button.clicked.connect(lambda: self.window().request_filter(True))
        self.permission_row = self.permission.add(
            Row(
                "Permission required",
                f"Allow DoubleClick Fixer in Privacy & Security › {permissions.pane_name()}.",
                self.permission_button,
                SymbolView("warning", 22, "symbol"),
            )
        )
        self.permission_gap = QWidget()
        self.permission_gap.setFixedHeight(20)
        self.body.addWidget(self.permission_gap)

        main = self.section()
        self.switch = Switch(accessible_name="Bounce filter")
        self.status_row = main.add(Row("Bounce Filter", "", self.switch, AppIconView(34 if IS_MAC else 32)))

        self.header("Filter window")
        window = self.section()
        slider_box = QWidget()
        slider_layout = QHBoxLayout(slider_box)
        slider_layout.setContentsMargins(0, 0, 0, 0)
        slider_layout.setSpacing(10)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(MIN_THRESHOLD_MS, MAX_THRESHOLD_MS)
        self.slider.setPageStep(5)
        # Flexes with the window instead of forcing it wider.
        self.slider.setMinimumWidth(110)
        self.slider.setMaximumWidth(240)
        self.slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.slider.setAccessibleName("Filter window in milliseconds")
        self.value = ValueLabel()
        self.value.setMinimumWidth(52)
        slider_layout.addWidget(self.slider)
        slider_layout.addWidget(self.value)
        window.add(Row("Ignore presses within", "", slider_box, card_icon("stopwatch")))
        self.window_note = self.footnote("")
        self.window_note.setTextFormat(Qt.TextFormat.RichText)
        self.window_note.linkActivated.connect(lambda _link: self.calibrate_requested.emit())

        self.header("Buttons")
        buttons = self.section()
        self.button_switches: dict[Button, Switch] = {}
        for button in Button:
            switch = Switch(accessible_name=f"Filter the {button.label.lower()} button")
            switch.clicked.connect(self._on_buttons)
            self.button_switches[button] = switch
            buttons.add(Row(f"{button.label} button", "", switch, card_icon("mouse")))

        self.header("Activity")
        activity = self.section()
        self.total_value = ValueLabel()
        self.session_value = ValueLabel()
        activity.add(Row("Blocked in total", "", self.total_value, card_icon("chart")))
        activity.add(Row("Blocked since launch", "", self.session_value, card_icon("chart")))
        self.body.addStretch(1)

        self.slider.valueChanged.connect(self._on_slider)
        # While dragging, only the label follows; the value is saved on release.
        self.slider.sliderReleased.connect(lambda: self._on_slider(self.slider.value()))

    def refresh(self, granted: bool, waiting_for_permission: bool = False) -> None:
        self._loading = True
        controller = self.controller
        threshold = controller.threshold_ms
        # The user's choice, as the menus show it: on while it waits for
        # permission or calibration has it paused.
        self.switch.setChecked(controller.wanted, animate=self.isVisible())
        if controller.suspended and controller.settings["fix_enabled"]:
            self.status_row.set_detail("Paused for calibration.")
        elif waiting_for_permission:
            self.status_row.set_detail(f"Waiting for {permissions.pane_name()} permission.")
        elif controller.failure and not controller.active:
            # Kept here too: the failure may have happened while the window
            # was closed, with only the menu's status line to show it.
            self.status_row.set_detail(f"{controller.failure}. {controller.failure_detail}".strip())
        else:
            self.status_row.set_detail("Ignores the extra click a worn switch adds.")
        self.slider.setValue(threshold)
        self.value.setText(f"{threshold} ms")
        if self.controller.calibrated:
            self.window_note.setText("Most worn switches bounce within 30 ms.")
        else:
            accent = look().accent.name()
            self.window_note.setText(
                f'Not calibrated yet. <a href="calibrate" style="color:{accent}; text-decoration:none">'
                f"{label('Calibrate')}</a> measures your mouse and sets this for you."
            )
        for button, switch in self.button_switches.items():
            switch.setChecked(button in self.controller.buttons, animate=False)
        self.total_value.setText(f"{self.controller.filtered_total:,}")
        self.session_value.setText(f"{self.controller.session_filtered:,}")
        show = permissions.needs_accessibility() and not granted
        self.permission.setVisible(show)
        self.permission_gap.setVisible(show)
        self._loading = False

    def note_global_event(self, event: ClickEvent) -> None:
        if event.is_bounce:
            self.total_value.setText(f"{self.controller.filtered_total:,}")
            self.session_value.setText(f"{self.controller.session_filtered:,}")

    def _on_slider(self, value: int) -> None:
        self.value.setText(f"{value} ms")
        if not self._loading and not self.slider.isSliderDown():
            self.controller.set_threshold(value)

    def _on_buttons(self, _checked: bool) -> None:
        if self._loading:
            return
        selected = [button for button, switch in self.button_switches.items() if switch.isChecked()]
        if not selected:
            # Something has to stay protected; keep the left button on.
            self.button_switches[Button.LEFT].setChecked(True)
            selected = [Button.LEFT]
        self.controller.set_buttons(selected)


class TestPage(Page):
    def __init__(self, controller: AppController, parent: Optional[QWidget] = None) -> None:
        super().__init__("Test", parent)
        self.controller = controller
        self.clicks = 0
        self.shortest_gap: Optional[float] = None

        self.pad = ClickPad()
        self.pad.setMaximumHeight(320)
        self.body.addWidget(self.pad, 1)
        self.gap(12)

        chart = self.section()
        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(14, 10, 14, 8)
        self.timeline = GapTimeline()
        holder_layout.addWidget(self.timeline)
        chart.add(holder)
        self.chart_note = self.footnote("")

        self.header("Measurements")
        values = self.section()
        self.last_value = ValueLabel("—")
        self.shortest_value = ValueLabel("—")
        self.count_value = ValueLabel("0")
        values.add(Row("Last gap", "", self.last_value))
        values.add(Row("Shortest gap", "", self.shortest_value))
        values.add(Row("Clicks", "", self.count_value))

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 14, 0, 0)
        actions.addStretch(1)
        self.clear_button = _button("Clear")
        self.clear_button.clicked.connect(self.reset)
        actions.addWidget(self.clear_button)
        self.body.addLayout(actions)
        # Once the pad reaches its full height, spare space collects at the
        # bottom instead of opening gaps between sections.
        self.body.addStretch(1)

        self.pad.pressed_with_gap.connect(self._on_pad_press)

    def refresh(self) -> None:
        self.timeline.set_threshold(self.controller.threshold_ms)
        # With the filter on, bounces are removed before this pad sees them,
        # so an all-green chart would otherwise read as a healthy mouse.
        self.chart_note.setText(
            "Bounce Filter is on: red bars are bounces it blocked."
            if self.controller.active
            else "Red bars fall within the filter window."
        )

    def note_global_event(self, event: ClickEvent) -> None:
        """A bounce the system-wide filter blocked. With the filter on, this
        pad never receives it, so show it here: proof the filter works."""
        if self.isVisible() and event.is_bounce and event.gap_ms is not None:
            self.timeline.add(event.gap_ms, True)
            self.pad.flash(True)

    def reset(self) -> None:
        self.clicks = 0
        self.shortest_gap = None
        self.timeline.clear()
        self.pad.reset()
        self.last_value.setText("—")
        self.shortest_value.setText("—")
        self.count_value.setText("0")

    def _on_pad_press(
        self, gap_ms: Optional[float], _interval_ms: Optional[float], button: Button = Button.LEFT
    ) -> None:
        self.clicks += 1
        self.count_value.setText(str(self.clicks))
        if gap_ms is None:
            self.pad.flash(False)
            return
        bounce = gap_ms <= self.controller.threshold_ms
        self.timeline.add(gap_ms, bounce)
        # Each button is timed against its own release; say which one.
        which = "" if button is Button.LEFT else f" ({button.label.lower()})"
        self.last_value.setText(f"{gap_ms:.0f} ms{which}")
        if self.shortest_gap is None or gap_ms < self.shortest_gap:
            self.shortest_gap = gap_ms
            self.shortest_value.setText(f"{gap_ms:.0f} ms")
        self.pad.flash(bounce)


class CalibratePage(Page):
    """Two measured phases, then a recommendation."""

    threshold_chosen = Signal(int)
    #: The phase moved: "intro", "single", "double" or "done". Filtering
    #: pauses only while a measuring phase is on screen.
    phase_changed = Signal(str)

    def __init__(self, controller: AppController, parent: Optional[QWidget] = None) -> None:
        super().__init__("Calibrate", parent)
        self.controller = controller
        self.calibrator = Calibrator()
        self.phase = "intro"
        self.suggestion = None
        # The button whose first press of a double-click is waiting for its
        # second, or None.
        self._pair_button: Optional[Button] = None

        # The step and its progress bar are one row, so they share one box
        # (macOS) or one card (Windows).
        step = self.section()
        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.setSpacing(0)
        self.count_label = ValueLabel()
        self.step_row = Row("", "", self.count_label)
        holder_layout.addWidget(self.step_row)
        self.progress_row = QWidget()
        progress_layout = QVBoxLayout(self.progress_row)
        progress_layout.setContentsMargins(12 if IS_MAC else 16, 0, 12 if IS_MAC else 16, 12 if IS_MAC else 16)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setAccessibleName("Calibration progress")
        progress_layout.addWidget(self.progress)
        holder_layout.addWidget(self.progress_row)
        step.add(holder)
        self.gap(12)

        self.pad = ClickPad()
        self.pad.setMaximumHeight(360)
        self.body.addWidget(self.pad, 1)

        self.result_header = self.header("Result")
        self.result = self.section()
        self.recommended_value = ValueLabel()
        self.bounce_value = ValueLabel()
        self.double_value = ValueLabel()
        self.result.add(Row("Recommended window", "", self.recommended_value))
        self.result.add(Row("Longest bounce", "", self.bounce_value))
        self.result.add(Row("Fastest double-click", "", self.double_value))
        self.summary = self.footnote("")

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 16, 0, 0)
        actions.setSpacing(8)
        actions.addStretch(1)
        self.restart_button = _button("Start Over")
        self.restart_button.clicked.connect(self.restart)
        self.primary_button = _button("Begin", default=True)
        self.primary_button.clicked.connect(self._advance)
        actions.addWidget(self.restart_button)
        actions.addWidget(self.primary_button)
        self.body.addLayout(actions)
        self.body.addStretch(1)

        self.pad.pressed_with_gap.connect(self._on_pad_press)
        self.restart()

    # -- flow ------------------------------------------------------------------
    @property
    def measuring(self) -> bool:
        return self.phase in ("single", "double")

    def _set_phase(self, phase: str) -> None:
        if phase != self.phase:
            self.phase = phase
            self.phase_changed.emit(phase)

    def restart(self) -> None:
        self.calibrator = Calibrator()
        self.suggestion = None
        self._pair_button = None
        self.pad.reset()
        self._set_phase("intro")
        self._render()

    def _advance(self) -> None:
        if self.phase == "intro":
            self._set_phase("single")
        elif self.phase == "single":
            self._pair_button = None
            self._set_phase("double")
        elif self.phase == "double":
            self._finish()
            return
        elif self.phase == "done":
            if self.suggestion is not None:
                self.threshold_chosen.emit(self.suggestion.threshold_ms)
                self.controller.set_calibrated(True)
            return
        self.pad.reset()
        self._render()

    def _finish(self) -> None:
        self.suggestion = self.calibrator.suggest()
        self._set_phase("done")
        self._render()

    @staticmethod
    def pair_window_ms() -> float:
        """How long a double-click may pause between its release and second
        press: at least PAIR_WINDOW_MS, and as long as the system's own
        double-click setting, so a pair the system accepts counts here too."""
        interval = QGuiApplication.styleHints().mouseDoubleClickInterval()
        return max(PAIR_WINDOW_MS, float(interval))

    def _on_pad_press(
        self, gap_ms: Optional[float], _interval_ms: Optional[float], button: Button = Button.LEFT
    ) -> None:
        note = ""
        if self.phase == "intro":
            # Clicking the pad is as good as Begin, and counts as the first
            # single click. Its gap means nothing: it is the first.
            self._advance()
            gap_ms = None
        if self.phase == "single":
            counted = self.calibrator.add_single_click(gap_ms)
            self.pad.flash(not counted)
            if not counted:
                note = f"Bounce detected ({gap_ms:.0f} ms)."
            if self.calibrator.has_enough_singles:
                self._pair_button = None
                self._set_phase("double")
                self.pad.reset()
                note = ""
        elif self.phase == "double":
            note = self._double_click_press(gap_ms, button)
            if self.calibrator.has_enough_doubles:
                self._finish()
                return
        else:
            return
        self._render(note)

    def _double_click_press(self, gap_ms: Optional[float], button: Button) -> str:
        """Pair presses into double-clicks: a first press opens a pair, and
        the same button's next press within the pair window closes it and
        counts. Anything else opens a new pair, so the third press of a
        triple-click never counts as another double. Returns a note."""
        if gap_ms is not None and gap_ms <= BOUNCE_CANDIDATE_MS:
            # Chatter, wherever it lands, is evidence; the pair stays open.
            self.calibrator.add_double_click(gap_ms)
            self.pad.flash(True)
            return f"Bounce detected ({gap_ms:.0f} ms)."
        if self._pair_button is button and gap_ms is not None and gap_ms <= self.pair_window_ms():
            self.calibrator.add_double_click(gap_ms)
            self._pair_button = None
            self.pad.flash(False)
            return ""
        too_slow = self._pair_button is button
        self._pair_button = button
        # Only the start of a pair: no "counted" flash.
        self.pad.flash(False, neutral=True)
        if too_slow:
            return "Too slow for a double-click, so it wasn’t counted. Double-click a little faster."
        return ""

    # -- rendering ---------------------------------------------------------------
    def _render(self, note: str = "") -> None:
        done = self.phase == "done"
        has_result = done and self.suggestion is not None
        self.result_header.setVisible(has_result)
        self.result.setVisible(has_result)
        self.summary.setVisible(has_result)
        self.restart_button.setVisible(self.phase != "intro")
        self.progress_row.setVisible(self.phase in ("single", "double"))
        self.pad.setVisible(not done)
        self.primary_button.setVisible(True)

        if self.phase == "intro":
            self.step_row.title.setText("Calibrate your mouse")
            self.step_row.set_detail(
                "Click once at a time, then double-click. Filtering pauses while you do, "
                "so the pad sees your mouse as it is."
            )
            self.count_label.setText("")
            self.pad.set_text(label("Ready"), "")
            self.primary_button.setText(label("Begin"))
            self.primary_button.setEnabled(True)
        elif self.phase == "single":
            count = self.calibrator.single_clicks
            self.step_row.title.setText("Single clicks")
            self.step_row.set_detail(note or "Click once, then pause.")
            self.count_label.setText(f"{count} of {REQUIRED_SINGLE_CLICKS}")
            self.progress.setValue(int(self.calibrator.single_progress * 100))
            self.pad.set_text(label("Click Once"), "")
            self.primary_button.setText(label("Skip"))
            self.primary_button.setEnabled(True)
        elif self.phase == "double":
            count = self.calibrator.double_clicks
            self.step_row.title.setText("Double-clicks")
            self.step_row.set_detail(note or "Double-click at your usual speed.")
            self.count_label.setText(f"{count} of {REQUIRED_DOUBLE_CLICKS}")
            self.progress.setValue(int(self.calibrator.double_progress * 100))
            self.pad.set_text(label("Double-Click"), "")
            # It finishes by itself at the last double-click; a Finish button
            # here could only lead to "not enough double-clicks".
            self.primary_button.setVisible(False)
        else:
            suggestion = self.suggestion
            self.count_label.setText("")
            if suggestion is None:
                self.step_row.title.setText("Not enough double-clicks")
                self.step_row.set_detail(f"Start over and double-click at least {REQUIRED_DOUBLE_CLICKS} times.")
                self.primary_button.setText(label("Apply"))
                self.primary_button.setEnabled(False)
                return
            self.step_row.title.setText("Calibration complete")
            self.step_row.set_detail("")
            self.recommended_value.setText(f"{suggestion.threshold_ms} ms")
            self.bounce_value.setText(
                "None detected" if suggestion.worst_bounce_ms is None else f"{suggestion.worst_bounce_ms:.0f} ms"
            )
            self.double_value.setText(f"{suggestion.fastest_double_click_ms:.0f} ms")
            # Only speak up when the result needs a caveat or an explanation.
            if not suggestion.confident:
                note = (
                    "Little margin between bounce and your double-clicks. "
                    "Raise the window if bounce still gets through."
                )
            elif suggestion.worst_bounce_ms is None:
                note = (
                    "Your mouse didn’t bounce this time. Bounce comes and goes, so a light "
                    "filter is kept on; the Test pane shows bounce when it happens."
                )
            else:
                note = ""
            self.summary.setText(note)
            self.summary.setVisible(bool(note))
            self.primary_button.setText(label("Apply"))
            self.primary_button.setEnabled(True)


class GeneralPage(Page):
    def __init__(self, controller: AppController, updater=None, parent: Optional[QWidget] = None) -> None:
        super().__init__("General", parent)
        self.controller = controller
        self.updater = updater

        startup_section = self.section()
        self.login_switch = Switch(accessible_name="Open at login")
        # Shown when the user switched the app off in System Settings › Login
        # Items: only there can it be switched back on.
        self.login_items_button = _button("Open Login Items…")
        self.login_items_button.clicked.connect(lambda: startup.open_login_items_settings())
        self.login_items_button.setVisible(False)
        login_controls = QWidget()
        login_layout = QHBoxLayout(login_controls)
        login_layout.setContentsMargins(0, 0, 0, 0)
        login_layout.setSpacing(10)
        login_layout.addWidget(self.login_items_button)
        login_layout.addWidget(self.login_switch)
        self.login_row = startup_section.add(
            Row("Open at login", self._login_detail(startup.ON), login_controls, card_icon("power"))
        )
        self.login_switch.clicked.connect(self._on_login)

        self.permission_row: Optional[Row] = None
        if permissions.needs_accessibility():
            self.header("Permissions")
            section = self.section()
            self.permission_icon = SymbolView("ok", 20, "symbol")
            button = _button("Open Settings…")
            button.clicked.connect(lambda: permissions.open_accessibility_settings())
            self.permission_button = button
            # The name System Settings itself uses, which macOS 27 changed.
            self.permission_row = section.add(Row(permissions.pane_name(), "", button, self.permission_icon))

        self.update_header = self.header("Software update")
        self.update_section = self.section()
        self.auto_check_switch = Switch(accessible_name="Check for updates automatically")
        self.auto_check_switch.clicked.connect(self._on_auto_check)
        self.update_section.add(Row("Check for updates automatically", "", self.auto_check_switch, card_icon("sync")))
        self.auto_install_switch = Switch(accessible_name="Install updates automatically")
        self.auto_install_switch.clicked.connect(self._on_auto_install)
        self.update_section.add(Row("Install updates automatically", "", self.auto_install_switch, card_icon("sync")))
        self.update_button = _button("Check Now")
        self.update_button.clicked.connect(self._on_update_button)
        self.update_row = self.update_section.add(
            Row(f"DoubleClick Fixer {__version__}", "", self.update_button, card_icon("sync"))
        )
        supported = updater is not None and updater.supported
        self.update_header.setVisible(supported)
        self.update_section.setVisible(supported)
        if supported:
            updater.changed.connect(self.refresh_update)

        self.header("Statistics")
        stats = self.section()
        reset = _button("Reset…")
        reset.clicked.connect(self._confirm_reset)
        self.stats_row = stats.add(Row("Blocked bounces", "", reset, card_icon("chart")))

        self.header("Troubleshooting")
        troubleshooting = self.section()
        self.diagnostics_button = _button("Copy Diagnostics")
        self.diagnostics_button.clicked.connect(self._copy_diagnostics)
        self.diagnostics_row = troubleshooting.add(
            Row("Diagnostics", DIAGNOSTICS_DETAIL, self.diagnostics_button, card_icon("copy"))
        )
        self._diagnostics_timer = QTimer(self)
        self._diagnostics_timer.setSingleShot(True)
        self._diagnostics_timer.setInterval(4000)
        self._diagnostics_timer.timeout.connect(lambda: self.diagnostics_row.set_detail(DIAGNOSTICS_DETAIL))

        self.body.addStretch(1)
        version = TextLabel(f"DoubleClick Fixer {__version__}", "caption", "tertiary")
        version.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        version.setContentsMargins(0, 24, 0, 0)
        self.body.addWidget(version)

    @staticmethod
    def _login_detail(state: str) -> str:
        if state == startup.BLOCKED:
            if IS_MAC:
                return "Turned off in System Settings. Turn it on again in Login Items."
            return "Turned off in Task Manager › Startup apps. Turn it on here to allow it again."
        return f"Starts in the {'menu bar' if IS_MAC else 'notification area'}."

    def refresh(self, granted: bool) -> None:
        self.refresh_login()
        if self.permission_row is not None:
            self.permission_icon.name = "ok" if granted else "warning"
            self.permission_icon.update()
            self.permission_row.set_detail("Allowed" if granted else "Not allowed")
        total = self.controller.filtered_total
        self.stats_row.set_detail(f"{total:,} total")
        self.refresh_update()

    def refresh_update(self) -> None:
        updater = self.updater
        if updater is None or not updater.supported:
            return
        self.auto_check_switch.setChecked(updater.auto_check, animate=False)
        self.auto_install_switch.setChecked(updater.auto_install, animate=False)
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
            updater.INSTALLING: ("Installing. DoubleClick Fixer will reopen.", "Update Now", False),
            updater.FAILED: (updater.message, "Try Again", True),
        }
        detail, text, enabled = states.get(updater.state, ("", "Check Now", True))
        self.update_row.set_detail(detail)
        self.update_button.setText(globals()["label"](text))
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

    def refresh_login(self) -> None:
        self.login_switch.setChecked(bool(self.controller.settings["start_at_login"]), animate=False)
        state = self.controller.login_item_state
        self.login_row.set_detail(self._login_detail(state))
        # On macOS only Login Items can switch it back on; the switch here
        # would slide on and snap off again.
        blocked_by_system = IS_MAC and state == startup.BLOCKED
        self.login_items_button.setVisible(blocked_by_system)
        self.login_switch.setEnabled(not blocked_by_system)

    def _on_login(self, checked: bool) -> None:
        error = self.controller.set_start_at_login(checked)
        if error:
            self.login_switch.setChecked(not checked)
            QMessageBox.warning(self, "Couldn’t change the login item", error)
            return
        # Still off if macOS keeps it switched off in Login Items; say so.
        self.refresh_login()

    def _copy_diagnostics(self) -> None:
        """Put a bug report's worth of detail on the clipboard: versions,
        state, settings and the end of the log."""
        window = self.window()
        state = self.controller.diagnostic_state()
        if permissions.needs_accessibility():
            state[f"{permissions.pane_name()} permission"] = getattr(window, "_permission_granted", "?")
        if self.updater is not None and self.updater.supported:
            state["update"] = f"{self.updater.state} {self.updater.message}".strip()
        text = diagnostics.report(__version__, state, self.controller.settings)
        QApplication.clipboard().setText(text)
        self.diagnostics_row.set_detail("Copied. Paste it into your bug report.")
        self._diagnostics_timer.start()

    def _confirm_reset(self) -> None:
        answer = QMessageBox.question(
            self,
            "Reset the count?",
            "Blocked bounces will start again from zero.",
            QMessageBox.StandardButton.Reset | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Reset:
            self.controller.reset_statistics()


# -- window ----------------------------------------------------------------------

class MainWindow(QWidget):
    """Sidebar navigation over four panes."""

    closed_to_tray = Signal()

    def __init__(self, controller: AppController, updater=None) -> None:
        super().__init__()
        self.controller = controller
        self.updater = updater
        set_look(current_look())
        self.setWindowTitle("DoubleClick Fixer")
        self.setWindowIcon(icons.app_icon())
        self.translucent = native.prepare(self)
        self._material = False
        side = look().sidebar_width
        page_margins = 40 if IS_MAC else 72
        # Every pane scrolls, so the minimum height only has to fit the
        # sidebar; Windows at 200% scaling leaves about 490 px.
        self.setMinimumSize(side + page_margins + COLUMN_MIN + 16, 480)
        self.resize(side + page_margins + COLUMN_MAX // (1 if IS_MAC else 2) + 60, 640 if IS_MAC else 700)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.title_bar_height = 52 if IS_MAC else 0
        self.sidebar = Sidebar(list(PAGES), top_inset=self.title_bar_height)
        root.addWidget(self.sidebar)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        self.title_label = TextLabel("", "title")
        if IS_MAC:
            # The pane title sits in the (transparent) toolbar, level with the
            # window controls, as in System Settings.
            bar = QWidget()
            bar.setFixedHeight(self.title_bar_height)
            # Same centred column as the pages, so the title lines up with the
            # content at any window width.
            bar_column = centred_column(bar)
            bar_layout = QHBoxLayout(bar_column)
            bar_layout.setContentsMargins(20, 0, 20, 0)
            bar_layout.addWidget(self.title_label, 0, Qt.AlignmentFlag.AlignVCenter)
            bar_layout.addStretch(1)
            content_layout.addWidget(bar)

        self.filter_page = FilterPage(controller)
        self.test_page = TestPage(controller)
        self.calibrate = CalibratePage(controller)
        self.general = GeneralPage(controller, updater)
        self.pages = [self.filter_page, self.test_page, self.calibrate, self.general]
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
            # The pane colour is painted once by the window; the scroll area,
            # its viewport and the page must not paint over it. The rules use
            # object names so they never reach native controls further down.
            area.setObjectName("paneArea")
            area.viewport().setObjectName("paneViewport")
            page.setObjectName("pane")
            area.setStyleSheet(
                "#paneArea, #paneViewport, #pane { background: transparent; border: none; }"
            )
            area.setWidget(page)
            self.stack.addWidget(area)
        content_layout.addWidget(self.stack, 1)
        root.addWidget(content, 1)

        self.sidebar.current_changed.connect(self._show_page)
        # clicked, not toggled: refresh() sets the switch, and must not echo
        # back into a request.
        self.filter_page.switch.clicked.connect(self._on_switch)
        self.calibrate.threshold_chosen.connect(self._apply_calibration)
        self.calibrate.phase_changed.connect(lambda _phase: self._sync_pause())
        controller.filter_state_changed.connect(self._on_filter_state)
        controller.settings_changed.connect(self.refresh)
        controller.global_event.connect(self.filter_page.note_global_event)
        controller.global_event.connect(self.test_page.note_global_event)
        self.filter_page.calibrate_requested.connect(self.show_calibration)
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

        self._show_page(0)
        if not self._restore_geometry():
            self._fit_to_screen()

    # -- size and position -------------------------------------------------------
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

    def save_geometry(self) -> None:
        encoded = bytes(self.saveGeometry().toBase64()).decode("ascii")
        self.controller.set_window_geometry(encoded)

    # -- native chrome ---------------------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self.translucent and not self._material:
            self._material = native.apply(self, self.sidebar.width(), look().dark)
        self.sidebar.paint_background = not (self.translucent and self._material)
        self._tint_title_bar()
        self._sync_pause()
        self.update()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._sync_pause()

    def paintEvent(self, _event) -> None:  # noqa: N802
        lk = look()
        painter = QPainter(self)
        if self.translucent and self._material:
            if IS_MAC:
                # The sidebar shows the vibrant material; the pane is solid.
                painter.fillRect(
                    QRect(self.sidebar.width(), 0, self.width() - self.sidebar.width(), self.height()),
                    lk.pane,
                )
            # Windows: Mica fills the whole window, as in Windows Settings.
            return
        painter.fillRect(self.rect(), lk.pane)
        if IS_MAC:
            painter.fillRect(QRect(0, 0, self.sidebar.width(), self.height()), lk.sidebar)

    def changeEvent(self, event) -> None:  # noqa: N802
        if event.type() == QEvent.Type.ActivationChange:
            self.sidebar.set_window_active(self.isActiveWindow())
            if self.isActiveWindow() and PAGES[self.stack.currentIndex()][0] == "general":
                # Back from System Settings or Task Manager, where the login
                # item may just have been switched.
                self.controller.refresh_login_item()
                self.refresh()
            # Calibrating in a window left behind another app would leave
            # every click in that app unfiltered.
            self._sync_pause()
        elif event.type() == QEvent.Type.WindowStateChange:
            self._sync_pause()
        if (
            event.type() == QEvent.Type.WindowStateChange
            and not IS_MAC
            and self.isMinimized()
            and QSystemTrayIcon.isSystemTrayAvailable()
        ):
            # Windows: minimizing goes to the notification area, like closing,
            # instead of leaving a taskbar button behind.
            QTimer.singleShot(0, self.close)
        super().changeEvent(event)

    def apply_look(self) -> None:
        """Pick up a light/dark or accent change from the system."""
        set_look(current_look())
        self._tint_title_bar()
        for label in self.findChildren(TextLabel):
            label.restyle()
        for button in self.findChildren(QPushButton):
            if button.isDefault():
                set_primary(button, True)
        for widget in self.findChildren(QWidget):
            widget.update()
        self.update()

    def _tint_title_bar(self) -> None:
        # Mica already runs through the title bar; a solid window tints it.
        native.set_dark_title_bar(self, look().dark, None if self._material else look().pane)

    # Older name, kept for callers.
    apply_palette = apply_look

    # -- navigation --------------------------------------------------------------
    def _show_page(self, index: int) -> None:
        self.sidebar.set_current(index, emit=False)
        self.stack.setCurrentIndex(index)
        self.title_label.setText(PAGES[index][1])
        if PAGES[index][0] == "general":
            self.controller.refresh_login_item()
        self._sync_pause()
        self.refresh()

    def show_page(self, key: str) -> None:
        for index, (name, _title) in enumerate(PAGES):
            if name == key:
                self._show_page(index)

    def show_calibration(self) -> None:
        self.show_page("calibrate")

    # -- calibration pause -------------------------------------------------------
    def _should_pause(self) -> bool:
        """Calibration has to see the raw clicks, so filtering pauses, but
        only while it is measuring in front of the user: not on the intro or
        the result, and not while the window is hidden, minimized or behind
        another app, where those clicks would go unfiltered."""
        return (
            PAGES[self.stack.currentIndex()][0] == "calibrate"
            and self.calibrate.measuring
            and self.isVisible()
            and not self.isMinimized()
            and self.isActiveWindow()
        )

    def _sync_pause(self) -> None:
        if self._should_pause():
            self.controller.suspend()
        else:
            self.controller.resume()

    # -- state -------------------------------------------------------------------
    def refresh(self) -> None:
        granted = self._permission_granted
        waiting = self._enable_when_granted and not self.controller.active
        self.controller.set_waiting_for_permission(waiting)
        self.filter_page.refresh(granted, waiting)
        self.test_page.refresh()
        self.general.refresh(granted)

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

    def _on_permission_lost(self) -> None:
        """The filter found its access withdrawn when macOS disabled its
        tap, let every click through and stopped. Wait for access to come
        back, as when the permission poll is first to notice."""
        if not self.controller.settings["fix_enabled"]:
            return
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
        menu's status line and the Filter pane."""
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

    def _apply_calibration(self, threshold_ms: int) -> None:
        self.controller.set_threshold(threshold_ms)
        self.calibrate.restart()
        self._show_page(0)

    def closeEvent(self, event) -> None:  # noqa: N802
        event.ignore()
        # A calibration left half done, or finished but not applied, starts
        # over next time, so reopening the window never pauses filtering.
        if self.calibrate.phase != "intro":
            self.calibrate.restart()
        self.save_geometry()
        self.controller.flush_stats()
        self.hide()
        self._sync_pause()
        self.closed_to_tray.emit()
