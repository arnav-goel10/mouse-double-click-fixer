"""Offscreen tests for the panes and Test and Tune: the buttons and
Strength, the window pop-ups and their saving, the wheel fix, Test and Tune's
steps, History with its verdict and suggestion, Apps and Devices, and what
every control tells VoiceOver and Narrator."""

try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

import json
import os
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    import _ui_support as support
except ImportError:  # run as tests.<module> from the repository root
    from tests import _ui_support as support

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

StandInFilter = support.FakeFilter


class PaneTestCase(unittest.TestCase):
    """The window over a controller with a stand-in hook. `mac` picks the
    layout (None: this computer's)."""

    mac = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.application = support.application()

    def setUp(self) -> None:
        self.directory = support.patch_settings(self)
        self.controller = support.make_controller(self)
        from app.ui.window import MainWindow

        if self.mac is not None:
            platform = support.as_platform(self.mac)
            platform.__enter__()
            self.addCleanup(platform.__exit__, None, None, None)
        self.window = MainWindow(self.controller)
        self.window.isActiveWindow = lambda: True
        self.window.tune.isActiveWindow = lambda: True
        self.window.setMinimumSize(300, 200)
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(support.stop_timers, self.window, self.window.tune)

    def show(self, key: str) -> None:
        self.window.show()
        self.window.show_page(key)
        self.application.processEvents()

    def hook(self) -> StandInFilter:
        self.assertTrue(self.controller.set_active(True))
        return self.controller._filter


class PaneTestCaseCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = support.application()

    def test_a_pane_test_leaves_no_write_waiting_for_the_next_test_to_run(self) -> None:
        # A step in a window pop-up leaves its write waiting on a timer. Left
        # running, it fires in whichever test runs next, and writes this
        # test's settings into that one's folder. Ending the test ends its
        # controller.
        from app.core import Button

        left = []

        class Steps(PaneTestCase):
            def test_a_step(inner) -> None:
                inner.controller.set_threshold(Button.LEFT, 50, deferred=True)
                inner.assertTrue(inner.controller._save_timer.isActive())
                left.append(inner.controller)

        result = unittest.TestResult()
        Steps("test_a_step").run(result)
        self.assertEqual([failure for _test, failure in result.errors + result.failures], [])
        (controller,) = left
        self.assertFalse(controller._save_timer.isActive())


