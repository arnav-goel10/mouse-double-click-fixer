"""Offscreen tests for the window and the filter's life it keeps: the
permission poll, the retries, the pause while Test and Tune measures, and
the menus, the app menu and the instance lock."""

try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

import contextlib
import os
import unittest
from unittest import mock

try:
    import _ui_support as support
except ImportError:  # run as tests.<module> from the repository root
    from tests import _ui_support as support

FakeFilter = support.FakeFilter


class WindowTestCase(unittest.TestCase):
    """The window over a controller whose filter never starts."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.application = support.application()

    def setUp(self) -> None:
        support.patch_settings(self)
        from app.ui.window import MainWindow

        self.controller = support.make_controller(self, hook=False)
        self.window = MainWindow(self.controller)
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(support.stop_timers, self.window, self.window.tune)


class WindowTests(WindowTestCase):
    def test_the_window_opens_on_the_first_pane(self) -> None:
        from app.ui.theme import IS_MAC

        self.assertEqual(self.window.stack.currentIndex(), 0)
        self.assertEqual(self.window.current_key, "general" if IS_MAC else "home")
        self.assertFalse(self.window.home.switch.isChecked())

    def test_the_panes_each_platform_has(self) -> None:
        from app.ui import window

        self.assertEqual([title for _key, title in window.mac_pages()], ["General", "Exceptions", "History", "About"])
        self.assertEqual([title for _key, title in window.windows_pages()], ["Home", "Exceptions", "History", "Settings"])
        # Room to grow: at most six toolbar panes on the Mac, seven
        # navigation items on Windows.
        self.assertLessEqual(len(window.mac_pages()), 6)
        self.assertLessEqual(len(window.windows_pages()), 7)

    def test_a_mac_settings_window_is_titled_with_its_pane(self) -> None:
        from app.ui.window import MainWindow

        with support.as_platform(True):
            window = MainWindow(self.controller)
        self.addCleanup(window.deleteLater)
        self.addCleanup(support.stop_timers, window, window.tune)
        self.assertEqual(window.windowTitle(), "General")
        window.navigation.set_current(2)  # the user picks History
        self.assertEqual(window.current_key, "history")
        self.assertEqual(window.windowTitle(), "History")

    def test_a_mac_settings_window_opens_on_the_pane_last_shown(self) -> None:
        from app.ui import ui_state
        from app.ui.window import MainWindow

        with support.as_platform(True):
            first = MainWindow(self.controller)
            self.addCleanup(first.deleteLater)
            self.addCleanup(support.stop_timers, first, first.tune)
            first.show_page("about")
            self.assertEqual(ui_state.load()["settings_pane"], "about")
            again = MainWindow(self.controller)
            self.addCleanup(again.deleteLater)
            self.addCleanup(support.stop_timers, again, again.tune)
        self.assertEqual(again.current_key, "about")
        self.assertEqual(again.windowTitle(), "About")

    def test_windows_always_opens_on_home(self) -> None:
        from app.ui import ui_state
        from app.ui.window import MainWindow

        ui_state.remember(settings_pane="history")
        with support.as_platform(False):
            window = MainWindow(self.controller)
        self.addCleanup(window.deleteLater)
        self.addCleanup(support.stop_timers, window, window.tune)
        self.assertEqual(window.current_key, "home")

    def test_a_damaged_window_state_is_no_state(self) -> None:
        from app.ui import ui_state

        ui_state.path().parent.mkdir(parents=True, exist_ok=True)
        ui_state.path().write_text("{not json")
        self.assertEqual(ui_state.load(), {})
        ui_state.remember(settings_pane="general")
        self.assertEqual(ui_state.load(), {"settings_pane": "general"})

    def test_the_mac_settings_window_takes_its_panes_height_at_a_fixed_width(self) -> None:
        from app.ui.window import SETTINGS_WIDTH, MainWindow

        # A screen tall enough for every pane (the test machine's may not be).
        with support.as_platform(True), mock.patch("app.ui.window.SETTINGS_SCREEN_ROOM", -10_000):
            window = MainWindow(self.controller)
            self.addCleanup(window.deleteLater)
            self.addCleanup(support.stop_timers, window, window.tune)
            window.show()
            heights = {}
            for key in ("general", "exceptions"):
                window.show_page(key)
                self.application.processEvents()
                heights[key] = window.height()
                self.assertEqual(window.width(), SETTINGS_WIDTH)
                self.assertEqual(window.minimumSize(), window.maximumSize(), "the pane sets the size, not the user")
        self.assertNotEqual(heights["general"], heights["exceptions"], "each pane its own height")

    def test_a_pane_that_fits_fills_the_window_without_a_gap_or_a_scroll(self) -> None:
        import sys

        from app.ui.window import MainWindow

        if sys.platform != "darwin":
            # The Mac layout under another system's fonts and scroll bars
            # lays out a little differently from either platform's own.
            self.skipTest("the Mac Settings window's own metrics")

        with support.as_platform(True), mock.patch("app.ui.window.SETTINGS_SCREEN_ROOM", -10_000):
            window = MainWindow(self.controller)
            self.addCleanup(window.deleteLater)
            self.addCleanup(support.stop_timers, window, window.tune)
            window.show()
            for key in ("general", "exceptions", "history", "about"):
                window.show_page(key)
                self.application.processEvents()
                area = window.stack.currentWidget()
                self.assertEqual(area.verticalScrollBar().maximum(), 0, f"{key}: nothing to scroll")
                # No taller than the scroll area lays the pane out: at its
                # height for the width, and never under its minimum.
                page = area.widget()
                needed = max(page.heightForWidth(page.width()), page.minimumSizeHint().height())
                self.assertEqual(page.height(), needed, f"{key}: no gap below the pane")

    def test_a_pane_taller_than_the_screen_allows_scrolls(self) -> None:
        from PySide6.QtGui import QGuiApplication

        from app.ui import window as window_module
        from app.ui.window import MainWindow

        with support.as_platform(True):
            window = MainWindow(self.controller)
            self.addCleanup(window.deleteLater)
            self.addCleanup(support.stop_timers, window, window.tune)
            window.show()
            window.show_page("general")
            self.application.processEvents()
            screen = window.screen() or QGuiApplication.primaryScreen()
            room = screen.availableGeometry().height() - window_module.SETTINGS_SCREEN_ROOM
            with mock.patch.object(window, "pane_height", return_value=room + 400):
                window._fit_to_pane()
            self.assertEqual(window.height(), max(260, room), "no taller than the screen allows")

    def test_the_mac_settings_window_has_no_zoom(self) -> None:
        from PySide6.QtCore import Qt

        from app.ui.window import MainWindow

        with support.as_platform(True):
            window = MainWindow(self.controller)
        self.addCleanup(window.deleteLater)
        self.addCleanup(support.stop_timers, window, window.tune)
        self.assertFalse(window.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint)
        self.assertTrue(window.windowFlags() & Qt.WindowType.WindowMinimizeButtonHint)

    def test_light_and_dark_render_without_errors(self) -> None:
        from app.ui import widgets
        from app.ui.theme import current_look

        for dark in (True, False):
            widgets.set_look(current_look(dark))
            self.window.apply_look()
            self.window.grab()
            self.window.tune.grab()

    def test_settings_changes_reach_the_controller(self) -> None:
        from app.core import Button

        page = self.window.home
        self.controller.set_strength("custom")
        page.window_boxes[Button.LEFT].setValue(35)
        self.assertEqual(self.controller.threshold_for(Button.LEFT), 35)
        self.assertEqual(self.controller.threshold_for(Button.RIGHT), 46, "each button has its own window")

        page.button_checks[Button.RIGHT].click()
        self.assertIn(Button.RIGHT, self.controller.buttons)

        # The last button cannot be turned off; something must stay protected.
        page.button_checks[Button.RIGHT].click()
        page.button_checks[Button.LEFT].click()
        self.assertEqual(self.controller.buttons, [Button.LEFT])
        self.assertTrue(page.button_checks[Button.LEFT].isChecked())

    def test_permission_notice_follows_the_live_setting(self) -> None:
        notice = self.window.home.permission
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
        from app import permissions

        with mock.patch("app.permissions.needs_accessibility", return_value=True), mock.patch(
            "app.permissions.has_accessibility", return_value=False
        ), mock.patch.object(self.controller, "set_active", return_value=False) as set_active, \
                mock.patch("app.permissions.open_accessibility_settings") as ask:
            self.window._permission_granted = False
            self.window.request_filter(True)
            set_active.assert_not_called()
            ask.assert_called_once()
            self.assertTrue(self.window.home.switch.isChecked(), "switch shows the request")
            self.assertEqual(self.window.home.status_row.detail.text(),
                             f"Waiting for {permissions.pane_name()} permission.")

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

        # On Windows: the Mac's Settings window takes its pane's size.
        with support.as_platform(False):
            window = MainWindow(self.controller)
            self.addCleanup(window.deleteLater)
            self.addCleanup(support.stop_timers, window, window.tune)
            # The offscreen test screen is only 800 x 600 and Qt rightly
            # shrinks a restored window to fit its screen, so this checks
            # the round trip at a size well inside it.
            window.setMinimumSize(300, 200)
            window.show()
            window.resize(640, 460)
            self.application.processEvents()
            expected = window.size()
            window.save_geometry()
            reopened = MainWindow(self.controller)
            self.addCleanup(reopened.deleteLater)
            self.addCleanup(support.stop_timers, reopened, reopened.tune)
            reopened.setMinimumSize(300, 200)
            reopened._restore_geometry()
        self.assertEqual(reopened.size(), expected)

    def test_content_stays_readable_when_wide_and_fits_when_narrow(self) -> None:
        from app.ui.window import COLUMN_MAX, MainWindow

        with support.as_platform(False):
            window = MainWindow(self.controller)
            self.addCleanup(window.deleteLater)
            self.addCleanup(support.stop_timers, window, window.tune)
            window.show()
            window.resize(1600, 900)
            self.application.processEvents()
            self.assertLessEqual(window.home.column.width(), COLUMN_MAX)
            window.resize(window.minimumSize())
            self.application.processEvents()
            page = window.home
            viewport = page.parentWidget()
            self.assertLessEqual(page.column.geometry().right(), viewport.width(), "nothing is clipped")

    def test_closing_hides_instead_of_quitting(self) -> None:
        from PySide6.QtCore import QEvent

        self.window.show()
        self.window.closeEvent(QEvent(QEvent.Type.Close))
        self.assertTrue(self.window.isHidden())

    def test_the_status_sentence_is_the_controllers(self) -> None:
        with mock.patch.object(self.controller, "status_sentence", return_value="No extra clicks seen yet."):
            self.window.refresh()
        self.assertEqual(self.window.home.status_row.detail.text(), "No extra clicks seen yet.")

    def test_another_mouse_utility_is_noted_where_the_switch_is(self) -> None:
        home = self.window.home
        self.window.refresh()
        self.assertTrue(home.conflicts.isHidden())
        found = [support.Conflict("Logi Options+", ["side buttons", "wheel"])]
        with mock.patch.object(self.controller, "conflicts", return_value=found):
            self.controller.conflicts_changed.emit()
            self.assertFalse(home.conflicts.isHidden())
            row = home.conflicts.rows[0]
            self.assertEqual(row.title.text(), "Logi Options+ is running")
            self.assertIn("the side buttons and the wheel", row.detail.text())
            self.controller.conflicts_changed.emit()
            self.assertIs(home.conflicts.rows[0], row, "the same note: not built again")
        self.controller.conflicts_changed.emit()
        self.assertTrue(home.conflicts.isHidden())


class LiveWindowTests(unittest.TestCase):
    """A window over a controller whose filter really starts and stops (a
    stand-in hook), with both windows' activation under the test's control."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.application = support.application()

    def setUp(self) -> None:
        support.patch_settings(self)
        from app.ui.window import MainWindow

        self.controller = support.make_controller(self)
        self.window = MainWindow(self.controller)
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(support.stop_timers, self.window, self.window.tune)
        self.active_window = True
        self.window.isActiveWindow = lambda: self.active_window
        self.tune_active = True
        self.window.tune.isActiveWindow = lambda: self.tune_active
        self.window.setMinimumSize(300, 200)

    def set_active_window(self, active: bool) -> None:
        from PySide6.QtCore import QEvent

        self.active_window = active
        self.window.changeEvent(QEvent(QEvent.Type.ActivationChange))

    def set_tune_active(self, active: bool) -> None:
        from PySide6.QtCore import QEvent

        self.tune_active = active
        self.window.tune.changeEvent(QEvent(QEvent.Type.ActivationChange))

    def close(self) -> None:
        from PySide6.QtCore import QEvent

        self.window.closeEvent(QEvent(QEvent.Type.Close))


