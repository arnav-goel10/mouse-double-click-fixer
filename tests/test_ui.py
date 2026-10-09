"""Offscreen tests for the window, including a full calibration run."""

import contextlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import logging

# The app logs failures on purpose; keep them out of the test output.
logging.getLogger("app").addHandler(logging.NullHandler())
logging.getLogger("app").propagate = False

from run import _unhide_qt_plugins

_unhide_qt_plugins()

try:
    from PySide6.QtWidgets import QApplication
except ImportError:  # pragma: no cover - PySide6 is a hard dependency
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class WindowTests(unittest.TestCase):
    application = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from app import settings

        directory = Path(tempfile.mkdtemp())
        patcher = mock.patch.object(settings, "config_dir", return_value=directory)
        patcher.start()
        self.addCleanup(patcher.stop)
        legacy = mock.patch.object(settings, "LEGACY_PATH", directory / "absent.json")
        legacy.start()
        self.addCleanup(legacy.stop)

        from app.controller import AppController
        from app.ui.window import MainWindow

        self.controller = AppController()
        self.window = MainWindow(self.controller)
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(LiveWindowTests.stop_timers, self.window)

    def page_index(self, key: str) -> int:
        from app.ui.window import PAGES

        return [name for name, _title in PAGES].index(key)

    def test_window_opens_on_the_filter_pane(self) -> None:
        self.assertEqual(self.window.stack.currentIndex(), 0)
        self.assertEqual(self.window.title_label.text(), "Bounce Filter")
        self.assertFalse(self.window.filter_page.switch.isChecked())

    def test_sidebar_switches_panes(self) -> None:
        self.window.sidebar.set_current(self.page_index("general"))
        self.assertEqual(self.window.stack.currentIndex(), self.page_index("general"))
        self.assertEqual(self.window.title_label.text(), "General")

    def test_light_and_dark_render_without_errors(self) -> None:
        from app.ui import widgets
        from app.ui.theme import current_look

        for dark in (True, False):
            widgets.set_look(current_look(dark))
            self.window.apply_look()
            self.window.grab()

    def test_test_pad_records_gaps(self) -> None:
        page = self.window.test_page
        page._on_pad_press(None, None)
        page._on_pad_press(400.0, 460.0)
        page._on_pad_press(9.0, 40.0)
        self.assertEqual(page.clicks, 3)
        self.assertEqual(page.last_value.text(), "9 ms")
        self.assertEqual(page.shortest_gap, 9.0)
        page.reset()
        self.assertEqual(page.clicks, 0)
        self.assertEqual(page.count_value.text(), "0")

    def test_opening_calibrate_alone_does_not_pause_the_filter(self) -> None:
        # Only measuring pauses it (see CalibrationPauseTests); the intro
        # is just reading.
        with mock.patch.object(self.controller, "suspend") as suspend:
            self.window._show_page(self.page_index("calibrate"))
            suspend.assert_not_called()

    def test_calibration_runs_end_to_end_and_applies(self) -> None:
        from app.core import REQUIRED_DOUBLE_CLICKS, REQUIRED_SINGLE_CLICKS

        page = self.window.calibrate
        self.window._show_page(self.page_index("calibrate"))
        page._advance()  # leave the intro
        self.assertEqual(page.phase, "single")

        for _ in range(REQUIRED_SINGLE_CLICKS):
            page._on_pad_press(900.0, 960.0)
        page._on_pad_press(12.0, 40.0)  # a bounce
        self.assertEqual(page.phase, "double")

        for _ in range(REQUIRED_DOUBLE_CLICKS):
            page._on_pad_press(900.0, 960.0)  # first press of the pair
            page._on_pad_press(150.0, 210.0)  # second press
        self.assertEqual(page.phase, "done")

        suggestion = page.suggestion
        self.assertIsNotNone(suggestion)
        self.assertLess(suggestion.threshold_ms, 150, "never blocks the measured double-click")
        self.assertEqual(page.recommended_value.text(), f"{suggestion.threshold_ms} ms")

        page._advance()  # apply
        self.assertEqual(self.controller.threshold_ms, suggestion.threshold_ms)
        self.assertTrue(self.controller.calibrated)
        self.assertEqual(self.window.stack.currentIndex(), 0, "returns to the filter pane")

    def test_calibration_without_double_clicks_reports_instead_of_applying(self) -> None:
        page = self.window.calibrate
        page._advance()
        page._on_pad_press(900.0, 960.0)
        page._finish()
        self.assertIsNone(page.suggestion)
        self.assertFalse(page.primary_button.isEnabled())

    def test_settings_changes_reach_the_controller(self) -> None:
        from app.core import Button

        page = self.window.filter_page
        page.slider.setValue(35)
        self.assertEqual(self.controller.threshold_ms, 35)
        self.assertEqual(page.value.text(), "35 ms")

        page.button_switches[Button.RIGHT].click()
        self.assertIn(Button.RIGHT, self.controller.buttons)

        # The last button cannot be turned off; something must stay protected.
        page.button_switches[Button.RIGHT].click()
        page.button_switches[Button.LEFT].click()
        self.assertEqual(self.controller.buttons, [Button.LEFT])
        self.assertTrue(page.button_switches[Button.LEFT].isChecked())

    def test_permission_notice_follows_the_live_setting(self) -> None:
        notice = self.window.filter_page.permission
        with mock.patch("app.permissions.needs_accessibility", return_value=True), mock.patch(
            "app.permissions.has_accessibility", return_value=False
        ):
            self.window._permission_granted = True
            self.window._check_permission()
            self.assertFalse(notice.isHidden(), "missing permission must be announced")

        with mock.patch("app.permissions.needs_accessibility", return_value=True), mock.patch(
            "app.permissions.has_accessibility", return_value=True
        ), mock.patch("app.permissions.event_tap_allowed", return_value=True):
            self.window._check_permission()
            self.assertTrue(notice.isHidden(), "the notice must go once permission is granted")

    def test_filter_requested_before_permission_starts_once_granted(self) -> None:
        with mock.patch("app.permissions.needs_accessibility", return_value=True), mock.patch(
            "app.permissions.has_accessibility", return_value=False
        ), mock.patch.object(self.controller, "set_active", return_value=False) as set_active, \
                mock.patch("app.permissions.open_accessibility_settings") as ask:
            self.window._permission_granted = False
            self.window.request_filter(True)
            set_active.assert_not_called()
            ask.assert_called_once()
            self.assertTrue(self.window.filter_page.switch.isChecked(), "switch shows the request")
            self.assertIn("Waiting", self.window.filter_page.status_row.detail.text())

        with mock.patch("app.permissions.needs_accessibility", return_value=True), mock.patch(
            "app.permissions.has_accessibility", return_value=True
        ), mock.patch("app.permissions.event_tap_allowed", return_value=True), \
                mock.patch.object(self.controller, "set_active", return_value=True) as started:
            self.window._check_permission()
            started.assert_called_once_with(True)

    def test_revoked_permission_turns_the_filter_off(self) -> None:
        with mock.patch("app.permissions.needs_accessibility", return_value=True), mock.patch(
            "app.permissions.has_accessibility", return_value=False
        ), mock.patch.object(type(self.controller), "active", new_callable=mock.PropertyMock, return_value=True), \
                mock.patch.object(self.controller, "stop_for_permission") as stop, \
                mock.patch.object(self.controller, "set_active") as set_active:
            self.window._permission_granted = True
            self.window._check_permission()
            stop.assert_called_once_with()
            # Not set_active(False): that would save "off", and the filter would
            # not come back by itself once access is granted again.
            set_active.assert_not_called()
            self.assertTrue(self.window._enable_when_granted)

    def test_window_reopens_at_the_size_it_was_left(self) -> None:
        from app.ui.window import MainWindow

        # The offscreen test screen is only 800 x 600 and Qt rightly shrinks a
        # restored window to fit its screen, so this checks the save/restore
        # round trip at a size well inside it.
        self.window.setMinimumSize(300, 200)
        self.window.show()
        self.window.resize(640, 460)
        self.application.processEvents()
        expected = self.window.size()
        self.window.save_geometry()

        reopened = MainWindow(self.controller)
        reopened.setMinimumSize(300, 200)
        reopened._restore_geometry()
        self.addCleanup(reopened.deleteLater)
        self.addCleanup(LiveWindowTests.stop_timers, reopened)
        self.assertEqual(reopened.size(), expected)

    def test_content_stays_readable_when_wide_and_fits_when_narrow(self) -> None:
        from app.ui.window import COLUMN_MAX

        self.window.show()
        self.window.resize(1600, 900)
        self.application.processEvents()
        self.assertLessEqual(self.window.filter_page.column.width(), COLUMN_MAX)

        self.window.resize(self.window.minimumSize())
        self.application.processEvents()
        page = self.window.filter_page
        viewport = page.parentWidget()
        self.assertLessEqual(page.column.geometry().right(), viewport.width(), "nothing is clipped")

    def test_closing_hides_instead_of_quitting(self) -> None:
        from PySide6.QtCore import QEvent

        self.window.show()
        self.window.closeEvent(QEvent(QEvent.Type.Close))
        self.assertTrue(self.window.isHidden())


