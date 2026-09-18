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
        ), mock.patch.object(self.controller, "set_active", return_value=False) as set_active:
            self.window._permission_granted = False
            self.window.request_filter(True)
            set_active.assert_not_called()
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
                mock.patch.object(self.controller, "set_active") as set_active:
            self.window._permission_granted = True
            self.window._check_permission()
            set_active.assert_called_once_with(False)

    def test_closing_hides_instead_of_quitting(self) -> None:
        from PySide6.QtCore import QEvent

        self.window.show()
        self.window.closeEvent(QEvent(QEvent.Type.Close))
        self.assertTrue(self.window.isHidden())


if __name__ == "__main__":
    unittest.main()