class ButtonsAndStrengthTests(PaneTestCase):
    """The buttons filtered and how strongly, on Windows' Home (the layout
    with every window row in it; the Mac's shows them under Custom only)."""

    mac = False

    def test_a_box_per_button_marked_calibrated_where_it_is(self) -> None:
        from app.core import Button

        page = self.window.home
        self.assertEqual([page.button_checks[button].text() for button in Button],
                         ["Left", "Right", "Middle", "Back", "Forward"])
        self.assertEqual([button for button in Button if page.button_checks[button].isChecked()], [Button.LEFT])
        self.assertTrue(all(not row.value.isVisibleTo(row) for row in page.button_rows.values()))
        self.controller.set_calibrated(Button.RIGHT)
        self.assertEqual(page.button_rows[Button.RIGHT].value.text(), "Calibrated")
        self.assertTrue(page.button_rows[Button.RIGHT].value.isVisibleTo(page.button_rows[Button.RIGHT]))
        self.assertEqual(page.button_checks[Button.RIGHT].accessibleDescription(), "Calibrated")
        self.assertEqual(page.button_checks[Button.LEFT].accessibleName(), "Filter the left button")

    def test_strength_is_automatic_until_the_user_chooses_custom(self) -> None:
        from app.core import Button

        page = self.window.home
        self.assertEqual(page.strength_picker.currentText(), "Automatic")
        self.assertFalse(page.strength_expander.expanded, "no milliseconds by default")
        self.assertFalse(page.window_boxes[Button.LEFT].isEnabled(), "Automatic's windows are shown, not set")
        page.strength_picker.activated.emit(1)  # the user picks Custom
        self.assertEqual(self.controller.settings["strength"], "custom")
        self.assertTrue(page.strength_expander.expanded, "Custom opens the windows")
        self.assertTrue(page.window_boxes[Button.LEFT].isEnabled())
        self.assertIn("Your own window", page.strength_expander.header.detail.text())
        page.strength_picker.activated.emit(0)
        self.assertEqual(self.controller.settings["strength"], "automatic")

    def test_the_expander_opens_and_closes_by_its_header_and_chevron(self) -> None:
        from PySide6.QtCore import QEvent, QPointF
        from PySide6.QtGui import QMouseEvent

        expander = self.window.home.strength_expander
        self.assertEqual(expander.chevron.accessibleName(), "Strength for each button")
        self.assertEqual(expander.chevron.accessibleDescription(), "Collapsed")
        self.assertFalse(expander.chevron.isCheckable(), "a glyph, not a toggle the style would fill")
        expander.chevron.click()
        self.assertTrue(expander.expanded)
        self.assertEqual(expander.chevron.accessibleDescription(), "Expanded")
        centre = QPointF(expander.header.rect().center())
        release = QMouseEvent(QEvent.Type.MouseButtonRelease, centre, centre, Qt.MouseButton.LeftButton,
                              Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(expander.header, release)
        self.assertFalse(expander.expanded)

    def test_each_window_reaches_its_own_button(self) -> None:
        from app.core import Button

        hook = self.hook()
        page = self.window.home
        self.controller.set_strength("custom")
        page.button_checks[Button.BACK].click()
        self.assertIn(Button.BACK, self.controller.buttons)
        self.assertFalse(page.window_rows[Button.BACK].isHidden())
        page.window_boxes[Button.BACK].setValue(25)
        self.assertEqual(self.controller.threshold_for(Button.BACK), 25)
        self.assertEqual(self.controller.threshold_for(Button.LEFT), 46)
        self.assertEqual(hook.updates[-1].thresholds[Button.BACK], 25)

    def test_only_the_filtered_buttons_have_a_window_row(self) -> None:
        from app.core import Button

        page = self.window.home
        self.assertEqual([button for button in Button if not page.window_rows[button].isHidden()], [Button.LEFT])
        self.assertTrue(page.wheel_window_row.isHidden(), "the wheel's only once its fix is on")
        self.controller.set_wheel_fix(True)
        self.assertFalse(page.wheel_window_row.isHidden())

    def test_a_refresh_never_echoes_into_a_change(self) -> None:
        with mock.patch.object(self.controller, "set_threshold") as set_threshold, \
                mock.patch.object(self.controller, "set_wheel_fix") as set_wheel_fix, \
                mock.patch.object(self.controller, "set_strength") as set_strength, \
                mock.patch.object(self.controller, "set_buttons") as set_buttons:
            self.window.refresh()
        set_threshold.assert_not_called()
        set_wheel_fix.assert_not_called()
        set_strength.assert_not_called()
        set_buttons.assert_not_called()

    def test_each_window_is_a_pop_up_of_windows_across_the_allowed_range(self) -> None:
        from PySide6.QtWidgets import QComboBox

        from app import settings
        from app.core import DEFAULT_THRESHOLD_MS, MAX_THRESHOLD_MS, MIN_THRESHOLD_MS
        from app.platform import WHEEL_DEFAULT_MS

        page = self.window.home
        for box, low, high, default in [
            *((box, MIN_THRESHOLD_MS, MAX_THRESHOLD_MS, DEFAULT_THRESHOLD_MS) for box in page.window_boxes.values()),
            (page.wheel_box, settings.WHEEL_MIN_MS, settings.WHEEL_MAX_MS, WHEEL_DEFAULT_MS),
        ]:
            name = box.accessibleName()
            self.assertIsInstance(box, QComboBox, name)
            self.assertFalse(box.isEditable(), f"{name}: a pop-up button, not a field to type in")
            values = box.values()
            self.assertEqual(values, sorted(set(values)), name)
            self.assertTrue(all(low <= value <= high for value in values), name)
            self.assertEqual(values[-1], high, f"{name}: up to the longest window allowed")
            self.assertLessEqual(values[0], 10, f"{name}: down to the shortest worth choosing")
            self.assertIn(default, values, name)
            self.assertEqual([box.itemText(index) for index in range(box.count())], [f"{value} ms" for value in values])

    def test_a_window_that_is_not_in_the_list_is_shown_in_its_place(self) -> None:
        from app.core import Button
        from app.ui.window import BUTTON_WINDOWS

        hook = self.hook()
        self.controller.set_strength("custom")
        page = self.window.home
        self.controller.set_threshold(Button.LEFT, 59)  # as calibration sets it
        box = page.window_boxes[Button.LEFT]
        self.assertEqual(box.currentText(), "59 ms")
        self.assertEqual(box.values(), sorted({*BUTTON_WINDOWS, 59}), "in order among the others")
        self.assertEqual(page.window_boxes[Button.RIGHT].values(), list(BUTTON_WINDOWS), "only where it is chosen")
        box.setCurrentIndex(box.findData(60))  # the user picks 60 ms
        self.assertEqual(self.controller.threshold_for(Button.LEFT), 60)
        self.assertEqual(hook.updates[-1].thresholds[Button.LEFT], 60)
        box.setCurrentIndex(box.findData(59))  # and back to the measured one
        self.assertEqual(self.controller.threshold_for(Button.LEFT), 59)
        self.controller.set_wheel_fix(True, 35)
        self.assertEqual(page.wheel_box.currentText(), "35 ms")

    def test_the_wheel_fix_switch(self) -> None:
        hook = self.hook()
        page = self.window.home
        self.assertFalse(page.wheel_switch.isChecked())
        page.wheel_switch.click()
        self.assertTrue(self.controller.wheel_fix)
        self.assertTrue(hook.updates[-1].wheel_fix)
        self.controller.set_strength("custom")
        page.wheel_box.setValue(35)
        self.assertEqual(self.controller.wheel_window_ms, 35)
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
        """The pane `key` on screen at `height`, where it has to scroll, and
        its scroll area."""
        self.show(key)
        self.window.resize(self.window.width(), height)
        self.application.processEvents()
        area = self.window.stack.currentWidget()
        self.assertGreater(area.verticalScrollBar().maximum(), 0, f"the {key} pane scrolls at this size")
        return area

    def test_scrolling_over_a_window_pop_up_scrolls_the_pane_and_keeps_the_window(self) -> None:
        from app.core import Button

        hook = self.hook()
        self.controller.set_buttons(list(Button))  # every pop-up shown: the worst case
        self.controller.set_wheel_fix(True)
        self.controller.set_strength("custom")
        self.window.home.strength_expander.set_expanded(True)
        area = self.scrolling_pane("home")
        page = self.window.home
        bar = area.verticalScrollBar()
        saved = (self.directory / "settings.json").read_text()
        updates = len(hook.updates)
        for box in [*page.window_boxes.values(), page.wheel_box, page.strength_picker]:
            name = box.accessibleName()
            self.assertNotEqual(box.focusPolicy(), Qt.FocusPolicy.WheelFocus, f"{name}: the wheel gives no focus")
            area.ensureWidgetVisible(box)
            self.application.processEvents()
            before, position = box.currentIndex(), bar.value()
            notches = 1 if position == bar.maximum() else -1
            self.wheel(box, notches)
            self.assertEqual(box.currentIndex(), before, name)
            self.assertFalse(box.hasFocus(), name)
            self.assertNotEqual(bar.value(), position, f"{name}: the turn scrolls the pane instead")
        self.assertEqual([self.controller.threshold_for(button) for button in Button], [46] * 5)
        self.assertEqual(self.controller.wheel_window_ms, 50)
        self.assertEqual((self.directory / "settings.json").read_text(), saved, "nothing saved")
        self.assertEqual(len(hook.updates), updates, "nothing reached the filter")

    @staticmethod
    def step(box) -> None:
        """One step down the list, as an arrow key or a wheel notch takes it."""
        box.setCurrentIndex(box.currentIndex() + 1)

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
        self.controller.set_strength("custom")
        box = self.window.home.window_boxes[Button.LEFT]
        before = len(hook.updates)
        last = box.values()[box.currentIndex() + 10]
        with self.writes() as write:
            for _ in range(10):
                self.step(box)
            self.assertEqual(box.value(), last)
            self.assertEqual(self.controller.threshold_for(Button.LEFT), last)
            self.assertEqual(len(hook.updates) - before, 10, "the filter follows every step at once")
            self.assertEqual(hook.updates[-1].thresholds[Button.LEFT], last)
            write.assert_not_called()
            self.assertEqual(self.stored()["thresholds"]["left"], 46, "the file waits")
            QTest.qWait(SAVE_DELAY_MS + 400)
            write.assert_called_once()
        self.assertEqual(self.stored()["thresholds"]["left"], last, "the last step is what is saved")

    def test_a_run_of_steps_in_the_wheel_pop_up_is_one_write_too(self) -> None:
        from PySide6.QtTest import QTest

        from app.controller import SAVE_DELAY_MS

        box = self.window.home.wheel_box
        steps = 4
        last = box.values()[box.currentIndex() + steps]
        with self.writes() as write:
            for _ in range(steps):
                self.step(box)
            self.assertEqual(self.controller.wheel_window_ms, last)
            write.assert_not_called()
            QTest.qWait(SAVE_DELAY_MS + 400)
            write.assert_called_once()
        self.assertEqual(self.stored()["wheel_window_ms"], last)

    def test_closing_the_window_writes_what_is_waiting(self) -> None:
        from PySide6.QtCore import QEvent
        from PySide6.QtTest import QTest

        from app.controller import SAVE_DELAY_MS
        from app.core import Button

        self.controller.set_strength("custom")
        self.window.save_geometry()  # closing then has nothing else to write
        box = self.window.home.window_boxes[Button.LEFT]
        with self.writes() as write:
            for _ in range(3):
                self.step(box)
            write.assert_not_called()
            self.window.closeEvent(QEvent(QEvent.Type.Close))
            write.assert_called_once()
            self.assertEqual(self.stored()["thresholds"]["left"], 60, "46, then 50, 55 and 60")
            QTest.qWait(SAVE_DELAY_MS + 400)
            write.assert_called_once()  # the wait was ended, not repeated

    def test_quitting_and_other_changes_write_what_is_waiting(self) -> None:
        from app.core import Button

        self.controller.set_strength("custom")
        box = self.window.home.window_boxes[Button.LEFT]
        with self.writes() as write:
            self.step(box)
            self.window.home.button_checks[Button.BACK].click()  # an immediate change
            write.assert_called_once()
            self.assertEqual(self.stored()["thresholds"]["left"], 50, "it went out with the other change")
            self.assertIn("back", self.stored()["buttons"])
            self.assertFalse(self.controller._save_timer.isActive())
        with self.writes() as write:
            self.step(box)
            self.controller.shutdown()
            write.assert_called_once()
        self.assertEqual(self.stored()["thresholds"]["left"], 55)


class MacGeneralTests(PaneTestCase):
    """The Mac's General pane: no milliseconds until Custom."""

    mac = True

    def test_automatic_shows_no_window_and_custom_shows_the_filtered_buttons(self) -> None:
        from app.core import Button

        page = self.window.home
        self.assertIsNone(page.strength_expander)
        self.assertEqual(page.strength_picker.currentText(), "Automatic")
        self.assertFalse(any(row.isVisibleTo(page) for row in page.window_rows.values()))
        self.assertIn("Test and Tune", page.strength_note.text())
        self.controller.set_buttons([Button.LEFT, Button.BACK])
        page.strength_picker.activated.emit(1)
        self.assertEqual([button for button in Button if page.window_rows[button].isVisibleTo(page)],
                         [Button.LEFT, Button.BACK])
        self.assertIn("how soon after letting go", page.strength_note.text())

    def test_custom_makes_the_window_taller_to_fit(self) -> None:
        from app.core import Button

        self.controller.set_buttons(list(Button))
        self.show("general")
        self.window.resize(self.window.width(), 300)  # a tall screen's worth is not offscreen's
        before = self.window.pane_height()
        self.controller.set_strength("custom")
        self.application.processEvents()
        self.assertGreater(self.window.pane_height(), before, "five more rows")

    def test_test_and_tune_opens_from_general(self) -> None:
        page = self.window.home
        self.assertEqual(page.test_button.text(), "Test and Tune…")
        page.test_button.click()
        self.assertTrue(self.window.tune.isVisible())
        self.window.tune.close()

    def test_show_in_menu_bar_says_how_to_restore_the_icon(self) -> None:
        page = self.window.home
        toggled = []
        self.window.menu_bar_toggled.connect(toggled.append)
        page.menu_bar_switch.click()
        self.assertEqual(toggled, [True])
        self.assertIn("always opens Settings", page.menu_bar_note.text())


class WindowsHomeTests(PaneTestCase):
    """Windows' Home: the hero card, the accent Test and tune, and the
    InfoBar the first run shows."""

    mac = False

    def test_test_and_tune_is_the_pages_one_accent_button(self) -> None:
        from PySide6.QtWidgets import QPushButton

        page = self.window.home
        accents = [button for button in page.findChildren(QPushButton) if button.isDefault()]
        self.assertEqual(accents, [page.test_button])
        self.assertEqual(page.test_button.text(), "Test and tune")
        page.test_button.click()
        self.assertTrue(self.window.tune.isVisible())
        self.assertEqual(self.window.tune.windowTitle(), "Test and tune")
        self.window.tune.close()

    def test_the_first_run_infobar_shows_until_closed_once(self) -> None:
        page = self.window.home
        self.window.refresh()
        self.assertFalse(page.info_bar.isHidden())
        self.assertIn("taskbar corner", page.info_bar.title.text())
        self.assertEqual(page.info_bar.close_button.accessibleName(), "Close")
        page.info_bar.close_button.click()
        self.assertTrue(page.info_bar.isHidden())
        self.assertTrue(self.controller.tray_hint_shown, "remembered")
        self.window.refresh()
        self.assertTrue(page.info_bar.isHidden())


class TuneTests(PaneTestCase):
    """Test and Tune: the pad, the verdict, the tune step and Apply."""

    def tune(self, button=None):
        self.window.show_tune(button)
        return self.window.tune

    def measure(self, button=None, bounce_ms=None, double_ms=150.0):
        """A whole test of `button`, stopping at its verdict."""
        from app.core import Button

        tune = self.tune(button)
        button = button or Button.LEFT
        tune._on_pad_press(None, None, button)
        while tune.phase == "single":
            tune._on_pad_press(900.0, 960.0, button)
            if bounce_ms is not None:
                tune._on_pad_press(bounce_ms, 40.0, button)
                bounce_ms = None
        while tune.phase == "double":
            tune._on_pad_press(900.0, 960.0, button)
            tune._on_pad_press(double_ms, 210.0, button)
        self.assertEqual(tune.step, "verdict")
        return tune

    def test_the_steps_from_the_pad_to_done(self) -> None:
        from app.core import Button

        tune = self.measure(bounce_ms=12.0)
        self.assertEqual(tune.step_label.text(), "Step 2 of 3")
        self.assertEqual(tune.title.text(), "Your left button double-clicks on its own")
        self.assertEqual(tune.primary_button.text(), tune.primary_button.text())
        suggestion = tune.suggestion
        self.assertLess(suggestion.threshold_ms, 150, "never ignores the measured double-click")
        tune._primary()  # Continue
        self.assertEqual(tune.step, "tune")
        self.assertEqual(tune.step_label.text(), "Step 3 of 3")
        self.assertIn("(recommended)", tune.window_picker.currentText())
        with mock.patch.object(self.controller, "apply_calibration", wraps=self.controller.apply_calibration) as applied:
            tune._primary()  # Apply
        applied.assert_called_once_with(Button.LEFT, suggestion.threshold_ms, suggestion.fastest_double_click_ms)
        self.assertEqual(tune.step, "done")
        self.assertEqual(self.controller.threshold_for(Button.LEFT), suggestion.threshold_ms)
        self.assertTrue(self.controller.is_calibrated(Button.LEFT))
        self.assertIn(f"{suggestion.threshold_ms} ms", tune.result_row.detail.text())

    def test_without_enough_double_clicks_the_verdict_says_so_and_offers_no_apply(self) -> None:
        tune = self.tune()
        tune._advance()
        tune._on_pad_press(900.0, 960.0)
        tune._finish()
        self.assertIsNone(tune.suggestion)
        self.assertFalse(tune.primary_button.isVisibleTo(tune))
        self.assertIn("Not enough", tune.title.text())

    def test_no_bounce_says_the_mouse_may_be_fine(self) -> None:
        tune = self.measure()
        self.assertEqual(tune.title.text(), "No bounce seen")
        self.assertIn("may be fine", tune.detail.text())

    def test_the_verdict_adds_what_everyday_use_has_shown(self) -> None:
        health = support.Health("Your left button double-clicks on its own", "Caught 9 of 9 extra clicks.", None,
                                "bouncing")
        with mock.patch.object(self.controller, "health", return_value=health):
            tune = self.measure()
        self.assertTrue(tune.health_heading.isVisibleTo(tune))
        self.assertEqual(tune.health_row.title.text(), health.verdict)
        self.assertEqual(tune.health_icon.name, "warning")

    def test_the_verdict_leaves_the_suggestions_question_to_history(self) -> None:
        from app.core import Button

        reason = "Recent bounces on the left button reached about 56 ms, close to its 59 ms window. Raise it to 80 ms?"
        health = support.Health(
            "Your left button double-clicks on its own: caught 9 of 9",
            f"Over the last 30 days the filter ignored every extra click. {reason}",
            support.Suggestion(Button.LEFT, 59, 80, reason),
            "bouncing",
        )
        with mock.patch.object(self.controller, "health", return_value=health):
            tune = self.measure()
        detail = tune.health_row.detail.text()
        self.assertIn("reached about 56 ms, close to its 59 ms window.", detail)
        self.assertNotIn("?", detail, "Test and Tune sets the window itself: no question without an answer")

    def test_another_mouse_utility_is_named_once(self) -> None:
        found = [support.Conflict("Logi Options+", ["side buttons", "wheel"])]
        quiet = support.Health("No bounce seen", "", None, "quiet")
        with mock.patch.object(self.controller, "conflicts", return_value=found), \
                mock.patch.object(self.controller, "health", return_value=quiet):
            tune = self.measure()
        self.assertEqual([row.title.text() for row in tune.conflict_section.rows],
                         ["Another app may be changing clicks: Logi Options+"])
        verdict = support.Health("Another app may be changing clicks: Logi Options+", "", None, "conflict")
        with mock.patch.object(self.controller, "conflicts", return_value=found), \
                mock.patch.object(self.controller, "health", return_value=verdict):
            tune._render()
        self.assertTrue(tune.conflict_section.isHidden(), "the health check already says it")

    def test_the_back_button_is_measured_and_applied_to_itself(self) -> None:
        from app.core import Button

        tune = self.tune(Button.BACK)
        self.assertIs(tune.button, Button.BACK)
        self.assertEqual(tune.button_picker.currentText(), "Back")
        self.assertIn("twice quickly", tune.detail.text(), "side buttons aren't double-clicked")
        tune = self.measure(Button.BACK, bounce_ms=10.0)
        self.assertEqual(tune.double_row.title.text(), "Your fastest repeat")
        self.assertIn("repeats on its own", tune.title.text())
        chosen = tune.suggestion.threshold_ms
        tune._primary()
        tune._primary()
        self.assertEqual(self.controller.threshold_for(Button.BACK), chosen)
        self.assertEqual(self.controller.threshold_for(Button.LEFT), 46, "the left button is untouched")
        self.assertTrue(self.controller.is_calibrated(Button.BACK))
        self.assertIn(Button.BACK, self.controller.buttons, "it was worth measuring, so it is filtered")

    def test_the_picker_chooses_and_locks_while_measuring(self) -> None:
        from app.core import Button

        tune = self.tune()
        tune.button_picker.activated.emit(1)  # the user picks Right
        self.assertIs(tune.button, Button.RIGHT)
        tune._advance()
        self.assertFalse(tune.button_picker.isEnabled())
        tune._on_pad_press(None, None, Button.LEFT)
        self.assertEqual(tune.calibrator.single_clicks, 0, "a left click doesn't count for the right button")
        self.assertIn("left button", tune.step_row.detail.text())
        tune._on_pad_press(None, None, Button.RIGHT)
        self.assertEqual(tune.calibrator.single_clicks, 1)
        tune.restart()
        self.assertTrue(tune.button_picker.isEnabled())

    def test_choosing_another_button_starts_over(self) -> None:
        from app.core import Button

        tune = self.tune()
        tune._advance()
        tune._on_pad_press(None, None, Button.LEFT)
        tune.set_button(Button.LEFT)
        self.assertEqual(tune.phase, "single", "the same button carries on")
        tune.set_button(Button.MIDDLE)
        self.assertEqual(tune.phase, "intro")

    def test_the_first_pad_press_starts_and_counts(self) -> None:
        tune = self.tune()
        self.assertEqual(tune.phase, "intro")
        tune._on_pad_press(8.0, 30.0)  # timed against a click before the start
        self.assertEqual(tune.phase, "single")
        self.assertEqual(tune.calibrator.single_clicks, 1, "that press counts")
        self.assertEqual(tune.calibrator.single_gaps_ms, [], "its stale gap is not taken for bounce")
        self.assertEqual(tune.count_label.text(), "1 of 12")

    def to_double_phase(self):
        tune = self.tune()
        tune._advance()
        while tune.phase == "single":
            tune._on_pad_press(900.0, 960.0)
        return tune

    def test_a_pair_needs_a_first_press_and_a_second(self) -> None:
        tune = self.to_double_phase()
        tune._on_pad_press(1500.0, 1600.0)  # opens a pair
        self.assertEqual(tune.calibrator.double_clicks, 0)
        self.assertEqual(tune.pad._flash_tone, "neutral", "the first press isn't flashed as counted")
        tune._on_pad_press(150.0, 220.0)  # closes it
        self.assertEqual(tune.calibrator.double_clicks, 1)
        self.assertEqual(tune.pad._flash_tone, "good")

    def test_a_triple_click_counts_once(self) -> None:
        tune = self.to_double_phase()
        tune._on_pad_press(1500.0, 1600.0)
        tune._on_pad_press(150.0, 220.0)
        tune._on_pad_press(150.0, 220.0)  # the third press opens a new pair
        self.assertEqual(tune.calibrator.double_clicks, 1)

    def test_bounce_inside_a_pair_is_evidence_and_keeps_the_pair_open(self) -> None:
        tune = self.to_double_phase()
        tune._on_pad_press(1500.0, 1600.0)
        tune._on_pad_press(9.0, 60.0)  # chatter after the first release
        self.assertIn("Extra click caught", tune.step_row.detail.text())
        tune._on_pad_press(150.0, 220.0)
        self.assertEqual(tune.calibrator.double_clicks, 1)
        self.assertEqual(tune.calibrator.single_gaps_ms, [9.0])

    def test_a_long_system_setting_still_needs_a_quick_pair(self) -> None:
        hints = mock.Mock()
        hints.mouseDoubleClickInterval.return_value = 5000  # the slowest macOS allows
        with mock.patch("app.ui.tune.QGuiApplication.styleHints", return_value=hints):
            tune = self.to_double_phase()
            tune._on_pad_press(1500.0, 1600.0)
            tune._on_pad_press(1500.0, 1600.0)  # two separate slow clicks
            self.assertEqual(tune.calibrator.double_clicks, 0)
            self.assertIn("Too slow", tune.step_row.detail.text())
            tune._on_pad_press(900.0, 990.0)  # within a second of the last
        self.assertEqual(tune.calibrator.double_clicks, 1)

    def test_the_tune_step_tries_the_chosen_window_on_its_pad(self) -> None:
        tune = self.measure(bounce_ms=12.0)
        tune._primary()
        chosen = tune.chosen_ms
        tune._on_try_press(chosen - 5.0, 40.0)
        self.assertIn("ignored as an extra click", tune.try_note.text())
        tune._on_try_press(140.0, 200.0)
        self.assertIn("it counts", tune.try_note.text())
        too_long = max(value for value in (tune.window_picker.itemData(index) for index in range(tune.window_picker.count())))
        tune.window_picker.setCurrentIndex(tune.window_picker.findData(too_long))
        tune._on_window_chosen(tune.window_picker.currentIndex())
        self.assertEqual(tune.chosen_ms, too_long)
        self.assertIn("Too long", tune.window_row.detail.text())

    def test_the_pad_hears_every_button_each_against_its_own_release(self) -> None:
        from PySide6.QtCore import QPoint
        from PySide6.QtTest import QTest

        from app.core import Button

        tune = self.tune()
        self.application.processEvents()
        pad = tune.pad
        seen = []
        pad.pressed_with_gap.connect(lambda gap, _interval, button: seen.append((gap, button)))
        middle = QPoint(pad.width() // 2, pad.height() // 2)
        for button in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton, Qt.MouseButton.BackButton,
                       Qt.MouseButton.ForwardButton, Qt.MouseButton.RightButton):
            QTest.mouseClick(pad, button, Qt.KeyboardModifier.NoModifier, middle)
        self.assertEqual([button for _gap, button in seen],
                         [Button.LEFT, Button.RIGHT, Button.BACK, Button.FORWARD, Button.RIGHT])
        self.assertEqual([gap is None for gap, _button in seen], [True, True, True, True, False])

    def pad_gap(self, windows: bool, arrivals: list) -> float:
        """A press, release and press with tick-quantized event stamps
        (1000, 1016, 1016 ms) arriving at `arrivals` on the precise clock."""
        from app.ui import widgets

        pad = widgets.ClickPad()
        self.addCleanup(pad.deleteLater)
        gaps = []
        pad.pressed_with_gap.connect(lambda gap, _interval, _button: gaps.append(gap))

        def event(stamp):
            return mock.Mock(button=mock.Mock(return_value=Qt.MouseButton.LeftButton),
                             timestamp=mock.Mock(return_value=stamp))

        with mock.patch.object(widgets, "IS_WINDOWS", windows), \
                mock.patch.object(widgets, "perf_counter", side_effect=arrivals):
            pad.mousePressEvent(event(1000))
            pad.mouseReleaseEvent(event(1016))
            pad.mousePressEvent(event(1016))
        return gaps[-1]

    def test_the_pad_times_windows_clicks_on_the_precise_clock(self) -> None:
        # Windows stamps both from the same 15.6 ms tick: a 9 ms bounce reads 0.
        self.assertAlmostEqual(self.pad_gap(True, [10.000, 10.050, 10.059]), 9.0, places=3)

    def test_the_pad_keeps_the_events_own_stamps_elsewhere(self) -> None:
        self.assertEqual(self.pad_gap(False, []), 0.0, "macOS stamps are precise; these say 0")

    def test_the_done_step_offers_turning_the_filter_on(self) -> None:
        tune = self.measure(bounce_ms=12.0)
        tune._primary()
        tune._primary()
        self.assertTrue(tune.filter_section.isVisibleTo(tune), "the filter is off")
        requested = []
        tune.filter_requested.connect(requested.append)
        tune.turn_on_button.click()
        self.assertEqual(requested, [True])

    def test_each_platform_has_its_own_wording_and_button_order(self) -> None:
        from app.ui.tune import TuneWindow

        for mac in (True, False):
            with support.as_platform(mac):
                tune = TuneWindow(self.controller)
            self.addCleanup(tune.deleteLater)
            self.assertEqual(tune.windowTitle(), "Test and Tune" if mac else "Test and tune")
            buttons = [tune.footer.layout().itemAt(index).widget() for index in range(tune.footer.layout().count())]
            buttons = [button for button in buttons if button is not None]
            # The Mac puts the default button last, on the right; a Windows
            # dialog puts the primary first.
            self.assertEqual(buttons[-1 if mac else 0], tune.primary_button)


class HistoryPaneTests(PaneTestCase):
    def seed(self, button=None, days=30, clicks=500, bounces=(3, 9)) -> None:
        from app.core import Button, ClickEvent

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
        self.assertIn("1.8 bounces per 100 clicks", page.rate_chart.accessibleDescription())
        self.window.grab()

    def test_the_verdict_leads_the_pane(self) -> None:
        health = support.Health("Your left button double-clicks on its own", "Caught 9 of 9 extra clicks.", None,
                                "bouncing")
        with mock.patch.object(self.controller, "health", return_value=health):
            self.show("history")
        page = self.window.history
        self.assertEqual(page.verdict_row.title.text(), health.verdict)
        self.assertEqual(page.verdict_row.detail.text(), health.detail)
        self.assertEqual(page.verdict_icon.name, "warning")
        self.assertTrue(page.suggestion_row.isHidden(), "nothing to suggest")

    def test_a_suggestion_is_a_question_with_apply(self) -> None:
        from app.core import Button

        suggestion = support.Suggestion(Button.LEFT, 46, 64, "Bounces reached 50 ms.")
        health = support.Health("Your left button double-clicks on its own", "", suggestion, "bouncing")
        with mock.patch.object(self.controller, "health", return_value=health):
            self.show("history")
            page = self.window.history
            self.assertFalse(page.suggestion_row.isHidden())
            self.assertEqual(page.suggestion_row.title.text(), "Raise the left button to 64 ms?")
            self.assertEqual(page.suggestion_button.accessibleName(), "Apply: Raise the left button to 64 ms?")
            with mock.patch.object(self.controller, "apply_suggestion") as apply:
                page.suggestion_button.click()
        apply.assert_called_once_with(suggestion)

    def test_the_suggestions_question_is_asked_once(self) -> None:
        # The health check's detail carries the suggestion's reason, which
        # ends in the same question the row beside Apply asks.
        from app.core import Button

        reason = "Recent bounces on the left button reached about 56 ms, close to its 59 ms window. Raise it to 80 ms?"
        health = support.Health(
            "Your left button double-clicks on its own: caught 412 of 412",
            f"Over the last 30 days the filter ignored every extra click. {reason}",
            support.Suggestion(Button.LEFT, 59, 80, reason),
            "bouncing",
        )
        with mock.patch.object(self.controller, "health", return_value=health):
            self.show("history")
            page = self.window.history
            shown = f"{page.verdict_row.detail.text()} {page.suggestion_row.title.text()}"
        self.assertEqual(shown.count("?"), 1, shown)
        self.assertEqual(page.suggestion_row.title.text(), "Raise the left button to 80 ms?")
        self.assertEqual(
            page.verdict_row.detail.text(),
            "Over the last 30 days the filter ignored every extra click. Recent bounces on the left button "
            "reached about 56 ms, close to its 59 ms window.",
        )

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
        from app.core import Button, ClickEvent

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
        self.show("exceptions")
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

    def test_scrolling_over_the_button_picker_keeps_the_button(self) -> None:
        from app.core import Button, ClickEvent

        self.controller.wear.note_event(ClickEvent(Button.BACK, True, True, 400.0, None), 46)
        self.show("history")
        self.window.resize(self.window.width(), 300)
        self.application.processEvents()
        area = self.window.stack.currentWidget()
        picker = self.window.history.button_picker
        self.assertNotEqual(picker.focusPolicy(), Qt.FocusPolicy.WheelFocus)
        area.ensureWidgetVisible(picker)
        self.application.processEvents()
        ButtonsAndStrengthTests.wheel(self, picker, -1)
        self.assertIs(self.window.history.button, Button.LEFT)
        self.assertEqual(picker.currentIndex(), 0)


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
        self.assertEqual(page.remove_buttons["com.valvesoftware.steam"].accessibleName(), "Remove Steam")
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

    def test_add_app_is_a_plain_button_that_opens_the_menu(self) -> None:
        from app.ui.base import label

        page = self.window.apps
        self.assertIsNone(page.add_button.menu())
        self.assertEqual(page.add_button.text(), label("Add App…"))
        self.assertEqual(page.add_button.accessibleName(), "Add an app")
        with mock.patch.object(page.add_menu, "popup") as popup:
            page.add_button.click()
        popup.assert_called_once()
        at, corner = popup.call_args.args[0], page.add_button.mapToGlobal(page.add_button.rect().bottomLeft())
        self.assertEqual(at.x(), corner.x(), "lined up with the button")
        self.assertGreater(at.y(), corner.y(), "under it")

    def test_the_mac_menu_is_built_from_the_same_entries(self) -> None:
        import sys

        if sys.platform != "darwin":
            self.skipTest("AppKit")
        from app.app_keys import AppChoice
        from app.ui import popup_mac

        page = self.window.apps
        with mock.patch("app.app_keys.running_apps", return_value=[AppChoice("cs2.exe", "CS2")]):
            entries = page.menu_entries()
        menu, target = popup_mac.build(entries)
        titles = [menu.itemAtIndex_(index).title() for index in range(menu.numberOfItems())]
        self.assertEqual(titles, ["Running Apps", "CS2", "", "Choose App…"])
        self.assertTrue(menu.itemAtIndex_(2).isSeparatorItem())
        self.assertEqual(menu.itemAtIndex_(1).toolTip(), "cs2.exe")
        target.choose_(menu.itemAtIndex_(1))
        self.application.processEvents()  # run after the menu closes
        self.assertIn({"key": "cs2.exe", "name": "CS2"}, self.controller.excluded_apps)
        self.assertFalse(popup_mac.available(), "offscreen: Qt's menu instead")

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
        import time

        from app.platform import DeviceInfo

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
        self.assertEqual(page.touch.rows[0].trailing.text(), "Not filtered")

    def test_the_note_and_the_empty_list_say_what_each_system_does(self) -> None:
        from PySide6.QtWidgets import QLabel

        from app.ui import panes

        with mock.patch("app.ui.panes.platform.system", return_value="Windows"):
            note, empty = panes.devices_note(), panes.no_mouse_detail()
        self.assertIn("Precision touchpads, touchscreens and pens are not filtered", note)
        self.assertIn("within about a second of using a mouse is filtered as that mouse's click", note)
        self.assertIn("isn't a precision one reports as a mouse", note)
        self.assertIn("move or click", empty)
        with mock.patch("app.ui.panes.platform.system", return_value="Darwin"):
            note, empty = panes.devices_note(), panes.no_mouse_detail()
        self.assertTrue(note.startswith("Trackpads, touchscreens and pens are not filtered."))
        self.assertIn("click with it", empty)
        self.window.refresh()
        page = self.window.devices
        self.assertEqual(page.mice.rows[0].detail.text(), panes.no_mouse_detail())
        self.assertIn(panes.devices_note(), [each.text() for each in page.findChildren(QLabel)])

    def test_a_mouse_can_be_left_unfiltered_and_stays_listed(self) -> None:
        page = self.window.devices
        hook = self.hook()
        hook.on_device(self.device("bt:05AC:0269:x", "Magic Mouse"))
        self.window.refresh()
        switch = page.switches["bt:05AC:0269:x"]
        self.assertTrue(switch.isChecked())
        self.assertEqual(switch.accessibleName(), "Filter Magic Mouse")
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
        self.show("exceptions")
        row = page.mice.rows[0]
        self.assertIn("seen just now", row.detail.text())
        ticker = self.window.exceptions.ticker
        self.assertTrue(ticker.isActive())
        later = time.time() + 2 * 3600 + 5
        with mock.patch("app.ui.panes.time.time", return_value=later):
            ticker.timeout.emit()  # the pane is still open
        self.assertIn("seen 2 h ago", row.detail.text())
        self.assertIs(page.mice.rows[0], row, "the same row: focus stays where it was")
        self.show("history")
        self.assertFalse(ticker.isActive())

    def test_an_ignored_mouse_not_seen_this_run_can_be_turned_back_on(self) -> None:
        self.controller.set_device_ignored("usb:046D:C08B:y", "G502 HERO", True)
        page = self.window.devices
        self.assertEqual(page.mice.rows[0].title.text(), "G502 HERO")
        self.assertIn("not seen since launch", page.mice.rows[0].detail.text())
        page.switches["usb:046D:C08B:y"].click()
        self.assertEqual(self.controller.ignored_devices, [])


class FirstRunTests(unittest.TestCase):
    """The Mac's first run: one Continue, the system's own request, then
    live status. Nothing here asks macOS for anything."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.application = support.application()

    def setUp(self) -> None:
        support.patch_settings(self)
        self.controller = support.make_controller(self)
        platform = support.as_platform(True)
        platform.__enter__()
        self.addCleanup(platform.__exit__, None, None, None)
        from app.ui.first_run import FirstRunWindow

        self.window = FirstRunWindow(self.controller)
        self.addCleanup(self.window.deleteLater)

    def visible_buttons(self) -> list:
        from PySide6.QtWidgets import QPushButton

        return [button.text() for button in self.window.findChildren(QPushButton) if button.isVisibleTo(self.window)]

    def test_one_continue_button_and_nothing_that_leads_away(self) -> None:
        self.assertEqual(self.visible_buttons(), ["Continue"])
        self.assertTrue(self.window.primary_button.isDefault())
        self.assertIn("macOS asks you to allow this next", self.window.body.text())

    def test_continue_asks_macos_and_waits_for_the_switch(self) -> None:
        from app import permissions

        requested = []
        self.window.filter_requested.connect(lambda: requested.append(True))
        with mock.patch.object(permissions, "request_accessibility") as ask:
            self.window.primary_button.click()
        ask.assert_called_once_with()
        self.assertEqual(requested, [True], "the window that keeps the filter running waits for access")
        self.assertEqual(self.window.state, "waiting")
        self.assertEqual(self.window.status_text.text(), "Waiting for permission…")
        self.assertEqual(self.visible_buttons(), ["Open System Settings…"])
        with mock.patch.object(permissions, "open_accessibility_settings") as open_pane, \
                mock.patch.object(permissions, "request_accessibility") as again:
            self.window.secondary_button.click()
        open_pane.assert_called_once_with()
        again.assert_not_called()

    def test_once_filtering_it_says_ready_and_offers_test_and_tune(self) -> None:
        with mock.patch("app.permissions.request_accessibility"):
            self.window.primary_button.click()
        self.controller.set_active(True)
        self.assertEqual(self.window.state, "ready")
        self.assertEqual(self.window.status_text.text(), "Ready")
        self.assertEqual(self.visible_buttons(), ["Test and Tune…", "Done"])
        tuned = []
        self.window.tune_requested.connect(lambda: tuned.append(True))
        self.window.secondary_button.click()
        self.assertEqual(tuned, [True])

    def test_only_a_mac_without_the_permission_starts_here(self) -> None:
        from app import permissions
        from app.ui import first_run

        with mock.patch.object(permissions, "needs_accessibility", return_value=True), \
                mock.patch.object(permissions, "has_accessibility", return_value=False):
            self.assertTrue(first_run.needed())
        with mock.patch.object(permissions, "needs_accessibility", return_value=True), \
                mock.patch.object(permissions, "has_accessibility", return_value=True):
            self.assertFalse(first_run.needed())
        with mock.patch.object(permissions, "needs_accessibility", return_value=False):
            self.assertFalse(first_run.needed())


class LookTests(PaneTestCase):
    def surfaces(self, mac: bool) -> list:
        """Every surface of `mac`'s layout, built and filled in."""
        from app.ui.first_run import FirstRunWindow
        from app.ui.tray import TrayFlyout
        from app.ui.window import MainWindow

        with support.as_platform(mac):
            window = MainWindow(self.controller)
            self.addCleanup(window.deleteLater)
            self.addCleanup(support.stop_timers, window, window.tune)
            window.refresh()
            extra = FirstRunWindow(self.controller) if mac else TrayFlyout(self.controller, lambda _on: None,
                                                                             lambda: None)
            self.addCleanup(extra.deleteLater)
        return [window, window.tune, extra]

    def test_every_control_has_a_name_a_screen_reader_can_say(self) -> None:
        from PySide6.QtWidgets import QAbstractButton, QComboBox, QLabel, QProgressBar

        from app.ui.charts import DailyRateChart, GapHistogram
        from app.ui.widgets import ClickPad

        self.controller.add_excluded_app("cs2.exe", "CS2")
        for mac in (True, False):
            for surface in self.surfaces(mac):
                for widget in surface.findChildren(QWidget):
                    named = widget.accessibleName() or (widget.text() if isinstance(widget, (QAbstractButton,)) else "")
                    if isinstance(widget, (QAbstractButton, QComboBox, QProgressBar, ClickPad, DailyRateChart,
                                           GapHistogram)):
                        self.assertTrue(named, f"{'macOS' if mac else 'Windows'}: a {type(widget).__name__} "
                                               f"without a name in {surface.windowTitle() or type(surface).__name__}")
                    if isinstance(widget, QLabel) and widget.textInteractionFlags() & \
                            Qt.TextInteractionFlag.LinksAccessibleByKeyboard:
                        self.assertTrue(widget.accessibleName(), "a link without a name")

    def test_copy_diagnostics_says_what_1_0_adds_and_keeps_keys_private(self) -> None:
        from app.core import Button

        self.controller.set_threshold(Button.RIGHT, 30)
        self.controller.set_wheel_fix(True)
        self.controller.add_excluded_app("com.example.secretgame", "Secret Game")
        self.controller.set_device_ignored("usb:046D:C08B:SERIAL123", "G502 HERO", True)
        self.window.about.diagnostics_button.click()
        text = QApplication.clipboard().text()
        for expected in ("windows: left 46 ms, right 30 ms", "side buttons: back off, forward off",
                         "wheel fix: on, 50 ms", "excluded apps: 1", "ignored devices: G502 HERO", "seen devices: none"):
            self.assertIn(expected, text)
        self.assertNotIn("SERIAL123", text)
        self.assertNotIn("secretgame", text)

    def test_the_about_pane_says_what_goes_online(self) -> None:
        self.assertIn("goes online only to check GitHub for updates", self.window.about.privacy_row.detail.text())

    def test_pop_up_buttons_open_the_system_menu_on_macos(self) -> None:
        from PySide6.QtWidgets import QComboBox, QStyle

        from app.ui import widgets

        hint = QStyle.StyleHint.SH_ComboBox_UseNativePopup
        plain = QComboBox()
        self.assertIs(widgets.native_popup(plain), plain)
        self.assertFalse(plain.style().styleHint(hint), "offscreen: nothing native to open")
        saved = list(widgets._NATIVE_POPUP_STYLE)
        self.addCleanup(lambda: widgets._NATIVE_POPUP_STYLE.__setitem__(slice(None), saved))
        widgets._NATIVE_POPUP_STYLE.clear()
        with mock.patch.object(widgets.QGuiApplication, "platformName", return_value="cocoa"):
            pickers = [QComboBox(), QComboBox()]
            for picker in pickers:
                widgets.pop_up_button(picker)
        self.assertTrue(pickers[0].style().styleHint(hint))
        self.assertIs(pickers[0].style(), pickers[1].style(), "one style for all of them")
        self.assertNotEqual(pickers[0].focusPolicy(), Qt.FocusPolicy.WheelFocus)
        # Every pop-up button in the window and Test and Tune goes through
        # pop_up_button.
        with mock.patch.object(widgets, "native_popup", wraps=widgets.native_popup) as native:
            from app.ui.window import MainWindow

            built = MainWindow(self.controller)
            self.addCleanup(built.deleteLater)
            self.addCleanup(support.stop_timers, built, built.tune)
        combos = built.findChildren(QComboBox) + built.tune.findChildren(QComboBox)
        self.assertEqual(native.call_count, len(combos))

    def test_no_style_sheet_reaches_a_native_control(self) -> None:
        """A style sheet on any widget sends it and everything inside it
        through Qt's style-sheet style, which on macOS draws pop-up buttons,
        spin boxes and menu buttons as boxes of its own (1.0's panes did,
        from one on each pane's scroll area). Native controls keep the
        platform's style; the only style sheet a native control may carry is
        Windows' accent fill on its own default button (base.set_primary)."""
        from PySide6.QtWidgets import (
            QAbstractButton,
            QAbstractSlider,
            QAbstractSpinBox,
            QComboBox,
            QLineEdit,
            QProgressBar,
            QPushButton,
        )

        from app.ui import widgets

        native = (QComboBox, QAbstractSpinBox, QLineEdit, QAbstractButton, QProgressBar, QAbstractSlider)
        self.assertEqual(QApplication.instance().styleSheet(), "", "an application-wide style sheet reaches everything")
        # Every surface with the controls its states add: rows of apps and
        # devices with their buttons and switches, Custom's windows, and Test
        # and Tune at each step.
        self.controller.add_excluded_app("cs2.exe", "CS2")
        self.controller.set_device_ignored("usb:046D:C08B:y", "G502 HERO", True)
        self.controller.set_strength("custom")

        def describe(widget) -> str:
            return f"{type(widget).__name__} {widget.accessibleName() or getattr(widget, 'text', lambda: '')() or widget.objectName()!r}"

        for mac in (True, False):
            surfaces = self.surfaces(mac)
            built, tune = surfaces[0], surfaces[1]
            with support.as_platform(mac):
                built.show_tune()
                tune._on_pad_press(None, None)
                while tune.phase == "single":
                    tune._on_pad_press(900.0, 960.0)
                while tune.phase == "double":
                    tune._on_pad_press(900.0, 960.0)
                    tune._on_pad_press(150.0, 210.0)
                tune._primary()
                self.assertEqual(tune.step, "tune")
                built.refresh()
            found = set()
            for surface in surfaces:
                for control in surface.findChildren(QWidget):
                    if not isinstance(control, native) or isinstance(control, widgets.Switch):
                        continue
                    found.add(type(control).__mro__[1] if type(control).__module__.startswith("app.") else type(control))
                    ancestor = control.parentWidget()
                    while ancestor is not None:
                        self.assertEqual(ancestor.styleSheet(), "",
                                         f"{'macOS' if mac else 'Windows'}: {describe(ancestor)} has a style sheet, "
                                         f"which reaches {describe(control)}")
                        ancestor = ancestor.parentWidget()
                    own = control.styleSheet()
                    accent = not mac and isinstance(control, QPushButton) and control.isDefault()
                    if own:
                        self.assertTrue(accent, f"{describe(control)} styles itself: {own!r}")
                    else:
                        self.assertNotEqual(control.style().metaObject().className(), "QStyleSheetStyle", describe(control))
            self.assertTrue({QComboBox, QPushButton, QProgressBar} <= found, found)
            if not mac:
                defaults = [button for button in built.findChildren(QPushButton) + tune.findChildren(QPushButton)
                            if button.isDefault()]
                self.assertTrue(defaults and all(button.styleSheet() for button in defaults), "the Windows accent case ran")
            # Each pane lets the window's colour show through, without a
            # style sheet to say so.
            for page in built.pages:
                area = page.parentWidget().parentWidget()
                self.assertFalse(page.autoFillBackground(), page.page_title)
                self.assertFalse(area.viewport().autoFillBackground(), page.page_title)
            tune.close()

    def test_every_surface_renders_in_both_looks_and_both_layouts(self) -> None:
        from app.ui import theme, widgets

        for mac in (True, False):
            surfaces = self.surfaces(mac)
            window = surfaces[0]
            with support.as_platform(mac):
                for dark in (False, True):
                    widgets.set_look(theme.current_look(dark))
                    window.apply_look()
                    for index in range(len(window.pages)):
                        window._show_page(index)
                        window.grab()
                    for surface in surfaces[1:]:
                        surface.grab()


if __name__ == "__main__":
    unittest.main()
