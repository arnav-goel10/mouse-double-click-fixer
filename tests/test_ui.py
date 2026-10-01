"""Offscreen tests for the window, including a full calibration run."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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

    def test_calibration_pauses_the_filter(self) -> None:
        with mock.patch.object(self.controller, "suspend") as suspend:
            self.window._show_page(self.page_index("calibrate"))
            suspend.assert_called_once()

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

        page.button_switches[Button.RIGHT]._flip()
        self.assertIn(Button.RIGHT, self.controller.buttons)

        # The last button cannot be turned off; something must stay protected.
        page.button_switches[Button.RIGHT]._flip()
        page.button_switches[Button.LEFT]._flip()
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
        ):
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
        ), mock.patch.object(self.controller, "set_active", return_value=True) as started:
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

    def test_background_launch_does_not_raise_the_permission_prompt(self) -> None:
        with mock.patch("app.permissions.needs_accessibility", return_value=True), mock.patch(
            "app.permissions.open_accessibility_settings"
        ) as ask:
            self.window._permission_granted = False
            self.window.request_filter(True, prompt=False)
            ask.assert_not_called()
            self.assertTrue(self.window._enable_when_granted, "still starts once allowed")


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
