"""The controller's state: the user's choice, failures, pauses and the tap check."""

import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from run import _unhide_qt_plugins

_unhide_qt_plugins()

from PySide6.QtWidgets import QApplication

from app.platform import HookError

# The app logs failures on purpose; keep them out of the test output.
logging.getLogger("app").addHandler(logging.NullHandler())
logging.getLogger("app").propagate = False


class FakeFilter:
    """Stands in for GlobalClickFilter: no hook, just the lifecycle."""

    fail_with = None
    instances = []

    def __init__(self, threshold_ms, buttons, on_event=None, on_error=None) -> None:
        self.started = False
        self.stopped = False
        self.on_error = on_error
        self.tap_resets = 0
        self.hook_rearms = 0
        FakeFilter.instances.append(self)

    @property
    def running(self) -> bool:
        return self.started and not self.stopped

    def start(self) -> None:
        if FakeFilter.fail_with is not None:
            raise HookError(FakeFilter.fail_with)
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def update(self, **_changes) -> None:
        pass


class ControllerStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from app import settings

        directory = Path(tempfile.mkdtemp())
        for target, value in (("config_dir", mock.Mock(return_value=directory)), ("LEGACY_PATH", directory / "x")):
            patcher = mock.patch.object(settings, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        FakeFilter.fail_with = None
        FakeFilter.instances = []
        patcher = mock.patch("app.controller.GlobalClickFilter", FakeFilter)
        patcher.start()
        self.addCleanup(patcher.stop)
        from app.controller import AppController

        self.controller = AppController()
        self.states = []
        self.controller.filter_state_changed.connect(lambda active, error: self.states.append((active, error)))

    def test_a_failed_start_keeps_the_saved_choice(self) -> None:
        self.controller._store(fix_enabled=True)  # on at the last login
        FakeFilter.fail_with = "macOS refused the event tap."
        self.assertFalse(self.controller.set_active(True))
        self.assertTrue(self.controller.settings["fix_enabled"], "a refused tap is not the user's off")
        self.assertEqual(self.states, [(False, "macOS refused the event tap.")])
        self.assertEqual(self.controller.status_text(), "Couldn't start the filter")
        self.assertIn("refused", self.controller.failure_detail)

    def test_a_good_start_clears_the_failure(self) -> None:
        self.controller._store(fix_enabled=True)
        FakeFilter.fail_with = "busy"
        self.controller.set_active(True)
        FakeFilter.fail_with = None
        self.assertTrue(self.controller.set_active(True))
        self.assertEqual(self.controller.failure, "")
        self.assertTrue(self.controller.status_text().startswith("On"))

    def test_only_the_users_off_clears_the_choice(self) -> None:
        self.controller.set_active(True)
        self.controller.stop_after_failure("The hook stopped.")
        self.assertTrue(self.controller.settings["fix_enabled"])
        self.assertEqual(self.controller.status_text(), "The filter stopped")
        self.controller.stop_for_permission()
        self.controller.stop_keeping_choice()
        self.assertTrue(self.controller.settings["fix_enabled"])
        self.controller.set_active(False)
        self.assertFalse(self.controller.settings["fix_enabled"])
        self.assertEqual(self.controller.status_text(), "Off")

    def test_turning_off_says_so_whenever_the_choice_changes(self) -> None:
        # Never started (waiting for permission, say), but saved as on: the
        # menus must hear that it is off now.
        self.controller._store(fix_enabled=True)
        self.controller.set_active(False)
        self.assertEqual(self.states, [(False, "")])
        self.controller.set_active(False)  # nothing left to change
        self.assertEqual(self.states, [(False, "")])

    def test_wanted_follows_the_users_choice(self) -> None:
        controller = self.controller
        self.assertFalse(controller.wanted)
        controller.set_active(True)
        self.assertTrue(controller.wanted)
        controller.suspend()
        self.assertFalse(controller.active)
        self.assertTrue(controller.wanted, "paused for calibration is still on")
        controller.set_active(False)
        self.assertFalse(controller.wanted)
        self.assertFalse(controller.suspended, "off ends the pause too")
        controller.set_waiting_for_permission(True)
        self.assertTrue(controller.wanted)

    def test_turning_on_during_calibration_reads_as_on_and_paused(self) -> None:
        self.controller.enable_after_calibration()
        self.assertTrue(self.controller.wanted)
        self.assertEqual(self.controller.status_text(), "Paused for calibration")
        self.controller.resume()
        self.assertTrue(self.controller.active)

    def test_tooltip_uses_the_status_line(self) -> None:
        self.assertEqual(self.controller.tooltip_text(), "DoubleClick Fixer: off")
        self.controller.enable_after_calibration()
        self.assertEqual(self.controller.tooltip_text(), "DoubleClick Fixer: paused for calibration")
        self.controller.resume()
        self.assertEqual(self.controller.tooltip_text(), f"DoubleClick Fixer: on, {self.controller.threshold_ms} ms")

    def test_tap_check_defaults_to_alive(self) -> None:
        self.assertTrue(self.controller.tap_alive(), "no filter: nothing to check")
        self.controller.set_active(True)
        self.assertTrue(self.controller.tap_alive(), "a filter without tap_alive() counts as alive")
        self.controller._filter.tap_alive = lambda: False
        self.assertFalse(self.controller.tap_alive())


if __name__ == "__main__":
    unittest.main()