class TunePauseTests(LiveWindowTests):
    """Test and Tune has to see raw clicks: filtering pauses while it
    measures in front of the user, and only then."""

    def open_tune(self):
        self.controller.set_active(True)
        self.window.show_tune()
        return self.window.tune

    def test_opening_does_not_pause_and_the_first_press_does(self) -> None:
        tune = self.open_tune()
        self.assertTrue(self.controller.active, "reading the first step leaves filtering on")
        tune._on_pad_press(None, None)  # the test starts
        self.assertEqual(tune.phase, "single")
        self.assertFalse(self.controller.active)
        self.assertTrue(self.controller.suspended)

    def test_leaving_the_window_resumes_and_coming_back_pauses(self) -> None:
        tune = self.open_tune()
        tune._advance()
        self.set_tune_active(False)
        self.assertTrue(self.controller.active, "clicks in the other app are filtered")
        self.set_tune_active(True)
        self.assertFalse(self.controller.active)

    def test_minimizing_resumes(self) -> None:
        from PySide6.QtCore import QEvent

        tune = self.open_tune()
        tune._advance()
        with mock.patch.object(tune, "isMinimized", return_value=True):
            tune.changeEvent(QEvent(QEvent.Type.WindowStateChange))
            self.assertTrue(self.controller.active)

    def test_the_verdict_resumes(self) -> None:
        tune = self.open_tune()
        tune._advance()
        tune._finish()
        self.assertEqual(tune.step, "verdict")
        self.assertTrue(self.controller.active, "the verdict measures nothing")

    def test_closing_mid_measurement_resumes_and_starts_over(self) -> None:
        tune = self.open_tune()
        tune._advance()
        tune.close()
        self.assertTrue(self.controller.active)
        self.assertEqual(tune.phase, "intro")

    def test_reopening_after_a_verdict_does_not_pause(self) -> None:
        tune = self.open_tune()
        tune._advance()
        tune._finish()
        tune.close()
        self.assertEqual(tune.phase, "intro", "an unapplied result starts over")
        self.window.show_tune()
        self.assertTrue(self.controller.active)

    def test_the_menu_toggle_while_measuring_waits_for_the_end(self) -> None:
        self.window.show_tune()
        tune = self.window.tune
        tune._advance()
        self.window.request_filter(True)
        self.assertFalse(self.controller.active, "the pad keeps measuring raw clicks")
        self.assertTrue(self.controller.wanted)
        self.assertTrue(self.window.home.switch.isChecked())
        tune._finish()
        self.assertTrue(self.controller.active)

    def test_the_menu_toggle_while_paused_turns_it_off(self) -> None:
        tune = self.open_tune()
        tune._advance()
        self.assertTrue(self.controller.wanted)
        self.window.request_filter(not self.controller.wanted)
        self.assertFalse(self.controller.wanted)
        self.assertFalse(self.controller.settings["fix_enabled"])
        tune._finish()
        self.assertFalse(self.controller.active, "stays off after the test")

    def test_a_pause_the_user_asked_for_outlasts_a_test(self) -> None:
        self.controller.set_active(True)
        self.controller.pause(15)
        self.assertTrue(self.controller.paused)
        self.window.show_tune()
        self.window.tune._advance()
        self.window.tune.close()
        self.assertTrue(self.controller.paused, "the menu's pause runs its course")
        self.assertFalse(self.controller.active)

    # The user's pause and Test and Tune's hold are separate: either can end
    # first, and filtering starts again only when neither holds it. A filter
    # running under the pad would hide the bounces it measures, and Test and
    # Tune would then recommend too short a window.

    def test_a_pause_running_out_mid_measurement_waits_for_the_pad(self) -> None:
        self.controller.set_active(True)
        self.controller.pause(15)
        tune = self.window.tune
        self.window.show_tune()
        tune._advance()  # measuring, while paused
        self.assertTrue(tune.measuring)
        self.assertTrue(self.controller.suspended, "Test and Tune holds filtering as well")
        later = self.controller._clock() + 16 * 60
        self.controller._clock = lambda: later
        self.controller._check_pause_end()  # the 15 minutes run out
        self.assertFalse(self.controller.paused)
        self.assertFalse(self.controller.active, "the pad still measures the raw mouse")
        self.assertTrue(self.controller.wanted)
        tune._finish()
        self.assertTrue(self.controller.active, "filtering starts once the measuring is over")

    def test_the_pause_shortcut_pressed_twice_mid_measurement_waits_for_the_pad(self) -> None:
        from app.ui import menus

        # The shortcut where the controller has it, else the menus' item,
        # which does the same: pause, then resume.
        press = getattr(self.controller, "toggle_pause", None) or (lambda: menus.toggle_pause(self.controller))
        tune = self.open_tune()
        tune._advance()  # measuring
        self.assertFalse(self.controller.active)
        press()
        self.assertTrue(self.controller.paused)
        press()
        self.assertFalse(self.controller.paused)
        self.assertFalse(self.controller.active, "the pad still measures the raw mouse")
        self.assertTrue(self.controller.suspended)
        tune._finish()
        self.assertTrue(self.controller.active, "filtering starts once the measuring is over")

    def test_measuring_ending_mid_pause_leaves_the_pause_running(self) -> None:
        tune = self.open_tune()
        tune._advance()  # measuring
        self.controller.pause(15)
        tune._finish()
        self.assertFalse(self.controller.suspended, "Test and Tune let go")
        self.assertTrue(self.controller.paused, "the user's pause runs its course")
        self.assertFalse(self.controller.active)
        self.controller.resume()
        self.assertTrue(self.controller.active)