class FakeFilter:
    """Stands in for GlobalClickFilter: the lifecycle without a hook."""

    fail_with = None

    def __init__(self, threshold_ms, buttons, on_event=None, on_error=None,
                 permission_ok=None, on_permission_lost=None) -> None:
        self.started = self.stopped = False
        self.on_permission_lost = on_permission_lost

    @property
    def running(self) -> bool:
        return self.started and not self.stopped

    def start(self) -> None:
        if FakeFilter.fail_with:
            from app.platform import HookError

            raise HookError(FakeFilter.fail_with)
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def update(self, **_changes) -> None:
        pass


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class LiveWindowTests(unittest.TestCase):
    """A shown window over a controller whose filter really starts and stops
    (a stand-in hook), with the window's activation under the test's control."""

    application = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from app import settings

        directory = Path(tempfile.mkdtemp())
        FakeFilter.fail_with = None
        patches = [
            mock.patch.object(settings, "config_dir", return_value=directory),
            mock.patch.object(settings, "LEGACY_PATH", directory / "absent.json"),
            mock.patch("app.controller.GlobalClickFilter", FakeFilter),
            # Granted, so the window takes the plain path on every platform.
            mock.patch("app.permissions.needs_accessibility", return_value=False),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        from app.controller import AppController
        from app.ui.window import MainWindow

        self.controller = AppController()
        self.window = MainWindow(self.controller)
        self.addCleanup(self.window.deleteLater)
        # The window outlives the test until Qt deletes it; its timers must
        # not act after the stand-in hook is gone (a real hook would start).
        self.addCleanup(self.stop_timers, self.window)
        self.active_window = True
        self.window.isActiveWindow = lambda: self.active_window
        self.window.setMinimumSize(300, 200)

    def page_index(self, key: str) -> int:
        from app.ui.window import PAGES

        return [name for name, _title in PAGES].index(key)

    @staticmethod
    def stop_timers(window) -> None:
        from PySide6.QtCore import QTimer

        for timer in window.findChildren(QTimer):
            timer.stop()
        window.hide()

    def set_active_window(self, active: bool) -> None:
        from PySide6.QtCore import QEvent

        self.active_window = active
        self.window.changeEvent(QEvent(QEvent.Type.ActivationChange))

    def close(self) -> None:
        from PySide6.QtCore import QEvent

        self.window.closeEvent(QEvent(QEvent.Type.Close))


class CalibrationPauseTests(LiveWindowTests):
    def open_calibrate(self) -> None:
        self.controller.set_active(True)
        self.window.show()
        self.window._show_page(self.page_index("calibrate"))

    def test_the_intro_does_not_pause_and_begin_does(self) -> None:
        self.open_calibrate()
        self.assertTrue(self.controller.active, "reading the intro leaves filtering on")
        self.window.calibrate._advance()  # Begin
        self.assertFalse(self.controller.active)
        self.assertTrue(self.controller.suspended)

    def test_leaving_the_window_resumes_and_coming_back_pauses(self) -> None:
        self.open_calibrate()
        self.window.calibrate._advance()
        self.set_active_window(False)
        self.assertTrue(self.controller.active, "clicks in the other app are filtered")
        self.set_active_window(True)
        self.assertFalse(self.controller.active)

    def test_minimizing_resumes(self) -> None:
        from PySide6.QtCore import QEvent

        self.open_calibrate()
        self.window.calibrate._advance()
        with mock.patch.object(self.window, "isMinimized", return_value=True):
            self.window.changeEvent(QEvent(QEvent.Type.WindowStateChange))
            self.assertTrue(self.controller.active)

    def test_finishing_resumes(self) -> None:
        self.open_calibrate()
        page = self.window.calibrate
        page._advance()
        page._finish()
        self.assertEqual(page.phase, "done")
        self.assertTrue(self.controller.active, "the result screen measures nothing")

    def test_reopening_after_a_result_does_not_pause(self) -> None:
        self.open_calibrate()
        page = self.window.calibrate
        page._advance()
        page._finish()
        self.close()
        self.assertEqual(page.phase, "intro", "an unapplied result starts over")
        self.window.show()
        self.assertTrue(self.controller.active)

    def test_closing_mid_measurement_resumes_and_starts_over(self) -> None:
        self.open_calibrate()
        self.window.calibrate._advance()
        self.close()
        self.assertTrue(self.controller.active)
        self.assertEqual(self.window.calibrate.phase, "intro")

    def test_the_menu_toggle_on_the_result_screen_turns_filtering_on(self) -> None:
        self.window.show()
        self.window._show_page(self.page_index("calibrate"))
        page = self.window.calibrate
        page._advance()
        page._finish()
        self.window.request_filter(True)
        self.assertTrue(self.controller.active)

    def test_the_menu_toggle_while_measuring_waits_for_the_end(self) -> None:
        self.window.show()
        self.window._show_page(self.page_index("calibrate"))
        page = self.window.calibrate
        page._advance()
        self.window.request_filter(True)
        self.assertFalse(self.controller.active, "the pad keeps measuring raw clicks")
        self.assertTrue(self.controller.wanted)
        self.assertTrue(self.window.filter_page.switch.isChecked())
        # The same words as the menus' status line.
        self.assertEqual(self.window.filter_page.status_row.detail.text(), f"{self.controller.status_text()}.")
        page._finish()
        self.assertTrue(self.controller.active)

    def test_the_menu_toggle_while_paused_turns_it_off(self) -> None:
        self.open_calibrate()
        self.window.calibrate._advance()
        self.assertTrue(self.controller.wanted)
        self.window.request_filter(not self.controller.wanted)
        self.assertFalse(self.controller.wanted)
        self.assertFalse(self.controller.settings["fix_enabled"])
        self.window.calibrate._finish()
        self.assertFalse(self.controller.active, "stays off after calibration")


class KeepFilterAliveTests(LiveWindowTests):
    """Fail open, and never lose the user's on (plan item 6)."""

    def setUp(self) -> None:
        super().setUp()
        warning = mock.patch("app.ui.window.QMessageBox.warning")
        self.dialog = warning.start()
        self.addCleanup(warning.stop)

    def fire_retry(self) -> None:
        """What the single-shot retry timer does when it runs out."""
        self.window._retry_timer.stop()
        self.window._retry_start()

    def test_revoked_access_is_caught_even_when_ax_still_says_yes(self) -> None:
        self.controller.set_active(True)
        with mock.patch("app.permissions.needs_accessibility", return_value=True), \
                mock.patch("app.permissions.has_accessibility", return_value=True), \
                mock.patch("app.permissions.event_tap_allowed", return_value=False) as probe:
            self.window._permission_granted = True
            self.window._check_permission()
            probe.assert_called()
            self.assertFalse(self.controller.active, "no filtering tap on a dead grant")
            self.assertTrue(self.controller.settings["fix_enabled"])
            self.assertTrue(self.controller.waiting_for_permission)
            self.window._check_permission()  # still refused: no restart attempt
            self.assertFalse(self.controller.active)
        with mock.patch("app.permissions.needs_accessibility", return_value=True), \
                mock.patch("app.permissions.has_accessibility", return_value=True), \
                mock.patch("app.permissions.event_tap_allowed", return_value=True):
            self.window._check_permission()
            self.assertTrue(self.controller.active, "back on once access returns")

    def mac_access(self, ax: bool, tap: bool):
        """macOS permission answers: AXIsProcessTrusted, then the tap probe."""
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch("app.permissions.needs_accessibility", return_value=True))
        stack.enter_context(mock.patch("app.permissions.has_accessibility", return_value=ax))
        stack.enter_context(mock.patch("app.permissions.event_tap_allowed", return_value=tap))
        return stack

    def test_a_tap_refused_at_launch_for_lack_of_access_waits_for_it(self) -> None:
        from app import permissions

        # macOS still reports the app as allowed, but the grant is dead, so
        # the tap is refused.
        self.controller._store(fix_enabled=True)
        self.window.show()
        FakeFilter.fail_with = "macOS refused the event tap."
        with self.mac_access(ax=True, tap=False):
            self.window._permission_granted = True
            self.window.restore_filter(background=False)
            self.assertTrue(self.controller.waiting_for_permission)
            self.assertEqual(self.controller.failure, "", "a wait, not a failure")
            self.assertEqual(self.controller.status_text(), f"Waiting for {permissions.pane_name()} permission")
            self.assertFalse(self.window.filter_page.permission.isHidden(), "the permission row explains it")
            self.assertFalse(self.window._retry_timer.isActive(), "the permission poll takes it from here")
            self.window._check_permission()
            self.assertFalse(self.controller.active)
        self.dialog.assert_not_called()
        FakeFilter.fail_with = None
        with self.mac_access(ax=True, tap=True):
            self.window._check_permission()
            self.assertTrue(self.controller.active, "starts once access is back")
            self.assertFalse(self.controller.waiting_for_permission)

    def test_a_tap_refused_with_access_in_place_is_a_failure(self) -> None:
        self.controller._store(fix_enabled=True)
        self.window.show()
        FakeFilter.fail_with = "The window server isn't ready."
        with self.mac_access(ax=True, tap=True):
            self.window._permission_granted = True
            self.window.restore_filter(background=False)
        self.assertFalse(self.controller.waiting_for_permission)
        self.assertEqual(self.controller.failure_detail, "The window server isn't ready.")
        self.dialog.assert_called_once()

    def test_a_filter_that_lost_its_access_is_stopped_and_waits(self) -> None:
        import threading

        self.controller.set_active(True)
        filter_ = self.controller._filter
        with self.mac_access(ax=True, tap=False):
            self.window._permission_granted = True
            hook = threading.Thread(target=filter_.on_permission_lost)
            hook.start()
            hook.join()
            self.assertFalse(filter_.stopped, "handled on the UI thread, not the hook's")
            self.application.processEvents()
            self.assertTrue(filter_.stopped)
            self.assertFalse(self.controller.active)
            self.assertTrue(self.controller.waiting_for_permission)
            self.assertTrue(self.controller.settings["fix_enabled"], "the user's choice is kept")
            self.window._check_permission()  # still refused: nothing starts
            self.assertFalse(self.controller.active)
        with self.mac_access(ax=True, tap=True):
            self.window._check_permission()
            self.assertTrue(self.controller.active, "back on once access returns")

    def test_the_probe_only_runs_while_it_matters(self) -> None:
        with mock.patch("app.permissions.needs_accessibility", return_value=True), \
                mock.patch("app.permissions.has_accessibility", return_value=True), \
                mock.patch("app.permissions.event_tap_allowed") as probe:
            self.window._permission_granted = True
            self.window._check_permission()
            probe.assert_not_called()

    def test_a_dead_hook_keeps_the_choice_and_stays_quiet_when_hidden(self) -> None:
        self.controller.set_active(True)
        self.window._on_hook_failed("The hook stopped.")
        self.assertTrue(self.controller.settings["fix_enabled"])
        self.assertEqual(self.controller.status_text(), "The filter stopped")
        self.dialog.assert_not_called()

    def test_failures_show_a_dialog_only_when_the_window_is_up(self) -> None:
        self.controller._store(fix_enabled=True)
        FakeFilter.fail_with = "macOS refused the event tap."
        self.window.request_filter(True)
        self.dialog.assert_not_called()
        self.assertEqual(self.controller.status_text(), "Couldn’t start the filter")
        self.window.show()
        self.assertIn("refused", self.window.filter_page.status_row.detail.text())
        self.window.request_filter(True)
        self.dialog.assert_called_once()

    def test_a_background_start_retries_on_schedule(self) -> None:
        from app.ui.window import RETRY_AT_S

        self.controller._store(fix_enabled=True)
        FakeFilter.fail_with = "macOS refused the event tap."
        self.window.restore_filter(background=True)
        self.assertTrue(self.window._retry_timer.isActive())
        waits = [self.window._retry_timer.interval()]
        for _ in range(len(RETRY_AT_S) - 1):
            self.fire_retry()
            waits.append(self.window._retry_timer.interval())
        self.fire_retry()  # the last try
        self.assertFalse(self.window._retry_timer.isActive(), "then it is left to the user")
        marks = [sum(waits[: index + 1]) / 1000 for index in range(len(waits))]
        self.assertEqual(tuple(marks), RETRY_AT_S)
        self.assertTrue(self.controller.settings["fix_enabled"], "still on for the next login")
        self.dialog.assert_not_called()

    def test_a_retry_that_works_ends_the_retries(self) -> None:
        self.controller._store(fix_enabled=True)
        FakeFilter.fail_with = "busy"
        self.window.restore_filter(background=True)
        FakeFilter.fail_with = None
        self.fire_retry()
        self.assertTrue(self.controller.active)
        self.assertFalse(self.window._retry_timer.isActive())
        self.assertEqual(self.controller.failure, "")

    def test_turning_off_ends_the_retries(self) -> None:
        self.controller._store(fix_enabled=True)
        FakeFilter.fail_with = "busy"
        self.window.restore_filter(background=True)
        self.window.request_filter(False)
        self.assertFalse(self.window._retry_timer.isActive())

    def test_a_saved_on_that_keeps_failing_can_be_switched_off(self) -> None:
        self.controller._store(fix_enabled=True)
        FakeFilter.fail_with = "busy"
        self.window.restore_filter(background=True)
        self.assertTrue(self.window.filter_page.switch.isChecked(), "shows the user's on")
        self.window.filter_page.switch.click()
        self.assertFalse(self.controller.settings["fix_enabled"])
        self.assertFalse(self.window._retry_timer.isActive())
        self.assertFalse(self.window.filter_page.switch.isChecked())
        self.assertEqual(self.controller.status_text(), "Off")

    def test_an_opened_window_does_not_retry_by_itself(self) -> None:
        self.controller._store(fix_enabled=True)
        FakeFilter.fail_with = "busy"
        self.window.show()
        self.window.restore_filter(background=False)
        self.assertFalse(self.window._retry_timer.isActive())
        self.dialog.assert_called_once()

    def test_a_dead_tap_is_rebuilt(self) -> None:
        self.controller.set_active(True)
        old = self.controller._filter
        self.assertTrue(self.window._health_timer.isActive(), "watched while filtering")
        old.tap_alive = lambda: False
        self.window._check_health()
        self.assertTrue(old.stopped)
        self.assertIsNot(self.controller._filter, old)
        self.assertTrue(self.controller.active)
        self.controller.set_active(False)
        self.assertFalse(self.window._health_timer.isActive())

    def test_a_live_tap_is_left_alone(self) -> None:
        self.controller.set_active(True)
        current = self.controller._filter
        current.tap_alive = lambda: True
        self.window._check_health()
        self.assertIs(self.controller._filter, current)

    def test_wake_rebuilds_a_filter_that_is_on(self) -> None:
        self.controller.set_active(True)
        old = self.controller._filter
        self.window.system_woke()
        self.assertTrue(old.stopped)
        self.assertTrue(self.controller.active)

    def test_a_restart_that_fails_retries_and_says_so_once(self) -> None:
        self.controller.set_active(True)
        self.window.show()
        FakeFilter.fail_with = "The window server isn't ready."
        self.window.system_woke()
        self.dialog.assert_called_once()  # the window is up, so it says so
        self.assertTrue(self.window._retry_timer.isActive())
        self.fire_retry()
        self.dialog.assert_called_once()  # but not again for every retry
        self.assertIn("isn't ready", self.window.filter_page.status_row.detail.text())
        FakeFilter.fail_with = None
        self.fire_retry()
        self.assertTrue(self.controller.active)

    def test_a_restart_that_fails_while_hidden_shows_no_dialog(self) -> None:
        self.controller.set_active(True)
        FakeFilter.fail_with = "The window server isn't ready."
        self.window.system_woke()
        self.dialog.assert_not_called()
        self.assertEqual(self.controller.status_text(), "Couldn’t start the filter")
        self.assertTrue(self.window._retry_timer.isActive())

    def test_wake_leaves_an_off_filter_off(self) -> None:
        self.window.system_woke()
        self.assertFalse(self.controller.active)

    def test_switching_sessions_stops_and_restarts(self) -> None:
        self.controller.set_active(True)
        self.window.session_resigned()
        self.assertFalse(self.controller.active, "no tap in a session nobody is using")
        self.assertTrue(self.controller.settings["fix_enabled"])
        self.window.system_woke()  # nothing starts while away
        self.assertFalse(self.controller.active)
        self.window.session_activated()
        self.assertTrue(self.controller.active)

    def test_a_retry_due_after_switching_away_starts_nothing(self) -> None:
        self.controller._store(fix_enabled=True)
        FakeFilter.fail_with = "busy"
        self.window.restore_filter(background=True)
        self.assertTrue(self.window._retry_timer.isActive())
        self.window.session_resigned()
        self.assertFalse(self.window._retry_timer.isActive(), "no retry is left to fire while away")
        FakeFilter.fail_with = None
        self.window._retry_start()  # one that fired anyway
        self.assertFalse(self.controller.active, "no tap in a session nobody is using")
        self.window.session_activated()
        self.assertTrue(self.controller.active)

    def test_leaving_a_calibration_after_switching_away_starts_nothing(self) -> None:
        self.controller.set_active(True)
        self.window.show()
        self.window._show_page(self.page_index("calibrate"))
        self.window.calibrate._advance()
        self.assertTrue(self.controller.suspended)
        self.window.session_resigned()
        self.set_active_window(False)  # the pause ends as the window loses focus
        self.assertFalse(self.controller.active, "no tap in a session nobody is using")
        self.assertTrue(self.controller.settings["fix_enabled"])
        self.window.session_activated()
        self.assertTrue(self.controller.active)

    def test_wake_does_not_end_a_calibration_pause(self) -> None:
        self.controller.set_active(True)
        self.window.show()
        self.window._show_page(self.page_index("calibrate"))
        self.window.calibrate._advance()
        self.window.system_woke()
        self.assertFalse(self.controller.active)
        self.assertTrue(self.controller.suspended)


