"""The main window: overview, calibration and settings."""

from __future__ import annotations

import platform
from typing import Optional

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import permissions
from ..controller import AppController
from ..core import (
    MAX_THRESHOLD_MS,
    MIN_THRESHOLD_MS,
    REQUIRED_DOUBLE_CLICKS,
    REQUIRED_SINGLE_CLICKS,
    Button,
    Calibrator,
    ClickEvent,
)
from . import icons
from .theme import Palette, stylesheet, system_palette
from .widgets import Card, ClickPad, GapTimeline, ProgressRing, StatTile, ToggleSwitch

#: A pause longer than this starts a new pair while calibrating double-clicks.
PAIR_WINDOW_MS = 600.0


def _row(*widgets: QWidget, spacing: int = 10) -> QHBoxLayout:
    layout = QHBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    for widget in widgets:
        layout.addWidget(widget)
    return layout


class OverviewPage(QWidget):
    """Status, live statistics and a local test pad."""

    def __init__(self, controller: AppController, palette: Palette, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.palette_tokens = palette
        self.clicks = 0
        self.shortest_gap: Optional[float] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        self.banner = QFrame()
        self.banner.setObjectName("banner")
        banner_layout = QHBoxLayout(self.banner)
        banner_layout.setContentsMargins(14, 12, 14, 12)
        self.banner_text = QLabel()
        self.banner_text.setWordWrap(True)
        self.banner_button = QPushButton("Open System Settings")
        self.banner_button.clicked.connect(permissions.open_accessibility_settings)
        banner_layout.addWidget(self.banner_text, 1)
        banner_layout.addWidget(self.banner_button)
        self.banner.hide()
        layout.addWidget(self.banner)

        status = Card()
        self.status_headline = QLabel("Filter is off")
        self.status_headline.setObjectName("statusHeadline")
        self.status_detail = QLabel()
        self.status_detail.setObjectName("hint")
        self.status_detail.setWordWrap(True)
        status.body.addWidget(self.status_headline)
        status.body.addWidget(self.status_detail)

        self.tile_total = StatTile("Bounces blocked", "0")
        self.tile_session = StatTile("This session", "0")
        self.tile_threshold = StatTile("Filter", "60", "ms")
        status.body.addLayout(_row(self.tile_total, self.tile_session, self.tile_threshold))
        layout.addWidget(status)

        test = Card()
        heading = QLabel("Test your mouse")
        heading.setObjectName("sectionTitle")
        test.body.addWidget(heading)
        caption = QLabel(
            "Click the pad the way you normally would. Each bar is the pause between releasing "
            "and pressing again — bounce shows up as a bar below the dashed line."
        )
        caption.setObjectName("hint")
        caption.setWordWrap(True)
        test.body.addWidget(caption)

        self.pad = ClickPad(palette)
        test.body.addWidget(self.pad, 1)
        self.timeline = GapTimeline(palette)
        test.body.addWidget(self.timeline)

        self.tile_last = StatTile("Last gap", "--", "ms")
        self.tile_shortest = StatTile("Shortest gap", "--", "ms")
        self.tile_clicks = StatTile("Clicks measured", "0")
        self.reset_button = QPushButton("Clear")
        self.reset_button.clicked.connect(self.reset)
        row = _row(self.tile_last, self.tile_shortest, self.tile_clicks)
        row.addStretch(1)
        row.addWidget(self.reset_button, alignment=Qt.AlignmentFlag.AlignBottom)
        test.body.addLayout(row)
        layout.addWidget(test, 1)

        self.pad.pressed_with_gap.connect(self._on_pad_press)

    def apply_palette(self, palette: Palette) -> None:
        self.palette_tokens = palette
        self.pad.apply_palette(palette)
        self.timeline.apply_palette(palette)

    def reset(self) -> None:
        self.clicks = 0
        self.shortest_gap = None
        self.timeline.clear()
        self.pad.reset()
        self.tile_last.set_value("--")
        self.tile_shortest.set_value("--")
        self.tile_clicks.set_value("0")

    def _on_pad_press(self, gap_ms: Optional[float], _interval_ms: Optional[float]) -> None:
        self.clicks += 1
        self.tile_clicks.set_value(str(self.clicks))
        if gap_ms is None:
            self.pad.flash(False)
            return
        bounce = gap_ms <= self.controller.threshold_ms
        self.timeline.add(gap_ms, bounce)
        self.tile_last.set_value(f"{gap_ms:.0f}")
        if self.shortest_gap is None or gap_ms < self.shortest_gap:
            self.shortest_gap = gap_ms
            self.tile_shortest.set_value(f"{gap_ms:.0f}")
        self.pad.flash(bounce)

    def refresh(self) -> None:
        active = self.controller.active
        threshold = self.controller.threshold_ms
        names = ", ".join(button.label.lower() for button in self.controller.buttons)
        self.timeline.set_threshold(threshold)
        self.tile_total.set_value(f"{self.controller.filtered_total:,}")
        self.tile_session.set_value(f"{self.controller.session_filtered:,}")
        self.tile_threshold.set_value(str(threshold))

        if active:
            self.status_headline.setText("Filter is on")
            self.status_detail.setText(
                f"A second {names} press arriving within {threshold} ms of the release is "
                "treated as switch bounce and never reaches your apps. Deliberate "
                "double-clicks are untouched."
            )
        else:
            self.status_headline.setText("Filter is off")
            self.status_detail.setText(
                "Your mouse behaves normally. Turn the filter on with the switch in the "
                "top-right corner."
            )

        if permissions.needs_accessibility() and not permissions.has_accessibility():
            self.banner_text.setText(
                "macOS needs Accessibility permission before the filter can block anything. "
                "Add DoubleClick Fixer under Privacy & Security > Accessibility."
            )
            self.banner.show()
        else:
            self.banner.hide()

    def note_global_event(self, event: ClickEvent) -> None:
        if event.is_bounce:
            self.tile_session.set_value(f"{self.controller.session_filtered:,}")
            self.tile_total.set_value(f"{self.controller.filtered_total:,}")


class CalibratePage(QWidget):
    """A two-phase wizard that measures the mouse and suggests a threshold."""

    threshold_chosen = Signal(int)

    def __init__(self, controller: AppController, palette: Palette, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.calibrator = Calibrator()
        self.phase = "intro"
        self.suggestion = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        card = Card()
        header = QHBoxLayout()
        header.setSpacing(18)
        self.ring = ProgressRing(palette)
        header.addWidget(self.ring)
        text = QVBoxLayout()
        text.setSpacing(6)
        self.headline = QLabel()
        self.headline.setObjectName("sectionTitle")
        self.instructions = QLabel()
        self.instructions.setObjectName("hint")
        self.instructions.setWordWrap(True)
        self.feedback = QLabel()
        self.feedback.setWordWrap(True)
        text.addWidget(self.headline)
        text.addWidget(self.instructions)
        text.addWidget(self.feedback)
        text.addStretch(1)
        header.addLayout(text, 1)
        card.body.addLayout(header)

        self.pad = ClickPad(palette)
        card.body.addWidget(self.pad, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.restart_button = QPushButton("Start over")
        self.restart_button.clicked.connect(self.restart)
        self.primary_button = QPushButton("Start calibration")
        self.primary_button.setObjectName("primary")
        self.primary_button.clicked.connect(self._advance)
        buttons.addStretch(1)
        buttons.addWidget(self.restart_button)
        buttons.addWidget(self.primary_button)
        card.body.addLayout(buttons)
        layout.addWidget(card, 1)

        self.pad.pressed_with_gap.connect(self._on_pad_press)
        self.restart()

    def apply_palette(self, palette: Palette) -> None:
        self.pad.apply_palette(palette)
        self.ring.apply_palette(palette)

    # -- flow --------------------------------------------------------------
    def restart(self) -> None:
        self.calibrator = Calibrator()
        self.phase = "intro"
        self.suggestion = None
        self.pad.reset()
        self.feedback.setText("")
        self._render()

    def _advance(self) -> None:
        if self.phase == "intro":
            self.phase = "single"
        elif self.phase == "single":
            self.phase = "double"
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
        self.phase = "done"
        self._render()

    def _on_pad_press(self, gap_ms: Optional[float], _interval_ms: Optional[float]) -> None:
        if self.phase == "single":
            counted = self.calibrator.add_single_click(gap_ms)
            if counted:
                self.feedback.setText("")
                self.pad.flash(False)
            else:
                self.feedback.setText(
                    f"Bounce detected: the button reported a second press {gap_ms:.0f} ms after "
                    "you released it. That is the fault this app filters."
                )
                self.pad.flash(True)
            if self.calibrator.has_enough_singles:
                self.phase = "double"
                self.pad.reset()
                self.feedback.setText("")
        elif self.phase == "double":
            if gap_ms is not None and gap_ms <= PAIR_WINDOW_MS:
                recorded = self.calibrator.add_double_click(gap_ms)
                self.pad.flash(not recorded)
                if not recorded:
                    self.feedback.setText(
                        f"That press came {gap_ms:.0f} ms after the release — too fast to be a "
                        "finger, so it was recorded as bounce. Keep double-clicking normally."
                    )
                else:
                    self.feedback.setText("")
            else:
                self.pad.flash(False)
            if self.calibrator.has_enough_doubles:
                self._finish()
                return
        self._render()

    # -- rendering ---------------------------------------------------------
    def _render(self) -> None:
        self.restart_button.setVisible(self.phase != "intro")
        if self.phase == "intro":
            self.ring.set_progress(0.0, "1 / 2")
            self.headline.setText("Measure your mouse")
            self.instructions.setText(
                "Calibration takes about a minute. First you click once at a time, so any extra "
                "press the mouse invents can be measured. Then you double-click normally, which "
                "sets the limit the filter must stay under. The system-wide filter pauses while "
                "you calibrate so the raw clicks are visible."
            )
            self.pad.set_text("Ready when you are", "Press Start calibration")
            self.primary_button.setText("Start calibration")
            self.primary_button.setEnabled(True)
        elif self.phase == "single":
            done = self.calibrator.single_clicks
            self.ring.set_progress(self.calibrator.single_progress, f"{done}/{REQUIRED_SINGLE_CLICKS}")
            self.headline.setText("Step 1 of 2 — single clicks")
            self.instructions.setText(
                "Click the pad once, pause for about a second, then click again. Do not "
                "double-click. Every extra press the mouse produces is recorded as bounce."
            )
            self.pad.set_text("Click once, then wait", f"{done} of {REQUIRED_SINGLE_CLICKS} counted")
            self.primary_button.setText("Skip to double-clicks")
            self.primary_button.setEnabled(True)
        elif self.phase == "double":
            done = self.calibrator.double_clicks
            self.ring.set_progress(self.calibrator.double_progress, f"{done}/{REQUIRED_DOUBLE_CLICKS}")
            self.headline.setText("Step 2 of 2 — double clicks")
            self.instructions.setText(
                "Double-click the pad the way you would open a file. Go at your natural speed: "
                "this is what the filter is told never to block."
            )
            self.pad.set_text("Double-click here", f"{done} of {REQUIRED_DOUBLE_CLICKS} pairs")
            self.primary_button.setText("Finish")
            self.primary_button.setEnabled(self.calibrator.double_clicks > 0)
        else:
            if self.suggestion is None:
                self.ring.set_progress(0.0, "—")
                self.headline.setText("Not enough double-clicks")
                self.instructions.setText(
                    f"At least {REQUIRED_DOUBLE_CLICKS} deliberate double-click pairs are needed "
                    "to know what must not be blocked. Start over and double-click the pad."
                )
                self.pad.set_text("Calibration incomplete", "Press Start over")
                self.primary_button.setText("Apply")
                self.primary_button.setEnabled(False)
                return
            self.ring.set_progress(1.0, f"{self.suggestion.threshold_ms}")
            self.headline.setText(f"Recommended filter: {self.suggestion.threshold_ms} ms")
            self.instructions.setText(self.suggestion.summary)
            self.pad.set_text(
                "Calibration complete",
                f"{self.suggestion.headroom_ms:.0f} ms of headroom below your fastest double-click",
            )
            self.primary_button.setText("Apply this setting")
            self.primary_button.setEnabled(True)


class SettingsPage(QWidget):
    """Threshold, buttons, startup behaviour and housekeeping."""

    threshold_changed = Signal(int)
    buttons_changed = Signal(list)

    def __init__(self, controller: AppController, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._loading = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        filter_card = Card()
        heading = QLabel("Bounce filter")
        heading.setObjectName("sectionTitle")
        filter_card.body.addWidget(heading)
        hint = QLabel(
            "A press that arrives within this many milliseconds of the previous release is "
            "discarded. Most faulty switches bounce under 30 ms; calibration measures yours."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        filter_card.body.addWidget(hint)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(MIN_THRESHOLD_MS, MAX_THRESHOLD_MS)
        self.slider.setPageStep(5)
        self.spin = QSpinBox()
        self.spin.setRange(MIN_THRESHOLD_MS, MAX_THRESHOLD_MS)
        self.spin.setSuffix(" ms")
        self.slider.valueChanged.connect(self._on_slider)
        self.spin.valueChanged.connect(self._on_spin)
        filter_card.body.addLayout(_row(self.slider, self.spin))
        self.risk_label = QLabel()
        self.risk_label.setObjectName("hint")
        self.risk_label.setWordWrap(True)
        filter_card.body.addWidget(self.risk_label)
        layout.addWidget(filter_card)

        button_card = Card()
        heading = QLabel("Buttons to protect")
        heading.setObjectName("sectionTitle")
        button_card.body.addWidget(heading)
        self.button_boxes: dict[Button, QCheckBox] = {}
        row = QHBoxLayout()
        row.setSpacing(18)
        for button in Button:
            box = QCheckBox(f"{button.label} button")
            box.toggled.connect(self._on_buttons)
            self.button_boxes[button] = box
            row.addWidget(box)
        row.addStretch(1)
        button_card.body.addLayout(row)
        layout.addWidget(button_card)

        system_card = Card()
        heading = QLabel("System")
        heading.setObjectName("sectionTitle")
        system_card.body.addWidget(heading)
        self.login_box = QCheckBox("Start DoubleClick Fixer when I sign in")
        self.login_box.toggled.connect(self._on_login)
        self.minimized_box = QCheckBox("Start hidden in the menu bar / notification area")
        self.minimized_box.toggled.connect(self._on_minimized)
        system_card.body.addWidget(self.login_box)
        system_card.body.addWidget(self.minimized_box)

        if permissions.needs_accessibility():
            permission_row = QHBoxLayout()
            self.permission_label = QLabel()
            self.permission_label.setObjectName("hint")
            self.permission_label.setWordWrap(True)
            permission_button = QPushButton("Open Accessibility settings")
            permission_button.clicked.connect(permissions.open_accessibility_settings)
            permission_row.addWidget(self.permission_label, 1)
            permission_row.addWidget(permission_button)
            system_card.body.addLayout(permission_row)
        else:
            self.permission_label = None

        reset_row = QHBoxLayout()
        self.stats_label = QLabel()
        self.stats_label.setObjectName("hint")
        reset_button = QPushButton("Reset counter")
        reset_button.clicked.connect(self.controller.reset_statistics)
        reset_row.addWidget(self.stats_label, 1)
        reset_row.addWidget(reset_button)
        system_card.body.addLayout(reset_row)
        layout.addWidget(system_card)

        self.about = QLabel()
        self.about.setObjectName("hint")
        self.about.setWordWrap(True)
        self.about.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.about.setOpenExternalLinks(True)
        layout.addWidget(self.about)
        layout.addStretch(1)

    def refresh(self) -> None:
        from .. import __version__
        from .. import settings as settings_store

        self._loading = True
        self.slider.setValue(self.controller.threshold_ms)
        self.spin.setValue(self.controller.threshold_ms)
        for button, box in self.button_boxes.items():
            box.setChecked(button in self.controller.buttons)
        self.login_box.setChecked(bool(self.controller.settings["start_at_login"]))
        self.minimized_box.setChecked(bool(self.controller.settings["start_minimized"]))
        self._loading = False

        self._update_risk(self.controller.threshold_ms)
        self.stats_label.setText(
            f"{self.controller.filtered_total:,} bounces blocked in total, "
            f"{self.controller.session_filtered:,} since this app started."
        )
        if self.permission_label is not None:
            granted = permissions.has_accessibility()
            self.permission_label.setText(
                "Accessibility permission is granted."
                if granted
                else "Accessibility permission is missing, so the filter cannot block anything yet."
            )
        self.about.setText(
            f"Version {__version__} · Settings are stored at {settings_store.settings_path()}"
        )

    def _update_risk(self, value: int) -> None:
        if value <= 40:
            self.risk_label.setText("Very safe: shorter than any deliberate click, but may miss slow bounce.")
        elif value <= 90:
            self.risk_label.setText("Recommended range: catches switch bounce and leaves double-clicks alone.")
        elif value <= 140:
            self.risk_label.setText("Aggressive: fast double-clicks may start to be swallowed.")
        else:
            self.risk_label.setText(
                "Very aggressive: this is long enough to block real double-clicks. Use only if "
                "bounce still gets through at a lower setting."
            )

    def _on_slider(self, value: int) -> None:
        if self._loading:
            return
        self.spin.setValue(value)

    def _on_spin(self, value: int) -> None:
        self._update_risk(value)
        if self._loading:
            return
        self.slider.setValue(value)
        self.threshold_changed.emit(value)

    def _on_buttons(self, _checked: bool) -> None:
        if self._loading:
            return
        selected = [button for button, box in self.button_boxes.items() if box.isChecked()]
        if not selected:
            self._loading = True
            self.button_boxes[Button.LEFT].setChecked(True)
            self._loading = False
            selected = [Button.LEFT]
        self.buttons_changed.emit(selected)

    def _on_login(self, checked: bool) -> None:
        if self._loading:
            return
        error = self.controller.set_start_at_login(checked)
        if error:
            self._loading = True
            self.login_box.setChecked(False)
            self._loading = False
            QMessageBox.warning(self, "Could not change startup", error)

    def _on_minimized(self, checked: bool) -> None:
        if self._loading:
            return
        self.controller.set_start_minimized(checked)


class MainWindow(QWidget):
    """The single window, with a header switch and three pages."""

    closed_to_tray = Signal()

    def __init__(self, controller: AppController) -> None:
        super().__init__()
        self.controller = controller
        self.palette_tokens = system_palette()
        self.setObjectName("root")
        self.setWindowTitle("DoubleClick Fixer")
        self.setWindowIcon(icons.app_icon())
        self.resize(820, 720)
        self.setMinimumSize(640, 600)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(16)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel("DoubleClick Fixer")
        title.setObjectName("title")
        self.subtitle = QLabel()
        self.subtitle.setObjectName("subtitle")
        titles.addWidget(title)
        titles.addWidget(self.subtitle)
        header.addLayout(titles)
        header.addStretch(1)
        self.switch_label = QLabel("Filter off")
        self.switch_label.setObjectName("statusHeadline")
        self.switch = ToggleSwitch(self.palette_tokens)
        self.switch.toggled.connect(self._on_switch)
        header.addWidget(self.switch_label)
        header.addWidget(self.switch)
        layout.addLayout(header)

        nav_frame = QFrame()
        nav_frame.setObjectName("navBar")
        nav_layout = QHBoxLayout(nav_frame)
        nav_layout.setContentsMargins(5, 5, 5, 5)
        nav_layout.setSpacing(4)
        self.nav_group = QButtonGroup(self)
        for index, name in enumerate(("Overview", "Calibrate", "Settings")):
            button = QPushButton(name)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.nav_group.addButton(button, index)
            nav_layout.addWidget(button)
        nav_layout.addStretch(1)
        self.nav_group.idClicked.connect(self._show_page)
        layout.addWidget(nav_frame)

        self.stack = QStackedWidget()
        self.overview = OverviewPage(controller, self.palette_tokens)
        self.calibrate = CalibratePage(controller, self.palette_tokens)
        self.settings_page = SettingsPage(controller)
        for page in (self.overview, self.calibrate, self.settings_page):
            self.stack.addWidget(page)
        layout.addWidget(self.stack, 1)

        self.calibrate.threshold_chosen.connect(self._apply_calibration)
        self.settings_page.threshold_changed.connect(controller.set_threshold)
        self.settings_page.buttons_changed.connect(controller.set_buttons)
        controller.filter_state_changed.connect(self._on_filter_state)
        controller.settings_changed.connect(self.refresh)
        controller.global_event.connect(self.overview.note_global_event)
        controller.hook_failed.connect(self._on_hook_failed)

        self._save_timer = QTimer(self)
        self._save_timer.setInterval(20000)
        self._save_timer.timeout.connect(controller.flush_stats)
        self._save_timer.start()

        self.apply_palette(self.palette_tokens)
        self._show_page(0)
        self.refresh()

    # -- theming -----------------------------------------------------------
    def apply_palette(self, palette: Palette) -> None:
        from .. import settings as settings_store

        self.palette_tokens = palette
        try:
            check = icons.checkmark_file(settings_store.config_dir(), palette.accent_text)
        except OSError:
            check = ""
        self.setStyleSheet(stylesheet(palette, check))
        self.switch.apply_palette(palette)
        self.overview.apply_palette(palette)
        self.calibrate.apply_palette(palette)

    # -- navigation --------------------------------------------------------
    def _show_page(self, index: int) -> None:
        button = self.nav_group.button(index)
        if button is not None:
            button.setChecked(True)
        self.stack.setCurrentIndex(index)
        if index == 1:
            # Calibration must see the real clicks, so pause the filter.
            self.controller.suspend()
        else:
            self.controller.resume()
        self.refresh()

    def show_calibration(self) -> None:
        self._show_page(1)

    # -- state -------------------------------------------------------------
    def refresh(self) -> None:
        active = self.controller.active
        self.switch.setChecked(active)
        self.switch_label.setText("Filter on" if active else "Filter off")
        if self.controller.suspended:
            self.subtitle.setText("Paused during calibration")
        elif not self.controller.supported():
            self.subtitle.setText(f"System-wide filtering is unavailable on {platform.system()}")
        elif active:
            self.subtitle.setText(
                f"Protecting the {', '.join(b.label.lower() for b in self.controller.buttons)} "
                f"button below {self.controller.threshold_ms} ms"
            )
        else:
            self.subtitle.setText("Switch bounce filter for a mouse that clicks twice")
        self.overview.refresh()
        self.settings_page.refresh()

    def _on_switch(self, checked: bool) -> None:
        if checked and self.stack.currentIndex() == 1:
            self._show_page(0)
        self.controller.set_active(checked)
        self.refresh()

    def _on_filter_state(self, active: bool, error: str) -> None:
        self.switch.setChecked(active)
        self.refresh()
        if error:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("Could not turn the filter on")
            box.setText(error)
            if permissions.needs_accessibility() and not permissions.has_accessibility():
                open_button = box.addButton("Open Settings", QMessageBox.ButtonRole.AcceptRole)
                box.addButton(QMessageBox.StandardButton.Close)
                box.exec()
                if box.clickedButton() is open_button:
                    permissions.open_accessibility_settings()
                return
            box.exec()

    def _on_hook_failed(self, message: str) -> None:
        self.controller.set_active(False)
        self.refresh()
        QMessageBox.warning(self, "Filter stopped", message)

    def _apply_calibration(self, threshold_ms: int) -> None:
        self.controller.set_threshold(threshold_ms)
        self._show_page(0)
        QMessageBox.information(
            self,
            "Calibration applied",
            f"The bounce filter is now set to {threshold_ms} ms. Turn the filter on to start "
            "blocking bounce system-wide.",
        )

    def closeEvent(self, event) -> None:  # noqa: N802
        event.ignore()
        self.controller.flush_stats()
        self.hide()
        self.closed_to_tray.emit()

    def open_documentation(self) -> None:
        QDesktopServices.openUrl("https://github.com/arnav-goel10/doubleclick-fixer")