class KeepFilterAliveTests(LiveWindowTests):
    """Fail open, and never lose the user's on."""

    def setUp(self) -> None:
        super().setUp()
        warning = mock.patch("app.ui.window.QMessageBox.warning")
        self.dialog = warning.start()
        self.addCleanup(warning.stop)

    def fire_retry(self) -> None:
        """What the single-shot retry timer does when it runs out."""
        self.window._retry_timer.stop()
        self.window._retry_start()

    def save_on(self) -> None:
        self.controller._store(fix_enabled=True)

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

        self.save_on()
        self.window.show()
        FakeFilter.fail_with = "macOS refused the event tap."
        with self.mac_access(ax=True, tap=False):
            self.window._permission_granted = True
            self.window.restore_filter(background=False)
            self.assertTrue(self.controller.waiting_for_permission)
            self.assertEqual(self.controller.failure, "", "a wait, not a failure")
            self.assertEqual(self.controller.status_text(), f"Waiting for {permissions.pane_name()} permission")
            self.assertFalse(self.window.home.permission.isHidden(), "the permission row explains it")
            self.assertFalse(self.window._retry_timer.isActive(), "the permission poll takes it from here")
            self.window._check_permission()
            self.assertFalse(self.controller.active)
        self.dialog.assert_not_called()
        FakeFilter.fail_with = None
        with self.mac_access(ax=True, tap=True):
            self.window._check_permission()
            self.assertTrue(self.controller.active, "starts once access is back")
            self.assertFalse(self.controller.waiting_for_permission)

    def test_a_background_launch_refused_for_lack_of_access_schedules_no_retries(self) -> None:
        self.save_on()
        FakeFilter.fail_with = "macOS refused the event tap."
        with self.mac_access(ax=True, tap=False):
            self.window._permission_granted = True
            self.window.restore_filter(background=True)
            self.assertTrue(self.controller.waiting_for_permission)
            self.assertFalse(self.window._retry_timer.isActive())
            self.assertEqual(self.window._retry_waits, [])
        FakeFilter.fail_with = None

    def test_a_tap_refused_with_access_in_place_is_a_failure(self) -> None:
        self.save_on()
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

    def test_turning_off_as_access_is_lost_stays_off(self) -> None:
        import threading

        self.controller.set_active(True)
        filter_ = self.controller._filter
        with self.mac_access(ax=True, tap=False):
            self.window._permission_granted = True
            hook = threading.Thread(target=filter_.on_permission_lost)
            hook.start()
            hook.join()
            self.window.request_filter(False)  # before the queued signal arrives
            self.application.processEvents()
            self.assertFalse(self.controller.waiting_for_permission)
            self.assertFalse(self.controller.settings["fix_enabled"])
        with self.mac_access(ax=True, tap=True):
            self.window._check_permission()
            self.assertFalse(self.controller.active, "it must not turn itself on when access returns")

    def test_a_late_signal_from_a_replaced_filter_is_ignored(self) -> None:
        import threading

        self.controller.set_active(True)
        old = self.controller._filter
        with self.mac_access(ax=True, tap=True):
            hook = threading.Thread(target=old.on_permission_lost)
            hook.start()
            hook.join()
            self.controller.set_active(False)
            self.controller.set_active(True)  # a new, healthy filter
            new = self.controller._filter
            self.application.processEvents()
        self.assertIsNot(new, old)
        self.assertTrue(self.controller.active)
        self.assertFalse(new.stopped)
        self.assertFalse(self.controller.waiting_for_permission)

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
        self.save_on()
        FakeFilter.fail_with = "macOS refused the event tap."
        self.window.request_filter(True)
        self.dialog.assert_not_called()
        self.assertEqual(self.controller.status_text(), "Couldn’t start the filter")
        self.window.show()
        self.assertIn("refused", self.window.home.status_row.detail.text(), "the full reason, in the window")
        self.window.request_filter(True)
        self.dialog.assert_called_once()

    def test_a_background_start_retries_on_schedule(self) -> None:
        from app.ui.window import RETRY_AT_S

        self.save_on()
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
        self.save_on()
        FakeFilter.fail_with = "busy"
        self.window.restore_filter(background=True)
        FakeFilter.fail_with = None
        self.fire_retry()
        self.assertTrue(self.controller.active)
        self.assertFalse(self.window._retry_timer.isActive())
        self.assertEqual(self.controller.failure, "")

    def test_turning_off_ends_the_retries(self) -> None:
        self.save_on()
        FakeFilter.fail_with = "busy"
        self.window.restore_filter(background=True)
        self.window.request_filter(False)
        self.assertFalse(self.window._retry_timer.isActive())

    def test_a_saved_on_that_keeps_failing_can_be_switched_off(self) -> None:
        self.save_on()
        FakeFilter.fail_with = "busy"
        self.window.restore_filter(background=True)
        self.assertTrue(self.window.home.switch.isChecked(), "shows the user's on")
        self.window.home.switch.click()
        self.assertFalse(self.controller.settings["fix_enabled"])
        self.assertFalse(self.window._retry_timer.isActive())
        self.assertFalse(self.window.home.switch.isChecked())
        self.assertEqual(self.controller.status_sentence(), "Off.")

    def test_an_opened_window_does_not_retry_by_itself(self) -> None:
        self.save_on()
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
        self.assertIn("isn't ready", self.window.home.status_row.detail.text())
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
        self.save_on()
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

    def test_leaving_a_test_after_switching_away_starts_nothing(self) -> None:
        self.controller.set_active(True)
        self.window.show_tune()
        self.window.tune._advance()
        self.assertTrue(self.controller.suspended)
        self.window.session_resigned()
        self.set_tune_active(False)  # the pause ends as the window loses focus
        self.assertFalse(self.controller.active, "no tap in a session nobody is using")
        self.assertTrue(self.controller.settings["fix_enabled"])
        self.window.session_activated()
        self.assertTrue(self.controller.active)

    def test_wake_does_not_end_a_test_pause(self) -> None:
        self.controller.set_active(True)
        self.window.show_tune()
        self.window.tune._advance()
        self.window.system_woke()
        self.assertFalse(self.controller.active)
        self.assertTrue(self.controller.suspended)