class AccessibilityTests(LiveWindowTests):
    """What VoiceOver and Narrator are told, and where keyboard focus shows."""

    def accessible(self, widget):
        from PySide6.QtGui import QAccessible

        return QAccessible.queryAccessibleInterface(widget)

    def test_switch_is_a_named_toggle_with_its_state(self) -> None:
        from PySide6.QtGui import QAccessible

        switch = self.window.filter_page.switch
        interface = self.accessible(switch)
        self.assertEqual(interface.role(), QAccessible.Role.CheckBox)
        self.assertEqual(interface.text(QAccessible.Text.Name), "Bounce filter")
        self.assertTrue(interface.state().checkable)
        self.assertFalse(interface.state().checked)
        self.assertIn("Toggle", interface.actionInterface().actionNames())
        switch.setChecked(True, animate=False)
        self.assertTrue(interface.state().checked)

    def test_every_switch_is_accessible_by_name(self) -> None:
        from PySide6.QtGui import QAccessible

        from app.ui.widgets import Switch

        for switch in self.window.findChildren(Switch):
            interface = self.accessible(switch)
            self.assertEqual(interface.role(), QAccessible.Role.CheckBox)
            self.assertTrue(interface.text(QAccessible.Text.Name), "every switch has a name")

    def test_setting_a_switch_from_code_never_reaches_its_handler(self) -> None:
        switch = self.window.filter_page.switch
        with mock.patch.object(self.controller, "set_active") as set_active:
            switch.setChecked(True, animate=False)
            self.window.refresh()  # sets it back to off, the real state
            set_active.assert_not_called()
            self.assertFalse(switch.isChecked())
            switch.click()  # the user
            set_active.assert_called_once_with(True)

    def test_the_users_click_on_a_switch_turns_the_filter_on(self) -> None:
        self.window.filter_page.switch.click()
        self.assertTrue(self.controller.active)
        self.assertTrue(self.window.filter_page.switch.isChecked())

    def test_space_flips_a_focused_switch(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        from app.core import Button

        switch = self.window.filter_page.button_switches[Button.RIGHT]
        self.window.show()
        before = switch.isChecked()
        QTest.keyClick(switch, Qt.Key.Key_Space)
        self.assertNotEqual(switch.isChecked(), before)
        self.assertEqual(Button.RIGHT in self.controller.buttons, switch.isChecked())

    def test_sidebar_is_a_list_of_its_panes(self) -> None:
        from PySide6.QtGui import QAccessible

        from app.ui.window import PAGES

        interface = self.accessible(self.window.sidebar)
        self.assertEqual(interface.role(), QAccessible.Role.List)
        self.assertEqual(interface.childCount(), len(PAGES))
        names = [interface.child(index).text(QAccessible.Text.Name) for index in range(interface.childCount())]
        self.assertEqual(names, [title for _key, title in PAGES])
        for index in range(interface.childCount()):
            item = interface.child(index)
            self.assertEqual(item.role(), QAccessible.Role.ListItem)
            self.assertTrue(item.state().selectable)
            self.assertFalse(item.state().checkable, "no check box to announce")
        self.assertTrue(interface.child(0).state().selected)
        self.window.sidebar.set_current(self.page_index("calibrate"))
        selected = [interface.child(index).state().selected for index in range(interface.childCount())]
        self.assertEqual(selected, [index == self.page_index("calibrate") for index in range(len(PAGES))])

    def test_sidebar_keys_move_between_panes(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        from app.ui.window import PAGES

        sidebar = self.window.sidebar
        self.window.show()
        self.window.activateWindow()
        self.application.processEvents()
        sidebar.setFocus()
        QTest.keyClick(sidebar, Qt.Key.Key_Down)
        self.assertEqual(self.window.stack.currentIndex(), 1)
        QTest.keyClick(sidebar, Qt.Key.Key_End)
        self.assertEqual(self.window.stack.currentIndex(), len(PAGES) - 1)
        QTest.keyClick(sidebar, Qt.Key.Key_Home)
        self.assertEqual(self.window.stack.currentIndex(), 0)
        self.assertTrue(sidebar.shows_focus(), "keyboard use shows where focus is")

    def test_sidebar_clicks_select_and_nothing_deselects(self) -> None:
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest

        sidebar = self.window.sidebar
        self.window.show()
        self.application.processEvents()
        general = sidebar.visualItemRect(sidebar.item(self.page_index("general"))).center()
        QTest.mouseClick(sidebar.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, general)
        self.assertEqual(self.window.stack.currentIndex(), self.page_index("general"))
        QTest.mouseClick(sidebar.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, general)
        self.assertTrue(sidebar.item(self.page_index("general")).isSelected())
        below = QPoint(20, sidebar.viewport().height() - 2)
        QTest.mouseClick(sidebar.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, below)
        self.assertTrue(sidebar.item(self.page_index("general")).isSelected(), "a click below the entries changes nothing")
        self.assertFalse(sidebar.shows_focus(), "a click shows no keyboard focus")

    def test_scroll_areas_are_not_tab_stops(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QScrollArea

        areas = self.window.findChildren(QScrollArea)
        self.assertEqual(len(areas), 4)
        for area in areas:
            self.assertEqual(area.focusPolicy(), Qt.FocusPolicy.NoFocus)

    def test_switch_keeps_its_focus_ring_across_window_switches(self) -> None:
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtGui import QFocusEvent

        switch = self.window.filter_page.switch
        switch.focusInEvent(QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.TabFocusReason))
        self.assertTrue(switch._keyboard_focus)
        switch.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.ActiveWindowFocusReason))
        switch.focusInEvent(QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.ActiveWindowFocusReason))
        self.assertTrue(switch._keyboard_focus, "back from another app: the ring is still there")
        switch.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.MouseFocusReason))
        self.assertFalse(switch._keyboard_focus)
        switch.focusInEvent(QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.MouseFocusReason))
        self.assertFalse(switch._keyboard_focus, "a click earns no ring")

    def test_switch_and_sidebar_render_in_both_looks(self) -> None:
        from app.ui import widgets
        from app.ui.theme import current_look

        self.window.show()
        self.window.sidebar.setFocus()
        self.window.sidebar._keyboard_focus = True
        for dark in (True, False):
            widgets.set_look(current_look(dark))
            self.window.apply_look()
            self.assertFalse(self.window.sidebar.grab().isNull())
            self.assertFalse(self.window.filter_page.switch.grab().isNull())


