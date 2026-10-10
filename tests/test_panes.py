"""Offscreen tests for 1.0's panes: button rows, wheel fix, per-button
calibration, History, Apps and Devices."""

try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

import json
import logging
import os
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# The app logs failures on purpose; keep them out of the test output.
logging.getLogger("app").addHandler(logging.NullHandler())
logging.getLogger("app").propagate = False

from run import _unhide_qt_plugins

_unhide_qt_plugins()

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication


class StandInFilter:
    """The 1.0 filter interface, without a hook."""

    def __init__(self, config, on_event=None, on_error=None, permission_ok=None,
                 on_permission_lost=None, on_device=None, on_wheel=None) -> None:
        self.config = config
        self.on_event = on_event
        self.on_device = on_device
        self.on_wheel = on_wheel
        self.updates = []
        self.started = self.stopped = False

    @property
    def running(self) -> bool:
        return self.started and not self.stopped

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def update(self, config) -> None:
        self.updates.append(config)

    def seen_devices(self) -> list:
        return []


class PaneTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from app import settings

        self.directory = Path(tempfile.mkdtemp())
        patches = [
            mock.patch.object(settings, "config_dir", return_value=self.directory),
            mock.patch.object(settings, "LEGACY_PATH", self.directory / "absent.json"),
            mock.patch("app.controller.GlobalClickFilter", StandInFilter),
            mock.patch("app.permissions.needs_accessibility", return_value=False),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        from app.controller import AppController
        from app.ui.window import MainWindow

        self.controller = AppController()
        self.window = MainWindow(self.controller)
        self.window.isActiveWindow = lambda: True
        self.window.setMinimumSize(300, 200)
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.stop_timers)

    def stop_timers(self) -> None:
        from PySide6.QtCore import QTimer

        for timer in self.window.findChildren(QTimer):
            timer.stop()
        self.window.hide()

    def show(self, key: str) -> None:
        self.window.show()
        self.window.show_page(key)
        self.application.processEvents()

    def hook(self) -> StandInFilter:
        self.assertTrue(self.controller.set_active(True))
        return self.controller._filter