class AccessibilityTests(LiveWindowTests):
    """What VoiceOver and Narrator are told, and where keyboard focus goes."""

    def accessible(self, widget):
        from PySide6.QtGui import QAccessible

        return QAccessible.queryAccessibleInterface(widget)

    def test_switch_is_a_named_toggle_with_its_state(self) -> None:
        from PySide6.QtGui import QAccessible

        switch = self.window.home.switch
        interface = self.accessible(switch)
        self.assertEqual(interface.role(), QAccessible.Role.CheckBox)
        self.assertEqual(interface.text(QAccessible.Text.Name), "Bounce filter")
        self.assertTrue(interface.state().checkable)
        self.assertFalse(interface.state().checked)
        self.assertIn("Toggle", interface.actionInterface().actionNames())
        switch.setChecked(True, animate=False)
        self.assertTrue(interface.state().checked)

    def test_setting_a_switch_from_code_never_reaches_its_handler(self) -> None:
        switch = self.window.home.switch
        with mock.patch.object(self.controller, "set_active") as set_active:
            switch.setChecked(True, animate=False)
            self.window.refresh()  # sets it back to off, the real state
            set_active.assert_not_called()
            self.assertFalse(switch.isChecked())
            switch.click()  # the user
            set_active.assert_called_once_with(True)

    def test_the_users_click_on_a_switch_turns_the_filter_on(self) -> None:
        self.window.home.switch.click()
        self.assertTrue(self.controller.active)
        self.assertTrue(self.window.home.switch.isChecked())

    def test_space_flips_a_focused_button_box(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        from app.core import Button

        box = self.window.home.button_checks[Button.RIGHT]
        self.window.show()
        before = box.isChecked()
        QTest.keyClick(box, Qt.Key.Key_Space)
        self.assertNotEqual(box.isChecked(), before)
        self.assertEqual(Button.RIGHT in self.controller.buttons, box.isChecked())

    def build(self, mac: bool):
        from app.ui.window import MainWindow

        with support.as_platform(mac):
            window = MainWindow(self.controller)
        self.addCleanup(window.deleteLater)
        self.addCleanup(support.stop_timers, window, window.tune)
        return window

    def test_the_windows_navigation_is_two_lists_of_its_pages(self) -> None:
        from PySide6.QtGui import QAccessible

        window = self.build(False)
        top, footer = window.navigation.lists
        for sidebar, titles in ((top, ["Home", "Exceptions", "History"]), (footer, ["Settings"])):
            interface = self.accessible(sidebar)
            self.assertEqual(interface.role(), QAccessible.Role.List)
            names = [interface.child(index).text(QAccessible.Text.Name) for index in range(interface.childCount())]
            self.assertEqual(names, titles)
            for index in range(interface.childCount()):
                item = interface.child(index)
                self.assertEqual(item.role(), QAccessible.Role.ListItem)
                self.assertFalse(item.state().checkable, "no check box to announce")
        self.assertTrue(self.accessible(top).child(0).state().selected)
        window.show_page("settings")
        self.assertFalse(top.selectedItems(), "one selection across both lists")
        self.assertTrue(self.accessible(footer).child(0).state().selected)

    def test_the_arrow_keys_cross_from_the_pages_to_settings_and_back(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        window = self.build(False)
        top, footer = window.navigation.lists
        window.show()
        self.application.processEvents()
        top.setFocus()
        QTest.keyClick(top, Qt.Key.Key_Down)
        self.assertEqual(window.current_key, "exceptions")
        QTest.keyClick(top, Qt.Key.Key_End)
        self.assertEqual(window.current_key, "settings", "End reaches the pinned item")
        QTest.keyClick(footer, Qt.Key.Key_Up)
        self.assertEqual(window.current_key, "history")
        QTest.keyClick(top, Qt.Key.Key_Home)
        self.assertEqual(window.current_key, "home")

    def test_navigation_clicks_select_and_nothing_deselects(self) -> None:
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest

        window = self.build(False)
        top, _footer = window.navigation.lists
        window.show()
        self.application.processEvents()
        history = top.visualItemRect(top.item(2)).center()
        QTest.mouseClick(top.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, history)
        self.assertEqual(window.current_key, "history")
        QTest.mouseClick(top.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, history)
        self.assertTrue(top.item(2).isSelected())
        below = QPoint(20, top.viewport().height() - 2)
        QTest.mouseClick(top.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, below)
        self.assertTrue(top.item(2).isSelected(), "a click below the entries changes nothing")
        self.assertFalse(top.shows_focus(), "a click shows no keyboard focus")

    def test_the_mac_panes_are_named_buttons_in_one_group(self) -> None:
        from PySide6.QtCore import Qt

        window = self.build(True)
        bar = window.pane_bar
        self.assertEqual([button.accessibleName() for button in bar.buttons],
                         ["General", "Exceptions", "History", "About"])
        self.assertTrue(all(button.focusPolicy() & Qt.FocusPolicy.TabFocus for button in bar.buttons))
        bar.buttons[3].click()
        self.assertEqual(window.current_key, "about")
        self.assertTrue(bar.buttons[3].isChecked())

    def test_scroll_areas_are_not_tab_stops(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QScrollArea

        from app.ui.window import pages

        areas = self.window.findChildren(QScrollArea)
        self.assertEqual(len(areas), len(pages()))
        for area in areas:
            self.assertEqual(area.focusPolicy(), Qt.FocusPolicy.NoFocus)

    def test_switch_keeps_its_focus_ring_across_window_switches(self) -> None:
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtGui import QFocusEvent

        switch = self.window.home.switch
        switch.focusInEvent(QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.TabFocusReason))
        self.assertTrue(switch._keyboard_focus)
        switch.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.ActiveWindowFocusReason))
        switch.focusInEvent(QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.ActiveWindowFocusReason))
        self.assertTrue(switch._keyboard_focus, "back from another app: the ring is still there")
        switch.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.MouseFocusReason))
        self.assertFalse(switch._keyboard_focus)
        switch.focusInEvent(QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.MouseFocusReason))
        self.assertFalse(switch._keyboard_focus, "a click earns no ring")

    def test_tab_reaches_every_control_on_a_pane_in_reading_order(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QAbstractButton, QComboBox

        for mac in (True, False):
            window = self.build(mac)
            window.show()
            self.application.processEvents()
            page = window.home
            stops = []
            widget = page.nextInFocusChain()
            for _ in range(400):
                if widget is page or not page.isAncestorOf(widget):
                    if stops:
                        break
                elif widget.isVisibleTo(page) and widget.isEnabled() and widget.focusPolicy() & Qt.FocusPolicy.TabFocus:
                    stops.append(widget)
                widget = widget.nextInFocusChain()
            controls = [w for w in page.findChildren(QAbstractButton) + page.findChildren(QComboBox)
                        if w.isVisibleTo(page) and w.isEnabled()]
            self.assertTrue(set(controls) <= set(stops), f"{'macOS' if mac else 'Windows'}: every control is a Tab stop")
            # Down the pane: never back up past a row (controls in one row
            # sit a few pixels apart).
            tops = [control.mapTo(page, control.rect().topLeft()).y() for control in stops]
            for earlier, later in zip(tops, tops[1:]):
                self.assertGreater(later, earlier - 12, f"{'macOS' if mac else 'Windows'}: Tab goes down the pane")


class UpdateSettingsTests(LiveWindowTests):
    """The update switches, through the real updater (no network: nothing
    here checks or downloads)."""

    def about(self):
        from app.ui.window import AboutPage
        from app.updater import Updater

        updater = Updater(self.controller)
        updater.kind = "mac"  # an installed copy, so the section shows
        page = AboutPage(self.controller, updater)
        self.addCleanup(page.deleteLater)
        page.refresh(True)
        return page, updater

    def test_checking_and_installing_are_separate_switches(self) -> None:
        from app import settings

        page, updater = self.about()
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
        page, updater = self.about()
        with mock.patch.object(updater, "set_auto_install", wraps=updater.set_auto_install) as set_install:
            page.auto_install_switch.click()
        set_install.assert_called_once_with(False)

    def test_the_switches_follow_the_updater(self) -> None:
        page, updater = self.about()
        updater.set_auto_check(False)  # from anywhere but this pane
        self.assertFalse(page.auto_check_switch.isChecked())
        self.assertFalse(page.auto_install_switch.isEnabled())

    def test_an_old_opt_out_shows_both_off(self) -> None:
        import json

        from app import settings

        settings.config_dir().mkdir(parents=True, exist_ok=True)
        settings.settings_path().write_text(json.dumps({"auto_update": False}))
        self.controller = support.make_controller(self)
        page, _updater = self.about()
        self.assertFalse(page.auto_check_switch.isChecked(), "no background checks either")
        self.assertFalse(page.auto_install_switch.isChecked())

    def test_the_update_button_says_what_it_does(self) -> None:
        page, updater = self.about()
        self.assertEqual(page.update_button.accessibleName(), "Check for updates now")
        with mock.patch.object(updater, "check") as check:
            page.update_button.click()
        check.assert_called_once_with(user_initiated=True)


class AppKitCallbackTests(unittest.TestCase):
    """Work AppKit asks for runs from Qt's event loop, not inside AppKit."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.application = support.application()

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


class LoginControlTests(WindowTestCase):
    """Open at login (macOS General) and Start with Windows (Windows
    Settings), which the system can switch off behind the app's back."""

    def login(self, mac: bool):
        from app.ui.window import MainWindow

        with support.as_platform(mac):
            window = MainWindow(self.controller)
        self.addCleanup(window.deleteLater)
        self.addCleanup(support.stop_timers, window, window.tune)
        login = window.home.login if mac else window.about.login
        self.assertIsNotNone(login)
        return window, login

    def test_each_platform_keeps_it_where_its_users_look(self) -> None:
        from PySide6.QtWidgets import QCheckBox

        from app.ui.widgets import Switch

        mac, login = self.login(True)
        self.assertIsInstance(login.toggle, QCheckBox, "a checkbox on the Mac")
        self.assertIsNone(mac.about.login)
        windows, login = self.login(False)
        self.assertIsInstance(login.toggle, Switch, "a toggle on Windows")
        self.assertIsNone(windows.home.login)

    def test_failed_login_change_restores_the_previous_state(self) -> None:
        for mac in (True, False):
            _window, login = self.login(mac)
            login.toggle.setChecked(False)
            with mock.patch.object(self.controller, "set_start_at_login", return_value="denied"), mock.patch(
                "app.ui.window.QMessageBox.warning"
            ):
                login._on_login(True)
            self.assertFalse(login.toggle.isChecked())
            login.toggle.setChecked(True)
            with mock.patch.object(self.controller, "set_start_at_login", return_value="denied"), mock.patch(
                "app.ui.window.QMessageBox.warning"
            ):
                login._on_login(False)
            self.assertTrue(login.toggle.isChecked(), "a failed turn-off leaves it on")

    def test_a_login_item_switched_off_in_the_system_says_where(self) -> None:
        from app import startup

        for mac in (True, False):
            window, login = self.login(mac)
            key = "general" if mac else "settings"
            with mock.patch.object(startup, "is_supported", return_value=True), \
                    mock.patch.object(startup, "status", return_value=startup.BLOCKED), \
                    support.as_platform(mac):
                window.show_page("exceptions")
                window.show_page(key)
                self.assertFalse(login.toggle.isChecked(), "it won't open at login")
                self.assertIn("Turned off in", login.row.detail.text())
                self.assertEqual(not login.items_button.isHidden(), mac, "macOS can only fix it there")
                self.assertEqual(login.toggle.isEnabled(), not mac, "so the box here can't")
            with mock.patch.object(startup, "open_login_items_settings") as open_settings:
                login.items_button.click()
            open_settings.assert_called_once_with()
            with mock.patch.object(startup, "is_supported", return_value=True), \
                    mock.patch.object(startup, "status", return_value=startup.ON), support.as_platform(mac):
                window.show_page("exceptions")
                window.show_page(key)
            self.assertTrue(login.toggle.isChecked())
            self.assertTrue(login.items_button.isHidden())
            self.assertTrue(login.toggle.isEnabled())

    def test_about_open_settings_opens_the_pane_even_when_allowed(self) -> None:
        import sys

        from app import permissions

        if sys.platform != "darwin":
            self.skipTest("macOS's own pane")
        from app.ui.window import AboutPage

        with mock.patch.object(permissions, "needs_accessibility", return_value=True):
            page = AboutPage(self.controller)
        self.addCleanup(page.deleteLater)
        with mock.patch.object(permissions, "has_accessibility", return_value=True), \
                mock.patch.object(permissions, "request_accessibility") as request, \
                mock.patch.object(permissions.subprocess, "Popen") as popen:
            page.permission_button.click()
        request.assert_not_called()
        popen.assert_called_once_with(["/usr/bin/open", permissions.ACCESSIBILITY_PANE])
        self.assertEqual(page.permission_row.title.text(), permissions.pane_name())

    def test_background_launch_does_not_raise_the_permission_prompt(self) -> None:
        with mock.patch("app.permissions.needs_accessibility", return_value=True), mock.patch(
            "app.permissions.open_accessibility_settings"
        ) as ask:
            self.window._permission_granted = False
            self.window.request_filter(True, prompt=False)
            ask.assert_not_called()
            self.assertTrue(self.window._enable_when_granted, "still starts once allowed")


class PauseOptionsTests(WindowTestCase):
    """The pause shortcut (General on the Mac, Settings on Windows) and
    pausing for full-screen games (Exceptions on both)."""

    def make_window(self, mac: bool):
        from app.ui.window import MainWindow

        with support.as_platform(mac):
            window = MainWindow(self.controller)
        self.addCleanup(window.deleteLater)
        self.addCleanup(support.stop_timers, window, window.tune)
        return window

    def pause_controls(self, mac: bool):
        window = self.make_window(mac)
        return (window.home if mac else window.about).pause

    def test_both_switches_reach_the_controller(self) -> None:
        for mac in (True, False):
            window = self.make_window(mac)
            controls = (window.home if mac else window.about).pause
            games = window.exceptions.games
            window.refresh()
            self.assertTrue(controls.shortcut_toggle.isChecked(), "on by default")
            self.assertFalse(games.toggle.isChecked(), "off by default")
            with mock.patch.object(self.controller, "set_pause_shortcut_enabled") as shortcut, \
                    mock.patch.object(self.controller, "set_auto_pause_games") as auto_pause:
                controls.shortcut_toggle.click()
                games.toggle.click()
            shortcut.assert_called_once_with(False)
            auto_pause.assert_called_once_with(True)

    def test_the_games_switch_follows_the_setting(self) -> None:
        window = self.make_window(True)
        self.controller.set_auto_pause_games(True)
        self.assertTrue(window.exceptions.games.toggle.isChecked())
        self.controller.set_auto_pause_games(False)
        self.assertFalse(window.exceptions.games.toggle.isChecked())

    def test_the_games_row_promises_only_what_is_checked(self) -> None:
        # The check reads the app in front and its windows; whether the
        # system's Game Mode is on has no public API.
        detail = self.make_window(True).exceptions.games.row.detail.text()
        self.assertNotIn("Game Mode", detail)
        self.assertIn("fills the screen", detail)

    def test_the_shortcut_row_names_the_keys(self) -> None:
        controls = self.pause_controls(True)
        self.assertIn(self.controller.pause_shortcut_text, controls.shortcut_row.detail.text())
        with mock.patch.object(type(self.controller), "pause_shortcut_text", new_callable=mock.PropertyMock,
                               return_value=""):
            hidden = self.pause_controls(True)
        self.assertTrue(hidden.shortcut_row.isHidden(), "no shortcut on this system: nothing to switch")


class MenuWordingTests(unittest.TestCase):
    """What the menus, tray and status lines say, from the controller."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.application = support.application()

    def setUp(self) -> None:
        support.patch_settings(self)
        self.controller = support.make_controller(self)

    def test_the_status_line_while_filtering_counts_bounces(self) -> None:
        from app.ui import menus

        self.controller.set_active(True)
        self.controller.settings["filtered_total"] = 1981
        self.assertEqual(menus.status_line(self.controller), "Filtering · 1,981 bounces blocked")
        self.controller.settings["filtered_total"] = 1
        self.assertEqual(menus.status_line(self.controller), "Filtering · 1 bounce blocked")
        self.assertEqual(menus.tooltip(self.controller), "Mouse Double-Click Fixer: on")

    def test_otherwise_it_is_the_status_sentence(self) -> None:
        from app.ui import menus

        self.assertEqual(menus.status_line(self.controller), "Off")
        self.assertEqual(menus.tooltip(self.controller), "Mouse Double-Click Fixer: off")
        with mock.patch.object(self.controller, "status_sentence", return_value="Paused until 3:45 PM."):
            self.assertEqual(menus.status_line(self.controller), "Paused until 3:45 PM")
            self.assertEqual(menus.tooltip(self.controller), "Mouse Double-Click Fixer: paused until 3:45 PM")

    def test_pause_pauses_and_then_resumes(self) -> None:
        from app.ui import menus

        self.assertFalse(menus.pause_item_shown(self.controller), "nothing to pause while off")
        self.controller.set_active(True)
        self.assertTrue(menus.can_pause(self.controller))
        with support.as_platform(True):
            self.assertEqual(menus.pause_item_title(self.controller), "Pause for 15 Minutes")
        with support.as_platform(False):
            self.assertEqual(menus.pause_item_title(self.controller), "Pause for 15 minutes")
        with mock.patch.object(self.controller, "pause", wraps=self.controller.pause) as pause:
            menus.toggle_pause(self.controller)
        pause.assert_called_once_with(15)
        self.assertTrue(self.controller.paused)
        self.assertIsNotNone(self.controller.paused_until)
        self.assertFalse(self.controller.active)
        self.assertTrue(self.controller.wanted, "the user's on is kept")
        self.assertEqual(menus.pause_item_title(self.controller), "Resume")
        self.assertTrue(menus.pause_item_shown(self.controller))
        menus.toggle_pause(self.controller)
        self.assertFalse(self.controller.paused)
        self.assertTrue(self.controller.active)

    def test_the_shortcut_shows_only_while_it_is_on(self) -> None:
        from app.ui import menus

        self.assertEqual(menus.shortcut_shown(self.controller), self.controller.pause_shortcut_text)
        self.controller.set_pause_shortcut_enabled(False)
        self.assertEqual(menus.shortcut_shown(self.controller), "")

    def test_a_conflict_says_what_it_may_take_over(self) -> None:
        from app.ui import menus

        note = menus.conflict_detail(support.Conflict("G HUB", ["side buttons", "middle button", "wheel"]))
        self.assertEqual(note, "It may take over the side buttons, the middle button and the wheel. "
                               "If those act up, check its settings.")


class MacMenuTests(unittest.TestCase):
    """The menu bar item's menu and the Dock menu, over stand-in AppKit
    objects (a real item would appear in the developer's menu bar)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.application = support.application()

    def setUp(self) -> None:
        support.patch_settings(self)
        self.controller = support.make_controller(self)
        # The Mac's wording, on any computer.
        platform = support.as_platform(True)
        platform.__enter__()
        self.addCleanup(platform.__exit__, None, None, None)

    def mac_item(self, toggle=None):
        import sys

        from app.ui import menu_bar_mac

        class Target:
            @classmethod
            def alloc(cls):
                return cls()

            def initWithActions_(self, actions):  # noqa: N802
                self.actions = actions
                return self

        class Item:
            def __init__(self, title, action, key):
                self.title, self.action, self.key = title, action, key
                self.enabled, self.state, self.modifiers = True, 0, 0

            def setTarget_(self, target):  # noqa: N802
                self.target = target

            def setEnabled_(self, enabled):  # noqa: N802
                self.enabled = enabled

            def setTitle_(self, title):  # noqa: N802
                self.title = title

            def setState_(self, state):  # noqa: N802
                self.state = state

            def setKeyEquivalent_(self, key):  # noqa: N802
                self.key = key

            def setKeyEquivalentModifierMask_(self, mask):  # noqa: N802
                self.modifiers = mask

        class Menu:
            def __init__(self):
                self.items = []

            def setAutoenablesItems_(self, _value):  # noqa: N802
                pass

            def addItem_(self, item):  # noqa: N802
                self.items.append(item)

        appkit = mock.MagicMock(NSControlStateValueOn=1, NSControlStateValueOff=0)
        appkit.NSMenu.alloc.return_value.init.side_effect = Menu
        appkit.NSMenuItem.alloc.return_value.initWithTitle_action_keyEquivalent_.side_effect = Item
        appkit.NSMenuItem.separatorItem.side_effect = lambda: Item("-", None, "")
        patches = [
            mock.patch.dict(sys.modules, {"AppKit": appkit}),
            mock.patch.object(menu_bar_mac, "_handler_class", return_value=Target),
            mock.patch.object(menu_bar_mac, "_status_image", return_value=None),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        toggle = toggle or mock.Mock()
        item = menu_bar_mac.MacMenuBarItem(
            self.controller, on_settings=mock.Mock(), on_tune=mock.Mock(), on_quit=mock.Mock(), on_toggle=toggle,
        )
        return item, appkit

    def titles(self, item) -> list:
        return [entry.title for entry in item.menu.items]

    def test_the_menu_holds_what_the_design_lists_in_order(self) -> None:
        item, _appkit = self.mac_item()
        self.assertEqual(self.titles(item), [
            "Off", "Filter Bounce", "Pause for 15 Minutes", "-", "Test and Tune…", "Settings…", "-",
            "Quit Mouse Double-Click Fixer",
        ])
        status, toggle, pause, _sep, tune, settings, _sep2, quit_item = item.menu.items
        self.assertFalse(status.enabled, "the status line is a line, not a command")
        self.assertIsNone(status.action)
        self.assertEqual((settings.key, quit_item.key), (",", "q"))
        self.assertFalse(pause.enabled, "nothing to pause while off")
        self.assertEqual(pause.key, "p", "the pause shortcut shows beside it")

    def test_the_menu_follows_the_filter(self) -> None:
        item, _appkit = self.mac_item()
        self.controller.set_active(True)
        self.controller.settings["filtered_total"] = 1981
        item.refresh()
        status, toggle, pause = item.menu.items[:3]
        self.assertEqual(status.title, "Filtering · 1,981 bounces blocked")
        self.assertEqual(toggle.state, 1)
        self.assertTrue(pause.enabled)
        item._target.actions["pause"]()
        self.assertTrue(self.controller.paused)
        self.assertEqual(pause.title, "Resume")
        self.assertTrue(status.title.startswith("Paused until"))
        self.assertEqual(toggle.state, 1, "paused is still the user's on")
        item._target.actions["pause"]()
        self.assertFalse(self.controller.paused)
        self.assertEqual(pause.title, "Pause for 15 Minutes")

    def test_the_toggle_follows_the_users_choice(self) -> None:
        toggle = mock.Mock()
        item, _appkit = self.mac_item(toggle)
        self.controller.set_waiting_for_permission(True)
        item.refresh()
        self.assertEqual(item.menu.items[1].state, 1, "waiting reads as on")
        item._target.actions["toggle"]()
        toggle.assert_called_once_with(False)
        from app import permissions

        self.assertEqual(item.menu.items[0].title, f"Waiting for {permissions.pane_name()} permission")
        item._item.button().setToolTip_.assert_called_with(
            f"Mouse Double-Click Fixer: waiting for {permissions.pane_name()} permission"
        )

    def test_the_shortcut_leaves_the_menu_when_it_is_turned_off(self) -> None:
        item, _appkit = self.mac_item()
        self.controller.set_pause_shortcut_enabled(False)
        self.assertEqual(item.menu.items[2].key, "")

    def test_the_status_item_is_saved_by_name_and_can_be_hidden(self) -> None:
        from app.ui import menu_bar_mac

        item, _appkit = self.mac_item()
        item._item.setAutosaveName_.assert_called_once_with(menu_bar_mac.AUTOSAVE_NAME)
        item.set_shown(False)
        item._item.setVisible_.assert_called_with(False)
        item.show()
        item._item.setVisible_.assert_called_with(True)

    def test_the_dock_menu_offers_the_same_actions(self) -> None:
        from app.ui.menu_bar_mac import DockMenu

        tune, settings, toggle = mock.Mock(), mock.Mock(), mock.Mock()
        dock = DockMenu(self.controller, on_settings=settings, on_tune=tune, on_toggle=toggle)
        self.addCleanup(dock.menu.deleteLater)
        self.assertEqual(dock.titles(), ["Off", "Filter Bounce", "Pause for 15 Minutes", "Test and Tune…", "Settings…"])
        self.assertFalse(dock.status_action.isEnabled())
        self.assertFalse(dock.pause_action.isEnabled())
        for action in dock.menu.actions():
            if not action.isSeparator():
                self.assertEqual(action.menuRole(), action.MenuRole.NoRole, "not moved into the app menu")
        dock.toggle_action.trigger()
        toggle.assert_called_once_with(True)
        dock.tune_action.trigger()
        tune.assert_called_once_with()
        dock.settings_action.trigger()
        settings.assert_called_once_with()
        self.controller.set_active(True)
        dock.pause_action.trigger()
        self.assertTrue(self.controller.paused)
        self.assertEqual(dock.pause_action.text(), "Resume")


class TrayTests(unittest.TestCase):
    """The Windows notification area icon: its flyout and its menu."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.application = support.application()

    def setUp(self) -> None:
        support.patch_settings(self)
        self.controller = support.make_controller(self)
        self.platform = support.as_platform(False)
        self.platform.__enter__()
        self.addCleanup(self.platform.__exit__, None, None, None)

    def tray(self, **actions):
        from app.ui.tray import Tray

        for name in ("on_open", "on_tune", "on_quit", "on_toggle"):
            actions.setdefault(name, mock.Mock())
        tray = Tray(self.controller, **actions)
        self.addCleanup(tray.flyout.deleteLater)
        self.addCleanup(tray.deleteLater)
        # A popup left open stays on Qt's popup stack, under the next test's.
        self.addCleanup(tray.flyout.close)
        return tray

    def test_the_menu_is_short_with_open_first_and_bold(self) -> None:
        tray = self.tray()
        menu = tray.contextMenu()
        actions = [action for action in menu.actions()]
        self.assertEqual([action.text() for action in actions if not action.isSeparator()],
                         ["Open", "Pause for 15 minutes\tCtrl+Alt+Shift+P" if os.name == "nt" else
                          f"Pause for 15 minutes\t{self.controller.pause_shortcut_text}", "Start with Windows", "Exit"])
        self.assertIs(menu.defaultAction(), tray.open_action, "Open is the default, drawn bold")
        self.assertFalse(tray.pause_action.isVisible(), "removed, not greyed out, while off")
        self.assertTrue(all(action.isEnabled() for action in actions if action.isVisible()), "nothing greyed out")
        self.assertNotIn("About", " ".join(action.text() for action in actions))

    def test_pause_then_resume_from_the_menu(self) -> None:
        tray = self.tray()
        self.controller.set_active(True)
        self.assertTrue(tray.pause_action.isVisible())
        tray.pause_action.trigger()
        self.assertTrue(self.controller.paused)
        self.assertTrue(tray.pause_action.text().startswith("Resume"))
        self.assertTrue(tray.toolTip().startswith("Mouse Double-Click Fixer: paused until"))
        tray.pause_action.trigger()
        self.assertFalse(self.controller.paused)
        self.assertEqual(tray.toolTip(), "Mouse Double-Click Fixer: on")

    def test_start_with_windows_is_a_check_mark(self) -> None:
        tray = self.tray()
        self.assertTrue(tray.login_action.isCheckable())
        with mock.patch.object(self.controller, "set_start_at_login", return_value="") as login:
            tray.login_action.trigger()
        login.assert_called_once_with(True)

    def test_left_click_opens_the_flyout_and_a_second_closes_it(self) -> None:
        from PySide6.QtWidgets import QSystemTrayIcon

        tray = self.tray()
        tray._on_activated(QSystemTrayIcon.ActivationReason.Trigger)
        self.assertTrue(tray.flyout.isVisible())
        tray._on_activated(QSystemTrayIcon.ActivationReason.Trigger)
        self.assertFalse(tray.flyout.isVisible())

    def test_the_flyout_shows_the_status_the_switch_and_open(self) -> None:
        from PySide6.QtCore import Qt

        opened, toggled = mock.Mock(), mock.Mock(side_effect=self.controller.set_active)
        tray = self.tray(on_open=opened, on_toggle=toggled)
        flyout = tray.flyout
        self.assertTrue(flyout.windowFlags() & Qt.WindowType.Popup, "closes on a click elsewhere")
        tray.show_flyout()
        self.assertEqual(flyout.status.text(), "Off.")
        self.assertFalse(flyout.switch.isChecked())
        self.assertEqual(flyout.switch.accessibleName(), "Bounce filter")
        flyout.switch.click()
        toggled.assert_called_once_with(True)
        self.assertTrue(flyout.switch.isChecked())
        self.assertEqual(flyout.status.text(), self.controller.status_sentence())
        self.assertEqual(flyout.open_link.accessibleName(), "Open Mouse Double-Click Fixer")
        self.assertEqual(flyout.open_link.text(), "Open")
        self.assertEqual(flyout.open_link.styleSheet(), "", "coloured by its palette")
        flyout.open_link.click()
        opened.assert_called_once_with()
        self.assertFalse(flyout.isVisible(), "Open closes it")

    def test_the_keyboard_reaches_the_switch_then_open(self) -> None:
        from PySide6.QtCore import QRect, Qt
        from PySide6.QtTest import QTest

        opened = mock.Mock()
        tray = self.tray(on_open=opened)
        # Whatever earlier tests left queued (a window going away closes
        # popups) runs first, not while this one is open.
        self.application.processEvents()
        tray.flyout.show_near(QRect())
        self.assertIs(tray.flyout.focusWidget(), tray.flyout.switch, "the switch first, ready for Space")
        QTest.keyClick(tray.flyout.switch, Qt.Key.Key_Tab)
        self.assertIs(tray.flyout.focusWidget(), tray.flyout.open_link)
        QTest.keyClick(tray.flyout.open_link, Qt.Key.Key_Space)
        opened.assert_called_once_with()

    def test_escape_closes_the_flyout(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        tray = self.tray()
        tray.show_flyout()
        QTest.keyClick(tray.flyout, Qt.Key.Key_Escape)
        self.assertFalse(tray.flyout.isVisible())

    def test_the_flyout_stays_on_the_screen_beside_the_taskbar(self) -> None:
        from PySide6.QtCore import QRect

        tray = self.tray()
        screen = self.application.primaryScreen().availableGeometry()
        tray.flyout.show_near(QRect(screen.right() - 30, screen.bottom() + 4, 24, 24))
        self.assertTrue(screen.contains(tray.flyout.frameGeometry()))
        tray.flyout.close()
        tray.flyout.show_near(QRect())  # in the overflow: no place to point at
        self.assertTrue(screen.contains(tray.flyout.frameGeometry()))

    def test_a_double_click_opens_the_window(self) -> None:
        from PySide6.QtWidgets import QSystemTrayIcon

        opened = mock.Mock()
        tray = self.tray(on_open=opened)
        tray._on_activated(QSystemTrayIcon.ActivationReason.DoubleClick)
        opened.assert_called_once_with()


class FactoryTests(unittest.TestCase):
    def test_macos_uses_the_native_menu_bar_item(self) -> None:
        from app.ui import tray as tray_module

        with mock.patch.object(tray_module, "IS_MAC", True), mock.patch(
            "app.ui.menu_bar_mac.MacMenuBarItem"
        ) as native:
            tray_module.create(mock.Mock(), on_open=None, on_settings=None, on_tune=None, on_quit=None)
            native.assert_called_once()

    def test_other_platforms_use_the_qt_tray_icon(self) -> None:
        from app.ui import tray as tray_module

        with mock.patch.object(tray_module, "IS_MAC", False), mock.patch.object(
            tray_module, "Tray"
        ) as qt_tray:
            tray_module.create(mock.Mock(), on_open=None, on_settings=None, on_tune=None, on_quit=None)
            qt_tray.assert_called_once()


class ApplicationTests(LiveWindowTests):
    """main.Application's menus, Dock icon and hints, called unbound on a
    stand-in for the rest of it."""

    def application_stand_in(self, system: str = "Darwin"):
        stand_in = mock.Mock()
        stand_in.window = mock.Mock()
        stand_in.window.tune.isVisible.return_value = False
        stand_in.first_run = None
        stand_in.controller = self.controller
        stand_in.tray = mock.Mock()
        stand_in.tray.is_shown.return_value = True
        stand_in.updater.supported = True
        # Only main's view of the platform; the rest of the app stays real.
        patch = mock.patch("app.main.platform", mock.Mock(system=mock.Mock(return_value=system)))
        patch.start()
        self.addCleanup(patch.stop)
        from app.main import Application

        stand_in._windows = lambda: Application._windows(stand_in)
        stand_in._menu_bar_icon_shown = lambda: Application._menu_bar_icon_shown(stand_in)
        stand_in._window_closed = lambda: Application._window_closed(stand_in)
        return stand_in

    def test_the_window_menu_has_minimize_and_a_dimmed_zoom(self) -> None:
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
        self.assertFalse(actions["Zoom"].isEnabled(), "Settings sizes itself to its pane")
        stand_in._front_window.return_value = stand_in.window
        actions["Minimize"].trigger()
        stand_in.window.showMinimized.assert_called_once()
        actions["Close"].trigger()
        stand_in.window.close.assert_called_once()
        app_menu = [menu for menu in bar.findChildren(QMenu) if menu.title() != "Window"][0]
        settings = next(action for action in app_menu.actions() if action.text() == "Settings…")
        self.assertEqual(settings.menuRole(), settings.MenuRole.PreferencesRole)
        self.assertEqual(settings.shortcut(), QKeySequence(QKeySequence.StandardKey.Preferences))

    def test_the_tray_hint_shows_once_ever(self) -> None:
        from app.main import Application

        stand_in = self.application_stand_in("Windows")
        Application._note_hidden(stand_in)
        Application._note_hidden(stand_in)
        self.assertEqual(stand_in.tray.showMessage.call_count, 1)
        stand_in.controller = support.make_controller(self)  # the next sign-in reads the saved flag
        Application._note_hidden(stand_in)
        self.assertEqual(stand_in.tray.showMessage.call_count, 1)

    def test_with_the_menu_bar_icon_hidden_the_dock_icon_stays(self) -> None:
        from app.main import Application

        stand_in = self.application_stand_in()
        stand_in.window.isVisible.return_value = False
        with mock.patch("app.main.dock.set_visible") as dock:
            Application._window_closed(stand_in)
            dock.assert_called_once_with(False)
            dock.reset_mock()
            stand_in.tray.is_shown.return_value = False
            Application._show_in_menu_bar(stand_in, False)
            stand_in.tray.set_shown.assert_called_with(False)
            dock.assert_called_once_with(True)
            dock.reset_mock()
            Application._window_closed(stand_in)
            dock.assert_not_called()

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

    def test_check_for_updates_opens_the_pane_with_the_updates_first(self) -> None:
        from app.main import Application
        from app.ui.window import updates_page_key

        stand_in = self.application_stand_in()
        order = []
        stand_in.show_window.side_effect = lambda: order.append("show")
        stand_in.window.show_page.side_effect = lambda page: order.append(page)
        stand_in.updater.check.side_effect = lambda **_kwargs: order.append("check")
        Application.check_for_updates(stand_in)
        self.assertEqual(order, ["show", updates_page_key(), "check"])

    def test_first_launch_fits_the_screen(self) -> None:
        from app.ui.window import FRAME_ALLOWANCE, MainWindow

        with support.as_platform(False):
            window = MainWindow(self.controller)
        self.addCleanup(window.deleteLater)
        self.addCleanup(support.stop_timers, window, window.tune)
        available = window.screen().availableGeometry()
        self.assertLessEqual(window.width(), available.width() - FRAME_ALLOWANCE[0])
        self.assertLessEqual(window.height(), available.height() - FRAME_ALLOWANCE[1])
        self.assertLessEqual(window.minimumHeight(), window.height())

    def test_a_small_screen_lowers_the_minimum_too(self) -> None:
        from PySide6.QtCore import QRect

        from app.ui.window import MainWindow

        screen = mock.Mock()
        screen.availableGeometry.return_value = QRect(0, 0, 960, 492)  # Windows at 200%
        with mock.patch.object(MainWindow, "screen", return_value=screen), support.as_platform(False):
            window = MainWindow(self.controller)
        self.addCleanup(window.deleteLater)
        self.addCleanup(support.stop_timers, window, window.tune)
        self.assertLessEqual(window.height(), 492 - 48)
        self.assertLessEqual(window.minimumHeight(), window.height(), "the user can still fit it")


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
        cls.application = support.application()

    def setUp(self) -> None:
        support.patch_settings(self)
        self.controller = support.make_controller(self, hook=False)

    def test_turning_off_while_waiting_for_permission_is_saved(self) -> None:
        self.controller._store(fix_enabled=True)
        self.controller.set_active(False)  # never started: only waiting
        self.assertFalse(self.controller.settings["fix_enabled"])

    def test_turning_on_during_a_test_waits_for_it_to_end(self) -> None:
        self.controller.enable_after_calibration()
        self.assertTrue(self.controller.suspended)
        self.assertTrue(self.controller.settings["fix_enabled"])
        self.assertEqual(self.controller.status_sentence(), "Paused while you test your mouse.")

    def test_a_failed_settings_write_still_takes_effect(self) -> None:
        from app.core import Button

        with mock.patch("app.settings.save", side_effect=OSError("disk full")):
            self.controller.set_threshold(Button.RIGHT, 35)
        self.assertEqual(self.controller.threshold_for(Button.RIGHT), 35)


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