class UpdateSettingsTests(LiveWindowTests):
    """General's update switches, through the real updater (no network:
    nothing here checks or downloads)."""

    def general(self):
        from app.ui.window import GeneralPage
        from app.updater import Updater

        updater = Updater(self.controller)
        updater.kind = "mac"  # an installed copy, so the section shows
        page = GeneralPage(self.controller, updater)
        self.addCleanup(page.deleteLater)
        page.refresh(True)
        return page, updater

    def test_checking_and_installing_are_separate_switches(self) -> None:
        from app import settings

        page, updater = self.general()
        self.assertTrue(page.auto_check_switch.isChecked())
        self.assertTrue(page.auto_install_switch.isChecked())
        self.assertTrue(page.auto_install_switch.isEnabled())
        page.auto_check_switch.click()
        self.assertFalse(updater.auto_check)
        self.assertFalse(settings.load()["auto_check"], "saved")
        self.assertTrue(self.controller.settings["auto_update"], "installing is its own choice")
        self.assertFalse(page.auto_install_switch.isEnabled(), "nothing installs without a check")
        page.auto_check_switch.click()
        self.assertTrue(page.auto_install_switch.isEnabled())
        page.auto_install_switch.click()
        self.assertFalse(updater.auto_install)
        self.assertFalse(settings.load()["auto_update"])
        self.assertTrue(updater.auto_check)

    def test_turning_installs_off_goes_through_the_updater(self) -> None:
        page, updater = self.general()
        with mock.patch.object(updater, "set_auto_install", wraps=updater.set_auto_install) as set_install:
            page.auto_install_switch.click()
        set_install.assert_called_once_with(False)

    def test_the_switches_follow_the_updater(self) -> None:
        page, updater = self.general()
        updater.set_auto_check(False)  # from anywhere but this pane
        self.assertFalse(page.auto_check_switch.isChecked())
        self.assertFalse(page.auto_install_switch.isEnabled())

    def test_an_old_opt_out_shows_both_off(self) -> None:
        import json

        from app import settings

        settings.config_dir().mkdir(parents=True, exist_ok=True)
        settings.settings_path().write_text(json.dumps({"auto_update": False}))
        from app.controller import AppController

        self.controller = AppController()
        page, _updater = self.general()
        self.assertFalse(page.auto_check_switch.isChecked(), "no background checks either")
        self.assertFalse(page.auto_install_switch.isChecked())