class FilterPaneTests(PaneTestCase):
    def test_a_row_per_button_with_its_window_and_switch(self) -> None:
        from app.core import Button

        page = self.window.filter_page
        self.assertEqual(set(page.button_rows), set(Button))
        self.assertEqual([row.title.text() for row in page.button_rows.values()],
                         ["Left button", "Right button", "Middle button", "Back button", "Forward button"])
        for button in Button:
            self.assertEqual(page.window_boxes[button].value(), 46)
            self.assertEqual(page.window_boxes[button].isEnabled(), button is Button.LEFT, "only filtered buttons")
            self.assertIn("Calibrate", page.button_rows[button].detail.text())

    def test_each_window_reaches_its_own_button(self) -> None:
        from app.core import Button

        hook = self.hook()
        page = self.window.filter_page
        page.button_switches[Button.BACK].click()
        self.assertIn(Button.BACK, self.controller.buttons)
        self.assertTrue(page.window_boxes[Button.BACK].isEnabled())
        page.window_boxes[Button.BACK].setValue(25)
        self.assertEqual(self.controller.threshold_for(Button.BACK), 25)
        self.assertEqual(self.controller.threshold_for(Button.LEFT), 46)
        self.assertEqual(hook.updates[-1].thresholds[Button.BACK], 25)

    def test_a_refresh_never_echoes_into_a_change(self) -> None:
        with mock.patch.object(self.controller, "set_threshold") as set_threshold, \
                mock.patch.object(self.controller, "set_wheel_fix") as set_wheel_fix:
            self.window.refresh()
        set_threshold.assert_not_called()
        set_wheel_fix.assert_not_called()

    def test_the_calibrate_link_opens_calibration_for_that_button(self) -> None:
        from app.core import Button

        self.show("filter")
        self.window.filter_page.button_rows[Button.FORWARD].detail.linkActivated.emit("calibrate")
        self.assertIs(self.window.pages[self.window.stack.currentIndex()], self.window.calibrate)
        self.assertIs(self.window.calibrate.button, Button.FORWARD)
        self.assertEqual(self.window.calibrate.button_picker.currentText(), "Forward")

    def test_a_calibrated_button_says_so(self) -> None:
        from app.core import Button

        self.controller.set_calibrated(Button.RIGHT)
        detail = self.window.filter_page.button_rows[Button.RIGHT].detail.text()
        self.assertTrue(detail.startswith("Calibrated."), detail)
        self.assertTrue(self.window.filter_page.button_rows[Button.LEFT].detail.text().startswith("Not calibrated."))
        self.assertEqual(self.window.filter_page.button_rows[Button.RIGHT].detail.accessibleDescription(), "Calibrated.")

    def test_each_calibrate_link_says_which_button_it_is_for(self) -> None:
        from app.core import Button

        names = [row.detail.accessibleName() for row in self.window.filter_page.button_rows.values()]
        self.assertEqual(names, ["Calibrate the left button", "Calibrate the right button", "Calibrate the middle button",
                                 "Calibrate the back button", "Calibrate the forward button"])
        self.assertEqual(self.window.filter_page.button_rows[Button.LEFT].detail.accessibleDescription(), "Not calibrated.")

    def test_the_wheel_fix_row(self) -> None:
        hook = self.hook()
        page = self.window.filter_page
        self.assertFalse(page.wheel_switch.isChecked())
        self.assertFalse(page.wheel_box.isEnabled())
        page.wheel_switch.click()
        self.assertTrue(self.controller.wheel_fix)
        self.assertTrue(page.wheel_box.isEnabled())
        page.wheel_box.setValue(35)
        self.assertEqual(self.controller.wheel_window_ms, 35)
        self.assertTrue(hook.updates[-1].wheel_fix)
        self.assertEqual(hook.updates[-1].wheel_window_ms, 35)


    def wheel(self, widget, notches: int) -> None:
        """Turn the wheel over the middle of `widget` the way the system does
        (QTest.wheelEvent goes through the window, so Qt picks the widget
        under the pointer, gives focus by policy and passes an ignored turn
        on to the parents)."""
        from PySide6.QtCore import QPoint, QPointF
        from PySide6.QtTest import QTest

        centre = widget.mapTo(self.window, QPoint(widget.width() // 2, widget.height() // 2))
        QTest.wheelEvent(self.window.windowHandle(), QPointF(centre), QPoint(0, 120 * notches))
        self.application.processEvents()

    def scrolling_pane(self, key: str, height: int = 480):
        """The pane `key` on screen at `height` (480: the window's minimum),
        where it has to scroll, and its scroll area."""
        self.show(key)
        self.window.resize(self.window.width(), height)
        self.application.processEvents()
        area = self.window.stack.currentWidget()
        self.assertGreater(area.verticalScrollBar().maximum(), 0, f"the {key} pane scrolls at this size")
        return area

    def test_scrolling_over_a_window_box_scrolls_the_pane_and_keeps_the_window(self) -> None:
        from app.core import Button

        hook = self.hook()
        self.controller.set_buttons(list(Button))  # every box enabled: the worst case
        self.controller.set_wheel_fix(True)
        area = self.scrolling_pane("filter")
        page = self.window.filter_page
        bar = area.verticalScrollBar()
        saved = (self.directory / "settings.json").read_text()
        updates = len(hook.updates)
        for box in [*page.window_boxes.values(), page.wheel_box]:
            name = box.accessibleName()
            self.assertNotEqual(box.focusPolicy(), Qt.FocusPolicy.WheelFocus, f"{name}: the wheel gives no focus")
            area.ensureWidgetVisible(box)
            self.application.processEvents()
            before, position = box.value(), bar.value()
            notches = 1 if position == bar.maximum() else -1
            self.wheel(box, notches)
            self.assertEqual(box.value(), before, name)
            self.assertFalse(box.hasFocus(), name)
            self.assertNotEqual(bar.value(), position, f"{name}: the turn scrolls the pane instead")
        self.assertEqual([self.controller.threshold_for(button) for button in Button], [46] * 5)
        self.assertEqual(self.controller.wheel_window_ms, 50)
        self.assertEqual((self.directory / "settings.json").read_text(), saved, "nothing saved")
        self.assertEqual(len(hook.updates), updates, "nothing reached the filter")

    def test_the_wheel_still_changes_a_window_box_with_focus(self) -> None:
        from app.core import Button

        self.scrolling_pane("filter")
        box = self.window.filter_page.window_boxes[Button.LEFT]
        self.window.activateWindow()
        box.setFocus(Qt.FocusReason.MouseFocusReason)
        self.application.processEvents()
        self.assertTrue(box.hasFocus())
        self.wheel(box, -1)
        self.assertEqual(box.value(), 45)
        self.assertEqual(self.controller.threshold_for(Button.LEFT), 45, "chosen on purpose, so saved")

    def writes(self):
        """Count the writes of settings.json, which really happen."""
        from app import settings

        return mock.patch.object(settings, "write_json", wraps=settings.write_json)

    def stored(self) -> dict:
        return json.loads((self.directory / "settings.json").read_text())

    def test_a_run_of_steps_is_one_write_and_the_filter_has_each_step(self) -> None:
        from PySide6.QtTest import QTest

        from app.controller import SAVE_DELAY_MS
        from app.core import Button

        hook = self.hook()
        box = self.window.filter_page.window_boxes[Button.LEFT]
        before = len(hook.updates)
        with self.writes() as write:
            for _ in range(10):
                box.stepUp()
            self.assertEqual(box.value(), 56)
            self.assertEqual(self.controller.threshold_for(Button.LEFT), 56)
            self.assertEqual(len(hook.updates) - before, 10, "the filter follows every step at once")
            self.assertEqual(hook.updates[-1].thresholds[Button.LEFT], 56)
            write.assert_not_called()
            self.assertEqual(self.stored()["thresholds"]["left"], 46, "the file waits")
            QTest.qWait(SAVE_DELAY_MS + 400)
            write.assert_called_once()
        self.assertEqual(self.stored()["thresholds"]["left"], 56, "the last step is what is saved")
        self.assertEqual(SAVE_DELAY_MS, 300)

    def test_a_run_of_steps_in_the_wheel_box_is_one_write_too(self) -> None:
        from PySide6.QtTest import QTest

        from app.controller import SAVE_DELAY_MS
        from app.settings import WHEEL_MAX_MS

        box = self.window.filter_page.wheel_box
        start = box.value()
        with self.writes() as write:
            for _ in range(10):
                box.stepUp()
            self.assertEqual(self.controller.wheel_window_ms, min(start + 10, WHEEL_MAX_MS))
            write.assert_not_called()
            QTest.qWait(SAVE_DELAY_MS + 400)
            write.assert_called_once()
        self.assertEqual(self.stored()["wheel_window_ms"], start + 10)

    def test_a_pause_between_steps_writes_each_run(self) -> None:
        from app.core import Button

        box = self.window.filter_page.window_boxes[Button.LEFT]
        with self.writes() as write:
            box.stepUp()
            self.controller.flush_settings()  # the wait is over
            box.stepUp()
            self.controller.flush_settings()
            self.controller.flush_settings()  # nothing held: nothing written
            self.assertEqual(write.call_count, 2)
        self.assertEqual(self.stored()["thresholds"]["left"], 48)

    def test_closing_the_window_writes_what_is_waiting(self) -> None:
        from PySide6.QtCore import QEvent
        from PySide6.QtTest import QTest

        from app.controller import SAVE_DELAY_MS
        from app.core import Button

        self.window.save_geometry()  # closing then has nothing else to write
        box = self.window.filter_page.window_boxes[Button.LEFT]
        with self.writes() as write:
            for _ in range(3):
                box.stepUp()
            write.assert_not_called()
            self.window.closeEvent(QEvent(QEvent.Type.Close))
            write.assert_called_once()
            self.assertEqual(self.stored()["thresholds"]["left"], 49)
            QTest.qWait(SAVE_DELAY_MS + 400)
            write.assert_called_once()  # the wait was ended, not repeated

    def test_quitting_and_other_changes_write_what_is_waiting(self) -> None:
        from app.core import Button

        box = self.window.filter_page.window_boxes[Button.LEFT]
        with self.writes() as write:
            box.stepUp()
            self.window.filter_page.button_switches[Button.BACK].click()  # an immediate change
            write.assert_called_once()
            self.assertEqual(self.stored()["thresholds"]["left"], 47, "it went out with the other change")
            self.assertIn("back", self.stored()["buttons"])
            self.assertFalse(self.controller._save_timer.isActive())
        with self.writes() as write:
            box.stepUp()
            self.controller.shutdown()
            write.assert_called_once()
        self.assertEqual(self.stored()["thresholds"]["left"], 48)

    def test_a_write_that_fails_is_held_for_the_next(self) -> None:
        from app import settings
        from app.core import Button

        box = self.window.filter_page.window_boxes[Button.LEFT]
        with mock.patch.object(settings, "write_json", side_effect=OSError("disk full")):
            box.stepUp()
            self.controller.flush_settings()
        self.assertEqual(self.controller.threshold_for(Button.LEFT), 47, "still takes effect")
        self.controller.flush_settings()  # still unsaved, so it tries again
        self.assertEqual(self.stored()["thresholds"]["left"], 47)

    def test_scrolling_over_a_button_picker_keeps_the_button(self) -> None:
        from app.core import Button

        self.seed_side_history()
        for key, page in (("calibrate", self.window.calibrate), ("history", self.window.history)):
            area = self.scrolling_pane(key, 300)
            picker = page.button_picker
            self.assertNotEqual(picker.focusPolicy(), Qt.FocusPolicy.WheelFocus, key)
            area.ensureWidgetVisible(picker)
            self.application.processEvents()
            position = area.verticalScrollBar().value()
            self.wheel(picker, -1)
            self.assertIs(page.button, Button.LEFT, key)
            self.assertEqual(picker.currentIndex(), 0, key)
            self.assertNotEqual(area.verticalScrollBar().value(), position, f"{key}: the turn scrolls the pane")

    def seed_side_history(self) -> None:
        """History for the back button too, so the History picker has a
        second button to turn to."""
        from app.core import Button
        from app.core import ClickEvent

        self.controller.wear.note_event(ClickEvent(Button.BACK, True, True, 400.0, None), 46)


class CalibrateEachButtonTests(PaneTestCase):
    def run_calibration(self, page) -> None:
        from app.core import REQUIRED_DOUBLE_CLICKS, REQUIRED_SINGLE_CLICKS

        button = page.button
        page._advance()
        for _ in range(REQUIRED_SINGLE_CLICKS):
            page._on_pad_press(900.0, 960.0, button)
        page._on_pad_press(14.0, 40.0, button)  # a bounce
        for _ in range(REQUIRED_DOUBLE_CLICKS):
            page._on_pad_press(900.0, 960.0, button)
            page._on_pad_press(150.0, 210.0, button)
        self.assertEqual(page.phase, "done")

    def test_the_back_button_is_measured_and_applied_to_itself(self) -> None:
        from app.core import Button

        page = self.window.calibrate
        self.window.show_calibration(Button.BACK)
        self.assertIn("back button", page.step_row.title.text())
        self.assertIn("twice quickly", page.step_row.detail.text(), "side buttons aren't double-clicked")
        self.run_calibration(page)
        self.assertEqual(page.double_row.title.text(), "Fastest repeat")
        self.assertIn("starts filtering it", page.step_row.detail.text())
        chosen = page.suggestion.threshold_ms
        page._advance()  # Apply
        self.assertEqual(self.controller.threshold_for(Button.BACK), chosen)
        self.assertEqual(self.controller.threshold_for(Button.LEFT), 46, "the left button is untouched")
        self.assertTrue(self.controller.is_calibrated(Button.BACK))
        self.assertFalse(self.controller.is_calibrated(Button.LEFT))
        self.assertIn(Button.BACK, self.controller.buttons, "it was worth measuring, so it is filtered")
        self.assertIs(page.button, Button.BACK, "the choice stays for next time")

    def measure(self, button, bounce_ms=None, double_ms=150.0):
        """A whole calibration of `button`, stopping at its result."""
        from app.core import REQUIRED_DOUBLE_CLICKS, REQUIRED_SINGLE_CLICKS

        page = self.window.calibrate
        self.window.show_calibration(button)
        page._advance()
        for _ in range(REQUIRED_SINGLE_CLICKS):
            page._on_pad_press(900.0, 960.0, button)
        if bounce_ms is not None:
            page._on_pad_press(bounce_ms, 40.0, button)
        for _ in range(REQUIRED_DOUBLE_CLICKS):
            page._on_pad_press(900.0, 960.0, button)
            page._on_pad_press(double_ms, 210.0, button)
        self.assertEqual(page.phase, "done")
        return page

    def test_the_result_notes_speak_of_the_button_measured(self) -> None:
        from app.core import Button

        # No bounce: said of the button, claiming nothing of a filter that
        # isn't on for it until Apply (the back button) or that Apply keeps.
        page = self.measure(Button.BACK)
        note = page.summary.text()
        self.assertIn("back button didn’t bounce", note)
        self.assertNotIn("Your mouse", note)
        self.assertNotIn("kept on", note, "the back button isn't filtered until Apply")
        self.assertIn("starts filtering it", page.step_row.detail.text())
        page.restart()
        page = self.measure(Button.LEFT)
        self.assertIn("left button didn’t bounce", page.summary.text())
        self.assertNotIn("kept on", page.summary.text())
        self.assertNotIn("starts filtering", page.step_row.detail.text(), "the left button is filtered already")

    def test_a_tight_result_says_repeats_for_the_side_buttons(self) -> None:
        from app.core import Button

        page = self.measure(Button.FORWARD, bounce_ms=28.0, double_ms=60.0)
        self.assertFalse(page.suggestion.confident)
        self.assertIn("quick repeats", page.summary.text())
        self.assertNotIn("double-click", page.summary.text())
        page.restart()
        page = self.measure(Button.RIGHT, bounce_ms=28.0, double_ms=60.0)
        self.assertIn("your double-clicks", page.summary.text())

    def test_the_picker_chooses_and_locks_while_measuring(self) -> None:
        from app.core import Button

        page = self.window.calibrate
        page.button_picker.activated.emit(1)  # the user picks Right
        self.assertIs(page.button, Button.RIGHT)
        page._advance()
        self.assertFalse(page.button_picker.isEnabled())
        page._on_pad_press(None, None, Button.LEFT)
        self.assertEqual(page.calibrator.single_clicks, 0, "a left click doesn't count for the right button")
        page._on_pad_press(None, None, Button.RIGHT)
        self.assertEqual(page.calibrator.single_clicks, 1)
        page.restart()
        self.assertTrue(page.button_picker.isEnabled())

    def test_choosing_another_button_starts_over(self) -> None:
        from app.core import Button

        page = self.window.calibrate
        page._advance()
        page._on_pad_press(None, None, Button.LEFT)
        page.set_button(Button.LEFT)
        self.assertEqual(page.phase, "single", "the same button carries on")
        page.set_button(Button.MIDDLE)
        self.assertEqual(page.phase, "intro")

    def test_the_pad_hears_the_side_buttons(self) -> None:
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest

        from app.core import Button

        self.show("test")
        pad = self.window.test_page.pad
        seen = []
        pad.pressed_with_gap.connect(lambda gap, _interval, button: seen.append(button))
        middle = QPoint(pad.width() // 2, pad.height() // 2)
        QTest.mouseClick(pad, Qt.MouseButton.BackButton, Qt.KeyboardModifier.NoModifier, middle)
        QTest.mouseClick(pad, Qt.MouseButton.ForwardButton, Qt.KeyboardModifier.NoModifier, middle)
        self.assertEqual(seen, [Button.BACK, Button.FORWARD])

    def test_the_test_pane_judges_each_button_by_its_window(self) -> None:
        from app.core import Button

        self.controller.set_threshold(Button.BACK, 20)
        page = self.window.test_page
        page._on_pad_press(30.0, 90.0, Button.BACK)
        page._on_pad_press(30.0, 90.0, Button.LEFT)
        self.assertEqual([bounce for _gap, bounce in page.timeline._gaps], [False, True])
        self.assertTrue(page.last_value.text().endswith("ms"))

    def test_the_test_pane_draws_the_window_of_the_button_it_shows(self) -> None:
        from app.core import Button
        from app.core import ClickEvent

        self.controller.set_threshold(Button.BACK, 20)
        self.controller.set_threshold(Button.RIGHT, 33)
        page = self.window.test_page
        self.show("test")
        self.assertEqual(page.timeline._threshold, 46, "the left button's, before any press")
        self.assertNotIn("button’s window", page.chart_note.text())
        page._on_pad_press(30.0, 90.0, Button.BACK)
        self.assertEqual(page.timeline._threshold, 20, "judged against 20, so the line is at 20")
        self.assertIn("back button’s window", page.chart_note.text())
        page._on_pad_press(30.0, 90.0, Button.LEFT)
        self.assertEqual(page.timeline._threshold, 46)
        self.assertNotIn("button’s window", page.chart_note.text())
        # A bounce the filter blocked is that button's too.
        page.note_global_event(ClickEvent(Button.RIGHT, True, False, 9.0, None))
        self.assertEqual(page.timeline._threshold, 33)
        # And the line follows its window being changed.
        self.controller.set_threshold(Button.RIGHT, 38)
        self.assertEqual(page.timeline._threshold, 38)
        page.reset()
        self.assertEqual(page.timeline._threshold, 46, "clearing starts from the left button again")


class HistoryPaneTests(PaneTestCase):
    def seed(self, button=None, days=30, clicks=500, bounces=(3, 9)) -> None:
        from app.core import Button
        from app.core import ClickEvent

        button = button or Button.LEFT
        wear = self.controller.wear
        start = wear._clock()
        for day in range(days):
            wear._clock = lambda day=day: start - (days - 1 - day) * 86400
            for _ in range(clicks):
                wear.note_event(ClickEvent(button, True, True, 400.0, None), 46)
            for _ in range(bounces[0] if day < days // 2 else bounces[1]):
                wear.note_event(ClickEvent(button, True, False, 11.0, None), 46)
        wear._clock = lambda: start

    def test_an_empty_history(self) -> None:
        self.show("history")
        page = self.window.history
        self.assertEqual(page.trend_value.text(), "Not enough clicks yet")
        self.assertEqual(page.clicks_value.text(), "0")
        self.assertEqual(page.rate_chart.accessibleDescription(), "No clicks recorded yet")
        self.assertEqual(page.button_picker.count(), 1, "only the left button is filtered")
        self.window.grab()

    def test_a_worsening_switch(self) -> None:
        self.seed()
        self.show("history")
        page = self.window.history
        self.assertEqual(page.trend_value.text(), "Getting worse")
        self.assertIn("wearing", page.trend_note.text())
        self.assertEqual(page.clicks_value.text(), f"{30 * 500:,}")
        self.assertEqual(page.bounces_value.text(), str(15 * 3 + 15 * 9))
        self.assertEqual(len(page.rate_chart.days), 30)
        self.assertEqual(dict(page.histogram.histogram.bins)[10], 15 * 3 + 15 * 9)
        self.assertIn("1.8 bounces per 100 clicks", page.rate_chart.accessibleDescription())
        self.window.grab()

    def test_the_trend_is_read_out_with_its_value(self) -> None:
        from PySide6.QtGui import QAccessible

        self.show("history")
        page = self.window.history
        name = lambda: QAccessible.queryAccessibleInterface(page.trend_value).text(QAccessible.Text.Name)
        self.assertEqual(name(), "Trend: Not enough clicks yet")
        self.seed()
        page.refresh()
        self.assertEqual(name(), "Trend: Getting worse", "a screen reader hears the value, not the title alone")

    def test_the_picker_shows_buttons_with_history(self) -> None:
        from app.core import Button

        self.seed(Button.FORWARD, days=2)
        self.window.refresh()
        page = self.window.history
        self.assertEqual([page.button_picker.itemText(index) for index in range(page.button_picker.count())],
                         ["Left", "Forward"])
        page.button_picker.activated.emit(1)
        self.assertIs(page.button, Button.FORWARD)
        self.assertEqual(page.clicks_value.text(), "1,000")

    def test_the_wheel_shows_once_it_has_a_count(self) -> None:
        page = self.window.history
        self.window.refresh()
        self.assertTrue(page.wheel_section.isHidden())
        self.controller.wear.note_wheel(1, True)
        self.window.refresh()
        self.assertFalse(page.wheel_section.isHidden())
        self.assertEqual(page.wheel_value.text(), "1 of 1 ticks")

    def test_the_counts_move_while_the_pane_is_open(self) -> None:
        from app.core import Button
        from app.core import ClickEvent

        self.show("history")
        page = self.window.history
        self.assertTrue(page.ticker.isActive())
        self.assertEqual(page.clicks_value.text(), "0")
        removed = []
        page.button_picker.model().rowsRemoved.connect(lambda *_args: removed.append(1))
        for _ in range(3):
            self.controller.wear.note_event(ClickEvent(Button.LEFT, True, True, 400.0, None), 46)
        page.ticker.timeout.emit()
        self.assertEqual(page.clicks_value.text(), "3")
        self.assertEqual(removed, [], "the picker isn't built again while its buttons stay the same")
        self.show("filter")
        self.assertFalse(page.ticker.isActive(), "it rests while another pane is in front")
        self.show("history")
        self.window.hide()
        self.assertFalse(page.ticker.isActive(), "and while the window is closed")

    def test_hovering_a_column_says_its_value(self) -> None:
        from PySide6.QtCore import QEvent, QPoint
        from PySide6.QtGui import QHelpEvent

        self.seed()
        self.show("history")
        chart = self.window.history.rate_chart
        chart.grab()  # lays the columns out
        rect, text = chart._targets[-1]
        event = QHelpEvent(QEvent.Type.ToolTip, rect.center().toPoint(), chart.mapToGlobal(rect.center().toPoint()))
        with mock.patch("app.ui.charts.QToolTip.showText") as show:
            chart.event(event)
        show.assert_called_once()
        self.assertIn("bounces per 100 clicks", show.call_args.args[1])
        self.assertTrue(text.startswith(show.call_args.args[1].split(":")[0]))
        with mock.patch("app.ui.charts.QToolTip.showText") as show:
            chart.event(QHelpEvent(QEvent.Type.ToolTip, QPoint(1, 1), QPoint(1, 1)))
        show.assert_not_called()


class AppsPaneTests(PaneTestCase):
    def test_empty_then_listed_then_removed(self) -> None:
        page = self.window.apps
        self.window.refresh()
        self.assertEqual(page.list_section.rows[0].title.text(), "No apps")
        hook = self.hook()
        self.controller.add_excluded_app("com.valvesoftware.steam", "Steam")
        self.application.processEvents()
        self.assertEqual([row.title.text() for row in page.list_section.rows], ["Steam"])
        self.assertEqual(page.list_section.rows[0].detail.text(), "com.valvesoftware.steam")
        page.remove_buttons["com.valvesoftware.steam"].click()
        self.assertEqual(self.controller.excluded_apps, [])
        self.assertEqual(hook.updates[-1].excluded_apps, frozenset())
        self.assertEqual(page.list_section.rows[0].title.text(), "No apps")

    def test_the_menu_offers_the_running_apps_and_a_file(self) -> None:
        from app.app_keys import AppChoice

        page = self.window.apps
        self.controller.add_excluded_app("already.exe", "Already")
        running = [AppChoice("cs2.exe", "CS2"), AppChoice("already.exe", "Already")]
        with mock.patch("app.app_keys.running_apps", return_value=running):
            page.add_menu.aboutToShow.emit()
        texts = [action.text() for action in page.add_menu.actions() if not action.isSeparator()]
        self.assertIn("CS2", texts)
        self.assertNotIn("Already", texts, "not offered twice")
        self.assertTrue(texts[-1].startswith("Choose"))
        next(action for action in page.add_menu.actions() if action.text() == "CS2").trigger()
        self.assertIn({"key": "cs2.exe", "name": "CS2"}, self.controller.excluded_apps)

    def test_an_app_chosen_from_a_file(self) -> None:
        bundle = Path(tempfile.mkdtemp()) / "Game.app"
        (bundle / "Contents").mkdir(parents=True)
        with open(bundle / "Contents" / "Info.plist", "wb") as handle:
            plistlib.dump({"CFBundleIdentifier": "com.example.game", "CFBundleName": "Game"}, handle)
        with mock.patch("app.app_keys.platform.system", return_value="Darwin"), \
                mock.patch("app.ui.panes.QFileDialog.getOpenFileName", return_value=(str(bundle), "")):
            self.window.apps.choose_file()
        self.assertEqual(self.controller.excluded_apps, [{"key": "com.example.game", "name": "Game"}])
        with mock.patch("app.ui.panes.QFileDialog.getOpenFileName", return_value=("", "")):
            self.window.apps.choose_file()  # cancelled
        self.assertEqual(len(self.controller.excluded_apps), 1)


    def test_the_program_dialog_starts_where_this_windows_keeps_programs(self) -> None:
        with mock.patch("app.ui.panes.platform.system", return_value="Windows"), \
                mock.patch.dict(os.environ, {"ProgramFiles": "D:\\Programs"}), \
                mock.patch("app.ui.panes.QFileDialog.getOpenFileName", return_value=("", "")) as dialog:
            self.window.apps.choose_file()
        self.assertEqual(dialog.call_args.args[2], "D:\\Programs")
        self.assertIn("*.exe", dialog.call_args.args[3])


class DevicesPaneTests(PaneTestCase):
    def device(self, key, name, kind="mouse"):
        from app.platform import DeviceInfo

        import time

        return DeviceInfo(key=key, name=name, kind=kind, filtered=kind == "mouse", last_seen=time.time())

    def test_devices_seen_appear_by_kind(self) -> None:
        page = self.window.devices
        self.window.refresh()
        self.assertEqual(page.mice.rows[0].title.text(), "No mouse seen yet")
        self.assertTrue(page.touch.isHidden())
        hook = self.hook()
        hook.on_device(self.device("usb:03F0:0001:x", "HP 2.4G wireless and BT Mouse"))
        hook.on_device(self.device("internal:trackpad", "Apple Internal Trackpad", "trackpad"))
        self.application.processEvents()  # the signal is queued from the hook thread
        self.assertEqual([row.title.text() for row in page.mice.rows], ["HP 2.4G wireless and BT Mouse"])
        self.assertIn("USB", page.mice.rows[0].detail.text())
        self.assertFalse(page.touch.isHidden())
        self.assertEqual(page.touch.rows[0].trailing.text(), "Never filtered")

    def test_a_mouse_can_be_left_unfiltered_and_stays_listed(self) -> None:
        page = self.window.devices
        hook = self.hook()
        hook.on_device(self.device("bt:05AC:0269:x", "Magic Mouse"))
        self.window.refresh()
        switch = page.switches["bt:05AC:0269:x"]
        self.assertTrue(switch.isChecked())
        switch.click()
        self.assertEqual(self.controller.ignored_devices, [{"key": "bt:05AC:0269:x", "name": "Magic Mouse"}])
        self.assertIn("bt:05AC:0269:x", hook.updates[-1].ignored_devices)
        self.assertIs(page.switches["bt:05AC:0269:x"], switch, "the same row: focus stays where it was")
        self.assertFalse(switch.isChecked())

    def test_the_time_since_a_mouse_was_seen_moves_on(self) -> None:
        import time

        page = self.window.devices
        hook = self.hook()
        hook.on_device(self.device("usb:03F0:0001:x", "HP 2.4G wireless and BT Mouse"))
        self.show("devices")
        row = page.mice.rows[0]
        self.assertIn("seen just now", row.detail.text())
        self.assertTrue(page.ticker.isActive())
        later = time.time() + 2 * 3600 + 5
        with mock.patch("app.ui.panes.time.time", return_value=later):
            page.ticker.timeout.emit()  # the pane is still open
        self.assertIn("seen 2 h ago", row.detail.text())
        self.assertIs(page.mice.rows[0], row, "the same row: focus stays where it was")
        with mock.patch("app.ui.panes.time.time", return_value=later + 3 * 86400):
            self.window.refresh()
        self.assertIn("seen 3 days ago", row.detail.text())
        self.show("filter")
        self.assertFalse(page.ticker.isActive())

    def test_an_ignored_mouse_not_seen_this_run_can_be_turned_back_on(self) -> None:
        self.controller.set_device_ignored("usb:046D:C08B:y", "G502 HERO", True)
        page = self.window.devices
        self.assertEqual(page.mice.rows[0].title.text(), "G502 HERO")
        self.assertIn("not seen since launch", page.mice.rows[0].detail.text())
        page.switches["usb:046D:C08B:y"].click()
        self.assertEqual(self.controller.ignored_devices, [])


class LookTests(PaneTestCase):
    def test_every_new_control_has_an_accessible_name(self) -> None:
        from PySide6.QtWidgets import QComboBox, QPushButton, QSpinBox

        from app.ui.charts import DailyRateChart, GapHistogram

        self.controller.add_excluded_app("cs2.exe", "CS2")
        self.window.refresh()
        for kind in (QSpinBox, QComboBox, DailyRateChart, GapHistogram):
            widgets = self.window.findChildren(kind)
            self.assertTrue(widgets, kind.__name__)
            for widget in widgets:
                self.assertTrue(widget.accessibleName(), f"a {kind.__name__} without a name")
        for button in self.window.findChildren(QPushButton):
            self.assertTrue(button.accessibleName() or button.text(), "a button without a name")

    def test_copy_diagnostics_says_what_1_0_adds_and_keeps_keys_private(self) -> None:
        from app.core import Button

        self.controller.set_threshold(Button.RIGHT, 30)
        self.controller.set_wheel_fix(True)
        self.controller.add_excluded_app("com.example.secretgame", "Secret Game")
        self.controller.set_device_ignored("usb:046D:C08B:SERIAL123", "G502 HERO", True)
        self.window.general.diagnostics_button.click()
        text = QApplication.clipboard().text()
        for expected in ("windows: left 46 ms, right 30 ms", "side buttons: back off, forward off",
                         "wheel fix: on, 50 ms", "excluded apps: 1", "ignored devices: G502 HERO", "seen devices: none"):
            self.assertIn(expected, text)
        self.assertNotIn("SERIAL123", text)
        self.assertNotIn("secretgame", text)

    def test_the_sidebar_lists_the_new_panes(self) -> None:
        titles = [self.window.sidebar.item(row).text() for row in range(self.window.sidebar.count())]
        self.assertEqual(titles, ["Bounce Filter", "Test", "Calibrate", "History", "Apps", "Devices", "General"])

    def test_every_pane_renders_in_both_looks_and_both_platform_styles(self) -> None:
        from app.ui import base, panes, theme, widgets, window
        from app.ui.window import MainWindow

        for mac in (True, False):
            # The Windows style leads every row with a Fluent icon; every one
            # the new panes name must exist.
            with mock.patch.object(base, "IS_MAC", mac), mock.patch.object(panes, "IS_MAC", mac), \
                    mock.patch.object(window, "IS_MAC", mac), mock.patch.object(widgets, "IS_MAC", mac):
                built = MainWindow(self.controller)
                self.addCleanup(built.deleteLater)
                for dark in (False, True):
                    widgets.set_look(theme.current_look(dark))
                    built.apply_look()
                    for index in range(len(built.pages)):
                        built._show_page(index)
                        built.grab()
                for timer in built.findChildren(__import__("PySide6.QtCore", fromlist=["QTimer"]).QTimer):
                    timer.stop()


if __name__ == "__main__":
    unittest.main()
