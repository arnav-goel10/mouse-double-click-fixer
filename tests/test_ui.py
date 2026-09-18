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

    def test_window_opens_on_the_overview(self) -> None:
        self.assertEqual(self.window.stack.currentIndex(), 0)
        self.assertIn("Filter off", self.window.switch_label.text())

    def test_theme_can_be_switched_without_errors(self) -> None:
        from app.ui.theme import DARK, LIGHT

        self.window.apply_palette(DARK)
        self.window.apply_palette(LIGHT)
        self.assertFalse(self.window.palette_tokens.dark)

    def test_test_pad_records_gaps(self) -> None:
        page = self.window.overview
        page._on_pad_press(None, None)
        page._on_pad_press(400.0, 460.0)
        page._on_pad_press(9.0, 40.0)
        self.assertEqual(page.clicks, 3)
        self.assertEqual(page.tile_last.value.text(), "9")
        self.assertEqual(page.shortest_gap, 9.0)
        page.reset()
        self.assertEqual(page.clicks, 0)

    def test_calibration_runs_end_to_end_and_applies(self) -> None:
        from app.core import REQUIRED_DOUBLE_CLICKS, REQUIRED_SINGLE_CLICKS

        page = self.window.calibrate
        self.window._show_page(1)
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

        with mock.patch("app.ui.window.QMessageBox.information"):
            page._advance()  # apply
        self.assertEqual(self.controller.threshold_ms, suggestion.threshold_ms)
        self.assertTrue(self.controller.calibrated)

    def test_calibration_without_double_clicks_reports_instead_of_applying(self) -> None:
        page = self.window.calibrate
        page._advance()
        page._on_pad_press(900.0, 960.0)
        page._finish()
        self.assertIsNone(page.suggestion)
        self.assertFalse(page.primary_button.isEnabled())

    def test_settings_changes_reach_the_controller(self) -> None:
        from app.core import Button

        page = self.window.settings_page
        page.spin.setValue(35)
        self.assertEqual(self.controller.threshold_ms, 35)

        page.button_boxes[Button.RIGHT].setChecked(True)
        self.assertIn(Button.RIGHT, self.controller.buttons)

        # The last button cannot be turned off; something must stay protected.
        page.button_boxes[Button.RIGHT].setChecked(False)
        page.button_boxes[Button.LEFT].setChecked(False)
        self.assertTrue(self.controller.buttons)

    def test_permission_banner_follows_the_live_setting(self) -> None:
        banner = self.window.overview.banner
        with mock.patch("app.permissions.needs_accessibility", return_value=True), mock.patch(
            "app.permissions.has_accessibility", return_value=False
        ):
            self.window._permission_granted = True
            self.window._check_permission()
            self.assertFalse(banner.isHidden(), "missing permission must be announced")

        with mock.patch("app.permissions.needs_accessibility", return_value=True), mock.patch(
            "app.permissions.has_accessibility", return_value=True
        ):
            self.window._check_permission()
            self.assertTrue(banner.isHidden(), "the banner must go once permission is granted")

    def test_filter_requested_before_permission_starts_once_granted(self) -> None:
        with mock.patch("app.permissions.needs_accessibility", return_value=True), mock.patch(
            "app.permissions.has_accessibility", return_value=False
        ), mock.patch.object(self.controller, "set_active", return_value=False) as refused:
            self.window._permission_granted = False
            self.window._on_switch(True)
            refused.assert_called_with(True)

        with mock.patch("app.permissions.needs_accessibility", return_value=True), mock.patch(
            "app.permissions.has_accessibility", return_value=True
        ), mock.patch.object(self.controller, "set_active", return_value=True) as started:
            self.window._check_permission()
            started.assert_called_once_with(True)

    def test_closing_hides_instead_of_quitting(self) -> None:
        from PySide6.QtCore import QEvent

        self.window.show()
        self.window.closeEvent(QEvent(QEvent.Type.Close))
        self.assertTrue(self.window.isHidden())


if __name__ == "__main__":
    unittest.main()