class CalibrationFlowTests(LiveWindowTests):
    """Calibration UX: starting from the pad, pairing double-clicks, buttons."""

    def to_double_phase(self):
        from app.core import REQUIRED_SINGLE_CLICKS

        page = self.window.calibrate
        page._advance()
        for _ in range(REQUIRED_SINGLE_CLICKS):
            page._on_pad_press(900.0, 960.0)
        self.assertEqual(page.phase, "double")
        return page

    def test_the_first_pad_press_starts_the_single_clicks(self) -> None:
        page = self.window.calibrate
        self.assertEqual(page.phase, "intro")
        page._on_pad_press(None, None)
        self.assertEqual(page.phase, "single")
        self.assertEqual(page.calibrator.single_clicks, 1, "that press counts")
        self.assertEqual(page.count_label.text(), "1 of 12")

    def test_a_stale_gap_on_the_starting_press_is_not_taken_for_bounce(self) -> None:
        page = self.window.calibrate
        page._on_pad_press(8.0, 30.0)  # the pad timed it against a click on the intro
        self.assertEqual(page.calibrator.single_clicks, 1)
        self.assertEqual(page.calibrator.single_gaps_ms, [])

    def test_a_pair_needs_a_first_press_and_a_second(self) -> None:
        page = self.to_double_phase()
        page._on_pad_press(1500.0, 1600.0)  # opens a pair
        self.assertEqual(page.calibrator.double_clicks, 0)
        self.assertEqual(page.pad._flash_tone, "neutral", "the first press isn't flashed as counted")
        page._on_pad_press(150.0, 220.0)  # closes it
        self.assertEqual(page.calibrator.double_clicks, 1)
        self.assertEqual(page.pad._flash_tone, "good")

    def test_a_triple_click_counts_once(self) -> None:
        page = self.to_double_phase()
        page._on_pad_press(1500.0, 1600.0)
        page._on_pad_press(150.0, 220.0)
        page._on_pad_press(150.0, 220.0)  # the third press opens a new pair
        self.assertEqual(page.calibrator.double_clicks, 1)

    def test_bounce_inside_a_pair_is_evidence_and_keeps_the_pair_open(self) -> None:
        page = self.to_double_phase()
        page._on_pad_press(1500.0, 1600.0)
        page._on_pad_press(9.0, 60.0)  # chatter after the first release
        self.assertIn("Bounce detected", page.step_row.detail.text())
        page._on_pad_press(150.0, 220.0)
        self.assertEqual(page.calibrator.double_clicks, 1)
        self.assertEqual(page.calibrator.single_gaps_ms, [9.0])

    def test_slow_double_clicks_count_when_the_system_accepts_them(self) -> None:
        hints = mock.Mock()
        hints.mouseDoubleClickInterval.return_value = 900
        with mock.patch("app.ui.window.QGuiApplication.styleHints", return_value=hints):
            page = self.to_double_phase()
            for _ in range(5):
                page._on_pad_press(1500.0, 1600.0)
                page._on_pad_press(700.0, 790.0)
        self.assertEqual(page.phase, "done")
        self.assertIsNotNone(page.suggestion)

    def test_too_slow_a_pair_says_so(self) -> None:
        hints = mock.Mock()
        hints.mouseDoubleClickInterval.return_value = 500
        with mock.patch("app.ui.window.QGuiApplication.styleHints", return_value=hints):
            page = self.to_double_phase()
            page._on_pad_press(1500.0, 1600.0)
            page._on_pad_press(700.0, 790.0)
        self.assertEqual(page.calibrator.double_clicks, 0)
        self.assertIn("Too slow", page.step_row.detail.text())
        self.assertEqual(page.pad._flash_tone, "neutral", "not flashed as counted")

    def test_a_pair_is_one_button(self) -> None:
        from app.core import Button

        page = self.to_double_phase()
        page._on_pad_press(1500.0, 1600.0, Button.LEFT)
        page._on_pad_press(150.0, 220.0, Button.RIGHT)  # a different button opens its own pair
        self.assertEqual(page.calibrator.double_clicks, 0)
        page._on_pad_press(150.0, 220.0, Button.RIGHT)
        self.assertEqual(page.calibrator.double_clicks, 1)

    def test_the_pad_times_each_button_against_its_own_release(self) -> None:
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest

        from app.core import Button

        pad = self.window.test_page.pad
        self.window.show()
        self.window._show_page(self.page_index("test"))
        self.application.processEvents()
        seen = []
        pad.pressed_with_gap.connect(lambda gap, interval, button: seen.append((gap, button)))
        middle = QPoint(pad.width() // 2, pad.height() // 2)
        QTest.mouseClick(pad, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, middle)
        QTest.mouseClick(pad, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, middle)
        QTest.mouseClick(pad, Qt.MouseButton.MiddleButton, Qt.KeyboardModifier.NoModifier, middle)
        QTest.mouseClick(pad, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, middle)
        self.assertEqual([button for _gap, button in seen], [Button.LEFT, Button.RIGHT, Button.MIDDLE, Button.RIGHT])
        self.assertEqual([gap is None for gap, _button in seen], [True, True, True, False],
                         "a button's first press has no gap, whatever other buttons did")
        self.assertEqual(self.window.test_page.clicks, 4)
        self.assertTrue(self.window.test_page.last_value.text().endswith("(right)"))

    def test_test_pane_shows_bounces_on_every_button(self) -> None:
        from app.core import Button, ClickEvent

        page = self.window.test_page
        self.window.show()
        self.window._show_page(self.page_index("test"))
        for button in Button:
            page.note_global_event(ClickEvent(button, True, False, 9.0, None))  # blocked
        self.assertEqual(len(page.timeline._gaps), 3)


class AppKitCallbackTests(unittest.TestCase):
    """Work AppKit asks for runs from Qt's event loop, not inside AppKit."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_system_notifications_are_handled_from_the_event_loop(self) -> None:
        import sys

        from app.ui import dock

        class NSObject:  # stands in for PyObjC's; nothing registers with AppKit
            @classmethod
            def alloc(cls):
                return cls()

            def init(self):
                return self

        calls = []
        with mock.patch.dict(sys.modules, {"AppKit": mock.MagicMock(), "Foundation": mock.MagicMock(NSObject=NSObject)}), \
                mock.patch.object(dock, "_available", return_value=True), \
                mock.patch.object(dock, "_SYSTEM_OBSERVER", []):
            dock.observe_system(
                on_wake=lambda: calls.append("wake"),
                on_session_active=lambda: calls.append("active"),
                on_session_inactive=lambda: calls.append("inactive"),
                on_permission_change=lambda: calls.append("permission"),
            )
            observer = dock._SYSTEM_OBSERVER[0]
        observer.woke_(None)
        observer.sessionInactive_(None)
        observer.sessionActive_(None)
        observer.permissionChanged_(None)
        self.assertEqual(calls, [], "nothing runs inside AppKit's dispatch")
        self.application.processEvents()
        self.assertEqual(calls, ["wake", "inactive", "active", "permission"])

    def test_an_exception_in_one_reaches_the_log_not_appkit(self) -> None:
        import sys

        from app.ui import dock

        def broken() -> None:
            raise RuntimeError("not a HookError")

        with mock.patch.object(sys, "excepthook") as excepthook:
            dock._deferred(broken)()  # what AppKit calls: returns cleanly
            self.application.processEvents()
        excepthook.assert_called_once()
        self.assertIs(excepthook.call_args[0][0], RuntimeError)


class MenuBarItemTests(unittest.TestCase):
    """macOS uses a native status item; Qt's own crashes on macOS 27."""

    def test_macos_uses_the_native_menu_bar_item(self) -> None:
        from app.ui import tray as tray_module

        with mock.patch.object(tray_module, "IS_MAC", True), mock.patch(
            "app.ui.menu_bar_mac.MacMenuBarItem"
        ) as native:
            tray_module.create(mock.Mock(), on_open=None, on_calibrate=None, on_quit=None)
            native.assert_called_once()

    def test_other_platforms_use_the_qt_tray_icon(self) -> None:
        from app.ui import tray as tray_module

        with mock.patch.object(tray_module, "IS_MAC", False), mock.patch.object(
            tray_module, "Tray"
        ) as qt_tray:
            tray_module.create(mock.Mock(), on_open=None, on_calibrate=None, on_quit=None)
            qt_tray.assert_called_once()


class MenuToggleTests(unittest.TestCase):
    """Both menus show and toggle the user's choice, not the running state."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def controller(self, active=False, wanted=False):
        controller = mock.Mock(active=active, wanted=wanted, threshold_ms=60)
        controller.status_text.return_value = "Waiting for Accessibility permission"
        controller.tooltip_text.return_value = "DoubleClick Fixer: waiting for Accessibility permission"
        return controller

    def test_tray_toggle_cancels_a_wait_for_permission(self) -> None:
        from app.ui.tray import Tray

        controller = self.controller(active=False, wanted=True)
        toggle = mock.Mock()
        tray = Tray(controller, on_open=mock.Mock(), on_calibrate=mock.Mock(), on_quit=mock.Mock(), on_toggle=toggle)
        self.addCleanup(tray.deleteLater)
        self.assertTrue(tray.toggle_action.isChecked(), "waiting reads as on")
        tray.toggle_action.trigger()
        toggle.assert_called_once_with(False)
        self.assertEqual(tray.toolTip(), "DoubleClick Fixer: waiting for Accessibility permission")

    def test_tray_toggle_turns_on_when_off(self) -> None:
        from app.ui.tray import Tray

        controller = self.controller(active=False, wanted=False)
        toggle = mock.Mock()
        tray = Tray(controller, on_open=mock.Mock(), on_calibrate=mock.Mock(), on_quit=mock.Mock(), on_toggle=toggle)
        self.addCleanup(tray.deleteLater)
        tray.toggle_action.trigger()
        toggle.assert_called_once_with(True)

    def mac_item(self, controller, toggle):
        """The macOS item over stand-in AppKit objects (a real one would
        appear in the developer's menu bar)."""
        import sys

        from app.ui import menu_bar_mac

        class Target:
            @classmethod
            def alloc(cls):
                return cls()

            def initWithActions_(self, actions):  # noqa: N802
                self.actions = actions
                return self

        appkit = mock.MagicMock(NSControlStateValueOn=1, NSControlStateValueOff=0)
        patches = [
            mock.patch.dict(sys.modules, {"AppKit": appkit}),
            mock.patch.object(menu_bar_mac, "_handler_class", return_value=Target),
            mock.patch.object(menu_bar_mac, "_status_image", return_value=None),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        item = menu_bar_mac.MacMenuBarItem(
            controller, on_open=mock.Mock(), on_calibrate=mock.Mock(), on_quit=mock.Mock(), on_toggle=toggle
        )
        return item, appkit

    def test_mac_status_item_is_saved_by_name_and_shown(self) -> None:
        from app.ui import menu_bar_mac

        item, _appkit = self.mac_item(self.controller(), mock.Mock())
        item._item.setAutosaveName_.assert_called_once_with(menu_bar_mac.AUTOSAVE_NAME)
        item.show()
        item._item.setVisible_.assert_called_with(True)

    def test_mac_menu_toggle_follows_the_users_choice(self) -> None:
        controller = self.controller(active=False, wanted=True)
        toggle = mock.Mock()
        item, _appkit = self.mac_item(controller, toggle)
        item._toggle_item.setState_.assert_called_with(1)
        item._target.actions["toggle"]()
        toggle.assert_called_once_with(False)
        item._item.button().setToolTip_.assert_called_with("DoubleClick Fixer: waiting for Accessibility permission")


class ControllerFixTests(unittest.TestCase):
    def setUp(self) -> None:
        from app import settings

        directory = Path(tempfile.mkdtemp())
        for target, value in (("config_dir", mock.Mock(return_value=directory)), ("LEGACY_PATH", directory / "x")):
            patcher = mock.patch.object(settings, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        from app.controller import AppController

        self.controller = AppController()

    def bounce(self) -> None:
        from app.core import Button, ClickEvent

        self.controller._on_global_event(ClickEvent(Button.LEFT, True, False, 5.0, None))

    def test_settings_writes_keep_unflushed_bounces(self) -> None:
        for _ in range(40):
            self.bounce()
        self.controller.set_threshold(70)            # an unrelated write
        self.assertEqual(self.controller.filtered_total, 40)
        self.controller.flush_stats()
        from app import settings

        self.assertEqual(settings.load()["filtered_total"], 40)

    def test_dead_hook_is_released_and_reported(self) -> None:
        dead = mock.Mock(running=False)
        self.controller._filter = dead
        self.controller._store(fix_enabled=True)
        states = []
        self.controller.filter_state_changed.connect(lambda active, _error: states.append(active))
        self.controller.set_active(False)
        dead.stop.assert_called_once()
        self.assertIsNone(self.controller._filter)
        self.assertFalse(self.controller.settings["fix_enabled"])
        self.assertEqual(states, [False], "the menu bar hears about it")


class WindowFixTests(WindowTests):
    def test_closing_on_calibrate_resumes_filtering(self) -> None:
        from PySide6.QtCore import QEvent

        with mock.patch.object(self.controller, "resume") as resume:
            self.window._show_page(self.page_index("calibrate"))
            self.window.closeEvent(QEvent(QEvent.Type.Close))
            resume.assert_called()

    def test_failed_login_change_restores_the_previous_state(self) -> None:
        page = self.window.general
        page.login_switch.setChecked(False, animate=False)
        with mock.patch.object(self.controller, "set_start_at_login", return_value="denied"), mock.patch(
            "app.ui.window.QMessageBox.warning"
        ):
            page._on_login(True)
        self.assertFalse(page.login_switch.isChecked())
        page.login_switch.setChecked(True, animate=False)
        with mock.patch.object(self.controller, "set_start_at_login", return_value="denied"), mock.patch(
            "app.ui.window.QMessageBox.warning"
        ):
            page._on_login(False)
        self.assertTrue(page.login_switch.isChecked(), "a failed turn-off leaves it on")

    def test_a_login_item_switched_off_in_the_system_says_where(self) -> None:
        from app import startup
        from app.ui.theme import IS_MAC

        page = self.window.general
        with mock.patch.object(startup, "is_supported", return_value=True), \
                mock.patch.object(startup, "status", return_value=startup.BLOCKED):
            self.window._show_page(self.page_index("general"))
        self.assertFalse(page.login_switch.isChecked(), "it won't open at login")
        self.assertIn("Turned off in", page.login_row.detail.text())
        self.assertEqual(not page.login_items_button.isHidden(), IS_MAC, "macOS can only fix it there")
        self.assertEqual(page.login_switch.isEnabled(), not IS_MAC, "so the switch here can't")
        with mock.patch.object(startup, "open_login_items_settings") as open_settings:
            page.login_items_button.click()
        open_settings.assert_called_once_with()
        with mock.patch.object(startup, "is_supported", return_value=True), \
                mock.patch.object(startup, "status", return_value=startup.ON):
            self.window._show_page(self.page_index("general"))
        self.assertTrue(page.login_switch.isChecked())
        self.assertTrue(page.login_items_button.isHidden())
        self.assertTrue(page.login_switch.isEnabled())

    def test_general_open_settings_opens_the_pane_even_when_allowed(self) -> None:
        from app import permissions

        page = self.window.general
        if page.permission_row is None:
            self.skipTest("macOS only")
        with mock.patch.object(permissions, "has_accessibility", return_value=True), \
                mock.patch.object(permissions, "request_accessibility") as request, \
                mock.patch.object(permissions.subprocess, "Popen") as popen:
            page.permission_button.click()
        request.assert_not_called()
        popen.assert_called_once_with(["open", permissions.ACCESSIBILITY_PANE])
        self.assertEqual(page.permission_row.title.text(), permissions.pane_name())

    def test_background_launch_does_not_raise_the_permission_prompt(self) -> None:
        with mock.patch("app.permissions.needs_accessibility", return_value=True), mock.patch(
            "app.permissions.open_accessibility_settings"
        ) as ask:
            self.window._permission_granted = False
            self.window.request_filter(True, prompt=False)
            ask.assert_not_called()
            self.assertTrue(self.window._enable_when_granted, "still starts once allowed")


class SmallFixTests(LiveWindowTests):
    """Window menu, the tray hint, first-launch size and the status item."""

    def application_stand_in(self, system: str = "Darwin"):
        """Enough of main.Application to call its methods unbound."""
        stand_in = mock.Mock()
        stand_in.window = mock.Mock()
        stand_in.controller = self.controller
        stand_in.tray = mock.Mock()
        stand_in.updater.supported = True
        # Only main's view of the platform; the rest of the app stays real.
        patch = mock.patch("app.main.platform", mock.Mock(system=mock.Mock(return_value=system)))
        patch.start()
        self.addCleanup(patch.stop)
        return stand_in

    def test_window_menu_has_minimize_and_zoom(self) -> None:
        from PySide6.QtGui import QKeySequence
        from PySide6.QtWidgets import QMenu

        from app.main import Application

        stand_in = self.application_stand_in()
        bar = Application._build_menu_bar(stand_in)
        self.addCleanup(bar.deleteLater)
        window_menu = [menu for menu in bar.findChildren(QMenu) if menu.title() == "Window"][0]
        actions = {action.text(): action for action in window_menu.actions() if action.text()}
        self.assertEqual(list(actions), ["Minimize", "Zoom", "Close"])
        self.assertEqual(actions["Minimize"].shortcut(), QKeySequence("Ctrl+M"))
        actions["Minimize"].trigger()
        stand_in.window.showMinimized.assert_called_once()
        actions["Zoom"].trigger()
        stand_in._zoom.assert_called_once()

    def test_zoom_toggles_between_maximized_and_normal(self) -> None:
        from app.main import Application

        stand_in = self.application_stand_in()
        stand_in.window.isMaximized.return_value = False
        Application._zoom(stand_in)
        stand_in.window.showMaximized.assert_called_once()
        stand_in.window.isMaximized.return_value = True
        Application._zoom(stand_in)
        stand_in.window.showNormal.assert_called_once()

    def test_the_tray_hint_shows_once_ever(self) -> None:
        from app.controller import AppController
        from app.main import Application

        stand_in = self.application_stand_in("Windows")
        Application._note_hidden(stand_in)
        Application._note_hidden(stand_in)
        self.assertEqual(stand_in.tray.showMessage.call_count, 1)
        stand_in.controller = AppController()  # the next sign-in reads the saved flag
        Application._note_hidden(stand_in)
        self.assertEqual(stand_in.tray.showMessage.call_count, 1)

    def test_quitting_leaves_the_menu_bar_item_alone_on_macos(self) -> None:
        from app.main import Application

        stand_in = self.application_stand_in("Darwin")
        stand_in.window.isVisible.return_value = False
        Application.quit(stand_in)
        stand_in.tray.hide.assert_not_called()

    def test_quitting_removes_the_tray_icon_on_windows(self) -> None:
        from app.main import Application

        stand_in = self.application_stand_in("Windows")
        stand_in.window.isVisible.return_value = False
        Application.quit(stand_in)
        stand_in.tray.hide.assert_called_once()

    def test_first_launch_fits_the_screen(self) -> None:
        from app.ui.window import FRAME_ALLOWANCE, MainWindow

        window = MainWindow(self.controller)
        self.addCleanup(window.deleteLater)
        self.addCleanup(self.stop_timers, window)
        available = window.screen().availableGeometry()
        self.assertLessEqual(window.width(), available.width() - FRAME_ALLOWANCE[0])
        self.assertLessEqual(window.height(), available.height() - FRAME_ALLOWANCE[1])
        self.assertLessEqual(window.minimumHeight(), window.height())

    def test_a_small_screen_lowers_the_minimum_too(self) -> None:
        from PySide6.QtCore import QRect

        from app.ui.window import MainWindow

        screen = mock.Mock()
        screen.availableGeometry.return_value = QRect(0, 0, 960, 492)  # Windows at 200%
        with mock.patch.object(MainWindow, "screen", return_value=screen):
            window = MainWindow(self.controller)
        self.addCleanup(window.deleteLater)
        self.addCleanup(self.stop_timers, window)
        self.assertLessEqual(window.height(), 492 - 48)
        self.assertLessEqual(window.minimumHeight(), window.height(), "the user can still fit it")

    def test_install_from_the_menu_opens_general_first(self) -> None:
        from app.main import Application

        stand_in = self.application_stand_in()
        order = []
        stand_in.show_window.side_effect = lambda: order.append("show")
        stand_in.window.show_page.side_effect = lambda page: order.append(page)
        stand_in.updater.install.side_effect = lambda: order.append("install")
        Application.install_update(stand_in)
        self.assertEqual(order, ["show", "general", "install"])

    def test_tray_update_item_installs_through_the_app(self) -> None:
        from app.ui.tray import Tray

        updater = mock.Mock(supported=True, AVAILABLE="available", READY="ready", state="available")
        updater.release.version = "1.0.0"
        install = mock.Mock()
        controller = mock.Mock(active=False, wanted=False, threshold_ms=60)
        controller.status_text.return_value = "Off"
        controller.tooltip_text.return_value = "DoubleClick Fixer: off"
        tray = Tray(controller, on_open=mock.Mock(), on_calibrate=mock.Mock(), on_quit=mock.Mock(),
                    updater=updater, on_check_updates=mock.Mock(), on_install_update=install)
        self.addCleanup(tray.deleteLater)
        self.assertEqual(tray.update_action.text(), "Update to 1.0.0")
        tray.update_action.trigger()
        install.assert_called_once_with()
        updater.install.assert_not_called()


class QuitCommandTests(unittest.TestCase):
    def test_quit_never_starts_a_copy(self) -> None:
        from time import monotonic

        from app import main as main_module

        # A name of its own: the real one would reach (and quit) a copy of
        # the app the developer has running.
        with mock.patch.object(main_module, "SERVER_NAME", f"dcf-test-quit-{os.getpid()}"):
            started = monotonic()
            self.assertEqual(main_module.main(["DoubleClickFixer", "--quit"]), 0)
            self.assertLess(monotonic() - started, 3.0)


class StateFixTests(unittest.TestCase):
    """Fixes from the 0.2.8 audit: saved choices and calibration pauses."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        patches = [
            mock.patch("app.settings.config_dir", return_value=Path(self.folder.name)),
            mock.patch("app.settings.LEGACY_PATH", Path(self.folder.name) / "legacy.json"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.folder.cleanup)
        from app.controller import AppController

        self.controller = AppController()

    def test_turning_off_while_waiting_for_permission_is_saved(self) -> None:
        self.controller._store(fix_enabled=True)
        self.controller.set_active(False)  # never started: only waiting
        self.assertFalse(self.controller.settings["fix_enabled"])

    def test_turning_on_during_calibration_waits_for_it_to_end(self) -> None:
        self.controller.enable_after_calibration()
        self.assertTrue(self.controller.suspended)
        self.assertTrue(self.controller.settings["fix_enabled"])
        self.assertEqual(self.controller.status_text(), "Paused for calibration")

    def test_a_failed_settings_write_still_takes_effect(self) -> None:
        with mock.patch("app.settings.save", side_effect=OSError("disk full")):
            self.controller.set_threshold(35)
        self.assertEqual(self.controller.threshold_ms, 35)

    def test_infinite_threshold_in_settings_falls_back(self) -> None:
        from app.core import DEFAULT_THRESHOLD_MS, clamp_threshold

        self.assertEqual(clamp_threshold(float("inf")), DEFAULT_THRESHOLD_MS)


class InstanceLockTests(unittest.TestCase):
    """A copy still starting up is found through its lock, not left running."""

    def setUp(self) -> None:
        from app import main as main_module

        self.main = main_module
        patch = mock.patch.object(main_module, "SERVER_NAME", f"dcf-test-lock-{os.getpid()}-{id(self)}")
        patch.start()
        self.addCleanup(patch.stop)

    def test_quit_waits_for_a_starting_copy_and_for_it_to_exit(self) -> None:
        held = self.main._instance_lock()
        self.assertTrue(held.tryLock(0))  # a copy that has started
        attempts = []

        def hand_over(request):
            attempts.append(request)
            if len(attempts) < 3:
                return False  # still starting: not listening yet
            held.unlock()  # it answers, and exits
            return True

        with mock.patch.object(self.main, "_hand_over_to_running_instance", side_effect=hand_over), \
                mock.patch.object(self.main, "sleep"):
            self.main._quit_running_copy()
        self.assertEqual(attempts, [b"quit"] * 3)

    def test_quit_keeps_asking_a_copy_that_is_still_unpacking(self) -> None:
        # The Windows exe unpacks itself for seconds before taking the lock or
        # listening; --quit must wait for it rather than decide nothing runs.
        others = [True, True, True, False]  # exits after the ask lands
        answers = [False, False, True]       # starts listening on the third try

        with mock.patch.object(self.main, "_other_copies_running", side_effect=lambda: others.pop(0) if others else False), \
                mock.patch.object(self.main, "_hand_over_to_running_instance", side_effect=lambda r: answers.pop(0) if answers else False) as hand, \
                mock.patch.object(self.main, "sleep"):
            self.main._quit_running_copy()
        self.assertEqual(hand.call_count, 3)
        self.assertEqual(others, [])

    def test_quit_returns_at_once_when_nothing_runs(self) -> None:
        with mock.patch.object(self.main, "_hand_over_to_running_instance", return_value=False) as hand:
            self.main._quit_running_copy()
        # One try, for a copy too old to take the lock; no waiting.
        hand.assert_called_once_with(b"quit")

    def test_second_launch_hands_over_instead_of_starting(self) -> None:
        held = self.main._instance_lock()
        self.assertTrue(held.tryLock(0))
        self.addCleanup(held.unlock)
        with mock.patch.object(self.main, "_hand_over_to_running_instance", side_effect=[False, True]) as hand, \
                mock.patch.object(self.main, "Application") as application, \
                mock.patch.object(self.main, "sleep"):
            self.assertEqual(self.main.main(["DoubleClickFixer"]), 0)
        application.assert_not_called()
        self.assertEqual(hand.call_count, 2)


if __name__ == "__main__":
    unittest.main()
