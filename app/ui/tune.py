"""Test and Tune: one task, in its own window (macOS) or dialog (Windows).

Four steps, one at a time: click the pad, see the verdict, tune the button,
apply. The pad step is 1.0's calibration: isolated clicks, then deliberate
double-clicks, with filtering paused so the pad sees the mouse as it is
(only while a measuring step is in front of the user; the main window keeps
that rule, see MainWindow._should_pause). The verdict says in plain words
whether the button double-clicks on its own; the tune step shows the
recommended setting and a pad to try it on; Apply sets it.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QGuiApplication, QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core import (
    BOUNCE_CANDIDATE_MS,
    MAX_THRESHOLD_MS,
    MIN_THRESHOLD_MS,
    REQUIRED_DOUBLE_CLICKS,
    REQUIRED_SINGLE_CLICKS,
    SIDE_BUTTONS,
    Button,
    Calibrator,
    ClickEvent,
)
from ..inputs import as_button, button_name
from . import icons, menus, native
from .base import label, make_button, set_primary
from .theme import IS_MAC
from .widgets import ClickPad, Row, Section, SymbolView, TextLabel, ValueLabel, look, pop_up_button

#: The longest release-to-press pause that still ends a double-click while
#: measuring, whatever the system's own setting (macOS allows 5 s, which
#: would count two separate slow clicks as a double-click).
PAIR_WINDOW_MAX_MS = 1000.0

#: The windows the tune step offers, in milliseconds (the recommendation is
#: listed too when it is none of these).
TUNE_WINDOWS = (15, 20, 25, 30, 35, 40, 46, 50, 55, 60, 70, 80, 90, 100, 120, 150)

#: The steps, as the window counts them.
STEPS = ("pad", "verdict", "tune", "done")

WINDOW_TITLE = "Test and Tune"


def window_title() -> str:
    return WINDOW_TITLE if IS_MAC else "Test and tune"


class TuneWindow(QDialog):
    """The Test and Tune task. `threshold_chosen` carries Apply's (Button,
    window in ms); `state_changed` tells the main window to look again at
    whether filtering should pause (a step, the window's visibility or its
    activation changed)."""

    threshold_chosen = Signal(object, int)
    phase_changed = Signal(str)
    state_changed = Signal()
    closed = Signal()
    #: The done step's "Turn On": the user wants the filter on.
    filter_requested = Signal(bool)

    def __init__(self, controller, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.controller = controller
        # The layout it was built in, which everything after follows.
        self.mac = IS_MAC
        self.calibrator = Calibrator()
        self.phase = "intro"
        self.suggestion = None
        self.button: Button = Button.LEFT
        # The button whose first press of a double-click waits for its second.
        self._pair_button: Optional[Button] = None
        self.chosen_ms: Optional[int] = None
        self.setWindowTitle(window_title())
        self.setWindowIcon(icons.app_icon())
        self.setModal(False)
        self.setSizeGripEnabled(False)
        flags = self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        if IS_MAC:
            # A task window: closes and minimizes, and has no zoom.
            flags = (
                Qt.WindowType.Window | Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowCloseButtonHint
                | Qt.WindowType.WindowMinimizeButtonHint | Qt.WindowType.CustomizeWindowHint
            )
        self.setWindowFlags(flags)
        self.setFixedSize(*((540, 560) if IS_MAC else (580, 620)))
        self._build()
        self.restart()

    # -- layout -------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.body = QWidget()
        body = QVBoxLayout(self.body)
        body.setContentsMargins(*((20, 18, 20, 8) if IS_MAC else (24, 24, 24, 16)))
        body.setSpacing(0)
        self.step_label = TextLabel("", "caption", "secondary")
        self.title = TextLabel("", "title" if IS_MAC else "large")
        self.title.setWordWrap(True)
        self.detail = TextLabel("", "body", "secondary" if IS_MAC else "text")
        self.detail.setWordWrap(True)
        body.addWidget(self.step_label)
        body.addSpacing(4)
        body.addWidget(self.title)
        body.addSpacing(6)
        body.addWidget(self.detail)
        body.addSpacing(14)
        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        root.addWidget(self.body, 1)

        self.stack.addWidget(self._pad_step())
        self.stack.addWidget(self._verdict_step())
        self.stack.addWidget(self._tune_step())
        self.stack.addWidget(self._done_step())

        self.footer = QWidget()
        footer = QHBoxLayout(self.footer)
        footer.setContentsMargins(*((20, 8, 20, 18) if IS_MAC else (24, 24, 24, 24)))
        footer.setSpacing(8)
        self.secondary_button = make_button("Cancel")
        self.primary_button = make_button("Continue", default=True)
        self.secondary_button.clicked.connect(self._secondary)
        self.primary_button.clicked.connect(self._primary)
        if IS_MAC:
            footer.addStretch(1)
            footer.addWidget(self.secondary_button)
            footer.addWidget(self.primary_button)
        else:
            # A ContentDialog: the primary button first, the buttons sharing
            # the width.
            footer.addWidget(self.primary_button, 1)
            footer.addWidget(self.secondary_button, 1)
        root.addWidget(self.footer)

    def _pad_step(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        picker = Section()
        self.button_picker = QComboBox()
        for button in Button:
            self.button_picker.addItem(button_name(button), button.value)
        self.button_picker.setAccessibleName("Button to test")
        pop_up_button(self.button_picker)
        # activated, not currentIndexChanged: only the user's own choice.
        self.button_picker.activated.connect(lambda index: self.set_button(list(Button)[index]))
        self.picker_row = picker.add(Row("Button", "", self.button_picker))
        layout.addWidget(picker)

        step = Section()
        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.setSpacing(0)
        self.count_label = ValueLabel()
        self.step_row = Row("", "", self.count_label)
        holder_layout.addWidget(self.step_row)
        self.progress_row = QWidget()
        progress_layout = QVBoxLayout(self.progress_row)
        progress_layout.setContentsMargins(*((12, 0, 12, 12) if IS_MAC else (16, 0, 16, 16)))
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setAccessibleName("Test progress")
        progress_layout.addWidget(self.progress)
        holder_layout.addWidget(self.progress_row)
        step.add(holder)
        layout.addWidget(step)

        self.pad = ClickPad()
        self.pad.setAccessibleName("Test pad")
        layout.addWidget(self.pad, 1)
        self.pad.pressed_with_gap.connect(self._on_pad_press)
        return page

    def _verdict_step(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        result = Section()
        self.verdict_icon = SymbolView("ok", 22, "symbol")
        self.verdict_row = result.add(Row(" ", " ", None, self.verdict_icon))
        self.double_value = ValueLabel()
        self.double_row = result.add(Row("Your fastest double-click", "", self.double_value))
        layout.addWidget(result)
        # What the filter has seen in use, apart from what the pad just saw.
        self.health_heading = TextLabel("In everyday use", "headline")
        self.health_heading.setContentsMargins(2 if IS_MAC else 0, 10, 0, 0)
        layout.addWidget(self.health_heading)
        self.health_section = Section()
        self.health_icon = SymbolView("history", 20, "secondary")
        self.health_row = self.health_section.add(Row(" ", "", None, self.health_icon))
        self._health_kind = ""
        layout.addWidget(self.health_section)
        self.conflict_section = Section()
        layout.addWidget(self.conflict_section)
        layout.addStretch(1)
        return page

    def _tune_step(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        choice = Section()
        self.window_picker = QComboBox()
        self.window_picker.setAccessibleName("Ignore a second click within")
        pop_up_button(self.window_picker)
        self.window_picker.activated.connect(self._on_window_chosen)
        self.window_row = choice.add(Row(label("Ignore a second click within"), "", self.window_picker))
        layout.addWidget(choice)
        self.try_pad = ClickPad()
        self.try_pad.setAccessibleName("Try pad")
        self.try_pad.pressed_with_gap.connect(self._on_try_press)
        layout.addWidget(self.try_pad, 1)
        self.try_note = TextLabel("", "caption", "secondary")
        self.try_note.setWordWrap(True)
        layout.addWidget(self.try_note)
        return page

    def _done_step(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        result = Section()
        self.result_value = ValueLabel("Calibrated")
        self.result_row = result.add(Row(" ", " ", self.result_value, SymbolView("ok", 22, "symbol")))
        layout.addWidget(result)
        self.filter_section = Section()
        self.turn_on_button = make_button("Turn On")
        self.turn_on_button.setAccessibleName("Turn on Bounce Filter")
        self.turn_on_button.clicked.connect(lambda: self.filter_requested.emit(True))
        self.filter_row = self.filter_section.add(
            Row(label("Bounce Filter is off"), "Turn it on to start ignoring the extra clicks.", self.turn_on_button)
        )
        layout.addWidget(self.filter_section)
        layout.addStretch(1)
        return page

    # -- flow ---------------------------------------------------------------------
    @property
    def measuring(self) -> bool:
        return self.phase in ("single", "double")

    @property
    def step(self) -> str:
        if self.phase in ("intro", "single", "double"):
            return "pad"
        return self.phase

    def _set_phase(self, phase: str) -> None:
        if phase != self.phase:
            self.phase = phase
            self.phase_changed.emit(phase)
            self.state_changed.emit()

    @property
    def _side(self) -> bool:
        return self.button in SIDE_BUTTONS

    def set_button(self, button: object) -> None:
        """Test `button` from now on, starting afresh if it is another."""
        button = as_button(button)
        self.button_picker.setCurrentIndex(list(Button).index(button))
        if button is not self.button:
            self.button = button
            self.restart()

    def open_for(self, button: object = None) -> None:
        """Bring the window up, at the pad, testing `button` if one is named."""
        if self.phase in ("done",):
            self.restart()
        if button is not None:
            self.set_button(button)

    def restart(self) -> None:
        self.calibrator = Calibrator()
        self.suggestion = None
        self.chosen_ms = None
        self._pair_button = None
        self.pad.reset()
        self.try_pad.reset()
        self._set_phase("intro")
        self._render()

    def _advance(self) -> None:
        """The pad's phases: from the intro to single clicks, from single
        clicks to double-clicks (skipping the rest), and from those to the
        verdict."""
        if self.phase == "intro":
            self._set_phase("single")
        elif self.phase == "single":
            self._pair_button = None
            self._set_phase("double")
        elif self.phase == "double":
            self._finish()
            return
        self.pad.reset()
        self._render()

    def _finish(self) -> None:
        self.suggestion = self.calibrator.suggest()
        if self.suggestion is not None:
            self.chosen_ms = self.suggestion.threshold_ms
        self._set_phase("verdict")
        self._render()

    def show_tune(self) -> None:
        if self.suggestion is None:
            return
        self._set_phase("tune")
        self.try_pad.reset()
        self._render()

    def apply(self) -> None:
        """Set the chosen window for the button tested, marked as measured,
        and filter it, since it was worth measuring. The fastest double-click
        goes with it: a later suggestion never comes near it."""
        if self.suggestion is None or self.chosen_ms is None:
            return
        button = self.button
        self.controller.apply_calibration(button, int(self.chosen_ms), self.suggestion.fastest_double_click_ms)
        self.threshold_chosen.emit(button, int(self.chosen_ms))
        self._set_phase("done")
        self._render()

    def _primary(self) -> None:
        if self.step == "verdict":
            self.show_tune()
        elif self.step == "tune":
            self.apply()
        elif self.step == "done":
            self.close()

    def _secondary(self) -> None:
        step = self.step
        if step == "pad":
            if self.phase == "intro":
                self.close()
            else:
                self.restart()
        elif step == "verdict":
            self.restart()
        elif step == "tune":
            self._set_phase("verdict")
            self._render()
        elif step == "done":
            self.restart()

    @staticmethod
    def pair_window_ms() -> float:
        """How long a double-click may pause between its release and second
        press: the system's own double-click setting, up to PAIR_WINDOW_MAX_MS."""
        interval = QGuiApplication.styleHints().mouseDoubleClickInterval()
        return min(float(interval), PAIR_WINDOW_MAX_MS)

    # -- the pad -----------------------------------------------------------------
    def _on_pad_press(self, gap_ms: Optional[float], _interval_ms: Optional[float], button: Button = Button.LEFT) -> None:
        note = ""
        button = as_button(button)
        if button is not self.button:
            if self.measuring or self.phase == "intro":
                self._render(
                    f"That was the {button_name(button).lower()} button. To test it, choose it above; "
                    f"this tests the {button_name(self.button).lower()} button."
                )
            return
        if self.phase == "intro":
            # Clicking the pad starts the test, and counts as its first
            # single click; its gap is against a click before the start.
            self._advance()
            gap_ms = None
        if self.phase == "single":
            counted = self.calibrator.add_single_click(gap_ms)
            self.pad.flash(not counted)
            if not counted:
                note = f"Extra click caught ({gap_ms:.0f} ms after letting go)."
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
            return f"Extra click caught ({gap_ms:.0f} ms after letting go)."
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
            if self._side:
                return "Too slow for a repeat, so it wasn’t counted. Press a little faster."
            return "Too slow for a double-click, so it wasn’t counted. Double-click a little faster."
        return ""

    def _on_try_press(self, gap_ms: Optional[float], _interval_ms: Optional[float], button: Button = Button.LEFT) -> None:
        """The tune step's pad: does a click at this gap count, with the
        chosen window?"""
        button = as_button(button)
        if button is not self.button or gap_ms is None or self.chosen_ms is None:
            self.try_pad.flash(False, neutral=True)
            return
        if gap_ms <= self.chosen_ms:
            self.try_pad.flash(True)
            self.try_note.setText(f"{gap_ms:.0f} ms after letting go: ignored as an extra click.")
        elif gap_ms <= self.pair_window_ms():
            self.try_pad.flash(False)
            kind = "repeat" if self._side else "double-click"
            self.try_note.setText(f"A {kind} {gap_ms:.0f} ms apart: it counts.")
        else:
            self.try_pad.flash(False, neutral=True)

    def note_global_event(self, event: ClickEvent) -> None:
        """A bounce the running filter blocked while the try pad is up:
        proof the setting works, on the pad."""
        if self.isVisible() and self.step == "tune" and event.is_bounce and as_button(event.button) is self.button:
            self.try_pad.flash(True)
            if event.gap_ms is not None:
                self.try_note.setText(f"An extra click {event.gap_ms:.0f} ms after letting go was blocked.")

    def _on_window_chosen(self, index: int) -> None:
        value = self.window_picker.itemData(index)
        if value is not None:
            self.chosen_ms = int(value)
            self._render_tune()

    # -- rendering ---------------------------------------------------------------
    def _render(self, note: str = "") -> None:
        step = self.step
        self.stack.setCurrentIndex(STEPS.index(step))
        number = {"pad": 1, "verdict": 2, "tune": 3, "done": 3}[step]
        self.step_label.setText(f"Step {number} of 3")
        self.step_label.setVisible(step != "done")
        name = button_name(self.button).lower()
        self.button_picker.setEnabled(not self.measuring)
        if step == "pad":
            self._render_pad(name, note)
        elif step == "verdict":
            self._render_verdict(name)
        elif step == "tune":
            self._render_tune()
        else:
            self._render_done(name)

    def _buttons(self, primary: Optional[str], secondary: str) -> None:
        self.primary_button.setVisible(primary is not None)
        if primary is not None:
            self.primary_button.setText(label(primary))
        self.secondary_button.setText(label(secondary))
        set_primary(self.primary_button, primary is not None)

    def _render_pad(self, name: str, note: str) -> None:
        side = self._side
        self.title.setText("Click the pad")
        if self.phase == "intro":
            self.detail.setText(
                (f"Press the {name} button on the pad once at a time, then twice quickly. " if side
                 else f"Click the {name} button on the pad once at a time, then double-click it. ")
                + "Filtering pauses while you do, so the pad sees your mouse as it is."
            )
            self.step_row.title.setText("Ready when you are")
            self.step_row.set_detail(note or "The test starts with your first click.")
            self.count_label.setText("")
            self.progress_row.setVisible(False)
            self.pad.set_text(label("Click Here"), "")
            self._buttons(None, "Cancel")
            return
        self.progress_row.setVisible(True)
        if self.phase == "single":
            count = self.calibrator.single_clicks
            self.detail.setText(
                "Press once, then pause, until the bar is full." if side else "Click once, then pause, until the bar is full."
            )
            self.step_row.title.setText("Single presses" if side else "Single clicks")
            self.count_label.setText(f"{count} of {REQUIRED_SINGLE_CLICKS}")
            self.progress.setValue(int(self.calibrator.single_progress * 100))
            self.pad.set_text(label("Press Once" if side else "Click Once"), "")
        else:
            count = self.calibrator.double_clicks
            self.detail.setText(
                "Now press twice quickly, as when going back two pages." if side
                else "Now double-click at your usual speed."
            )
            self.step_row.title.setText("Repeats" if side else "Double-clicks")
            self.count_label.setText(f"{count} of {REQUIRED_DOUBLE_CLICKS}")
            self.progress.setValue(int(self.calibrator.double_progress * 100))
            self.pad.set_text(label("Press Twice" if side else "Double-Click"), "")
        self.step_row.set_detail(note)
        self._buttons(None, "Start Over")

    def _render_verdict(self, name: str) -> None:
        suggestion = self.suggestion
        side = self._side
        self._buttons("Continue", "Test Again")
        if suggestion is None:
            self.title.setText("Not enough repeats" if side else "Not enough double-clicks")
            self.detail.setText(
                f"Test again and {'press twice quickly' if side else 'double-click'} at least "
                f"{REQUIRED_DOUBLE_CLICKS} times."
            )
            self.primary_button.setVisible(False)
            for widget in (self.verdict_row, self.double_row):
                widget.setVisible(False)
            self.health_section.setVisible(False)
            self.health_heading.setVisible(False)
            self.conflict_section.setVisible(False)
            return
        for widget in (self.verdict_row, self.double_row):
            widget.setVisible(True)
        clicks = self.calibrator.single_clicks + 2 * self.calibrator.double_clicks
        extra = suggestion.bounces_seen
        if extra:
            self.title.setText(f"Your {name} button {'repeats' if side else 'double-clicks'} on its own")
            self.detail.setText(
                f"It sent {extra} extra click{'s' if extra != 1 else ''} as you clicked. "
                "Tuning it ignores clicks that come that soon after letting go."
            )
            self.verdict_icon.name = "warning"
            self.verdict_row.title.setText(f"{extra} extra click{'s' if extra != 1 else ''} in {clicks} clicks")
            self.verdict_row.set_detail(f"Each came within {suggestion.worst_bounce_ms:.0f} ms of letting go.")
        else:
            self.title.setText("No bounce seen")
            self.detail.setText(
                "Your mouse may be fine, or it bounces only now and then. "
                "A light setting catches it when it does."
            )
            self.verdict_icon.name = "ok"
            self.verdict_row.title.setText(f"No extra clicks in {clicks} clicks")
            self.verdict_row.set_detail("Bounce comes and goes; History shows it when it happens.")
        self.verdict_icon.update()
        kind = "repeat" if side else "double-click"
        self.double_row.title.setText(f"Your fastest {kind}")
        self.double_value.setText(f"{suggestion.fastest_double_click_ms:.0f} ms apart")
        self._render_health()
        self._render_conflicts()

    def _render_health(self) -> None:
        """What the filter has seen in use (the controller's health check),
        under what the pad just saw."""
        health = self.controller.health()
        self._health_kind = getattr(health, "kind", "")
        self.health_section.setVisible(bool(health.verdict))
        self.health_heading.setVisible(bool(health.verdict))
        if health.verdict:
            self.health_row.title.setText(health.verdict)
            self.health_row.set_detail(health.detail)
            self.health_icon.name = "warning" if self._health_kind in ("bouncing", "conflict") else "history"
            self.health_icon.tone = "symbol" if self.health_icon.name == "warning" else "secondary"
            self.health_icon.update()

    def _render_conflicts(self) -> None:
        self.conflict_section.clear()
        # The health check already names them when they are its verdict.
        conflicts = [] if self._health_kind == "conflict" else list(self.controller.conflicts())
        for conflict in conflicts:
            self.conflict_section.add(
                Row(
                    f"Another app may be changing clicks: {conflict.name}",
                    menus.conflict_detail(conflict),
                    None,
                    SymbolView("warning", 20, "symbol"),
                )
            )
        self.conflict_section.setVisible(bool(conflicts))

    def _render_tune(self) -> None:
        suggestion = self.suggestion
        if suggestion is None:
            return
        name = button_name(self.button).lower()
        side = self._side
        self._buttons("Apply", "Back")
        self.title.setText(f"Tune the {name} button")
        self.detail.setText(
            "Choose how soon after letting go a second click is ignored. "
            f"The recommended setting stays clear of your {'repeats' if side else 'double-clicks'}."
        )
        recommended = suggestion.threshold_ms
        chosen = self.chosen_ms if self.chosen_ms is not None else recommended
        values = sorted({*TUNE_WINDOWS, recommended, chosen})
        values = [value for value in values if MIN_THRESHOLD_MS <= value <= MAX_THRESHOLD_MS]
        self.window_picker.blockSignals(True)
        self.window_picker.clear()
        for value in values:
            text = f"{value} ms" + (" (recommended)" if value == recommended else "")
            self.window_picker.addItem(text, value)
        self.window_picker.setCurrentIndex(values.index(chosen))
        self.window_picker.blockSignals(False)
        kind = "repeat" if side else "double-click"
        fastest = suggestion.fastest_double_click_ms
        if chosen >= fastest:
            detail = f"Too long: your fastest {kind} ({fastest:.0f} ms apart) would be ignored."
        elif not suggestion.confident and chosen == recommended:
            detail = (
                f"Little room between the extra clicks and your {kind}s. "
                "If extra clicks still get through, choose a little more."
            )
        else:
            detail = f"Your fastest {kind}, {fastest:.0f} ms apart, still counts."
        self.window_row.set_detail(detail)
        self.try_pad.set_text(label("Press Twice Here" if side else "Try a Double-Click"), "")
        if not self.try_note.text():
            self.try_note.setText("Try it on the pad: a real double-click should count, a bounce should not.")

    def _render_done(self, name: str) -> None:
        self._buttons("Done", "Test Another Button")
        self.title.setText(f"The {name} button is tuned")
        self.detail.setText(f"{label('Bounce Filter')} uses this from now on.")
        self.result_row.title.setText(f"{button_name(self.button)} button")
        self.result_row.set_detail(f"Ignores a second click within {self.chosen_ms} ms of letting go.")
        self.filter_section.setVisible(not self.controller.wanted)

    def refresh(self) -> None:
        """The controller changed (the filter went on or off)."""
        if self.step == "done":
            self._render()

    def apply_look(self) -> None:
        for text in self.findChildren(TextLabel):
            text.restyle()
        for button in self.findChildren(QPushButton):
            if button.isDefault():
                set_primary(button, True)
        native.set_dark_title_bar(self, look().dark, look().pane)
        self.update()

    # -- window events -------------------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        native.set_dark_title_bar(self, look().dark, look().pane)
        if self.mac:
            # No control rung at the start, as an AppKit window opens.
            self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.state_changed.emit()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self.state_changed.emit()

    def changeEvent(self, event) -> None:  # noqa: N802
        if event.type() in (QEvent.Type.ActivationChange, QEvent.Type.WindowStateChange):
            # Testing in a window left behind another app would leave every
            # click in that app unfiltered.
            self.state_changed.emit()
        super().changeEvent(event)

    def done(self, result: int) -> None:  # noqa: D401 - QDialog's close, Escape and the rest
        super().done(result)
        self._closed()

    def _closed(self) -> None:
        # A test left half done, or a result not applied, starts over next
        # time, so opening the window again never pauses filtering at once.
        if self.phase != "intro":
            self.restart()
        self.state_changed.emit()
        self.closed.emit()

    def paintEvent(self, _event) -> None:  # noqa: N802
        if self.mac:
            return
        # A ContentDialog: the content on a lighter layer, the buttons below
        # on the window's own colour.
        lk = look()
        painter = QPainter(self)
        painter.fillRect(self.rect(), lk.pane)
        layer = lk.pane.lighter(104) if not lk.dark else lk.pane.lighter(125)
        painter.fillRect(self.body.geometry(), layer)
        painter.setPen(lk.section_border)
        y = self.body.geometry().bottom()
        painter.drawLine(0, y, self.width(), y)
