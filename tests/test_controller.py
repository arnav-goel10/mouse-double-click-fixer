"""The controller's state: the user's choice, failures, pauses and the tap check."""

try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

import json
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
    #: Set: stop() gives up with the hook thread still alive.
    stuck = False
    instances = []

    def __init__(self, config, on_event=None, on_error=None, permission_ok=None,
                 on_permission_lost=None, on_device=None, on_wheel=None) -> None:
        self.config = config
        self.on_event = on_event
        self.on_device = on_device
        self.on_wheel = on_wheel
        self.updates = []
        self.started = False
        self.stopped = False
        self.on_error = on_error
        self.permission_ok = permission_ok
        self.on_permission_lost = on_permission_lost
        self.tap_resets = 0
        self.hook_rearms = 0
        self.device_lookup = "raw input ok"
        FakeFilter.instances.append(self)

    @property
    def running(self) -> bool:
        return self.started and not self.stopped

    def start(self) -> None:
        if FakeFilter.fail_with is not None:
            raise HookError(FakeFilter.fail_with)
        self.started = True

    def stop(self) -> None:
        self.stopped = not FakeFilter.stuck

    def update(self, config) -> None:
        self.updates.append(config)

    def seen_devices(self) -> list:
        return list(getattr(self, "devices", []))


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
        FakeFilter.stuck = False
        FakeFilter.instances = []
        patcher = mock.patch("app.controller.GlobalClickFilter", FakeFilter)
        patcher.start()
        self.addCleanup(patcher.stop)
        from app.controller import AppController

        self.controller = AppController()
        self.addCleanup(self.controller.shutdown)  # before the patches are undone
        self.states = []
        self.controller.filter_state_changed.connect(lambda active, error: self.states.append((active, error)))

    def test_a_failed_start_keeps_the_saved_choice(self) -> None:
        self.controller._store(fix_enabled=True)  # on at the last login
        FakeFilter.fail_with = "macOS refused the event tap."
        self.assertFalse(self.controller.set_active(True))
        self.assertTrue(self.controller.settings["fix_enabled"], "a refused tap is not the user's off")
        self.assertEqual(self.states, [(False, "macOS refused the event tap.")])
        self.assertEqual(self.controller.status_text(), "Couldn’t start the filter")
        self.assertIn("refused", self.controller.failure_detail)

    def test_a_failed_turn_on_from_the_menu_is_explained(self) -> None:
        # The window is closed, so no dialog: the status line has to say it.
        FakeFilter.fail_with = "busy"
        self.controller.set_active(True)
        self.assertFalse(self.controller.settings["fix_enabled"], "never got as far as on")
        self.assertFalse(self.controller.wanted, "so choosing the item again tries again")
        self.assertEqual(self.controller.status_text(), "Couldn’t start the filter")
        self.controller.set_active(False)
        self.assertEqual(self.controller.status_text(), "Off")

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
        self.assertEqual(self.controller.tooltip_text(), "Mouse Double-Click Fixer: off")
        self.controller.enable_after_calibration()
        self.assertEqual(self.controller.tooltip_text(), "Mouse Double-Click Fixer: paused for calibration")
        self.controller.resume()
        self.assertEqual(self.controller.tooltip_text(), f"Mouse Double-Click Fixer: on, {self.controller.threshold_ms} ms")

    def test_nothing_starts_a_hook_after_quitting(self) -> None:
        # Quitting mid-calibration: the window's hide event resumes the
        # pause as the app goes down, and must not start a new hook.
        self.controller.set_active(True)
        self.controller.suspend()
        self.controller.shutdown()
        self.controller.resume()
        self.assertFalse(self.controller.active)
        self.assertFalse(self.controller.set_active(True))
        self.assertEqual(len(FakeFilter.instances), 1)
        self.assertTrue(self.controller.settings["fix_enabled"], "still on at the next launch")

    def test_nothing_starts_a_hook_in_a_session_in_the_background(self) -> None:
        self.controller.set_active(True)
        first = FakeFilter.instances[0]
        self.controller.set_session_active(False)
        self.assertTrue(first.stopped)
        self.assertFalse(self.controller.set_active(True))
        self.controller.suspend()
        self.controller.resume()
        self.assertFalse(self.controller.active)
        self.assertEqual(len(FakeFilter.instances), 1, "no second hook while away")
        self.assertTrue(self.controller.settings["fix_enabled"], "the user's choice is kept")
        self.controller.set_session_active(True)
        self.assertTrue(self.controller.set_active(True))

    def test_the_filter_checks_access_and_reports_losing_it(self) -> None:
        from app import permissions

        lost = []
        self.controller.permission_lost.connect(lambda: lost.append(True))
        with mock.patch.object(permissions, "needs_accessibility", return_value=True):
            self.controller.set_active(True)
        hook = self.controller._filter
        self.assertIs(hook.permission_ok, permissions.event_tap_allowed, "re-armed only while access is there")
        hook.on_permission_lost()
        self.assertEqual(lost, [True])
        self.controller.set_active(False)
        with mock.patch.object(permissions, "needs_accessibility", return_value=False):
            self.controller.set_active(True)
        self.assertIsNone(self.controller._filter.permission_ok, "nothing to check without a permission")

    def test_a_saved_on_that_keeps_failing_can_be_turned_off(self) -> None:
        self.controller._store(fix_enabled=True)  # on at the last login
        FakeFilter.fail_with = "busy"
        self.controller.set_active(True)
        self.assertTrue(self.controller.wanted, "the menus show the user's on")
        self.controller.set_active(not self.controller.wanted)  # what the menus do
        self.assertFalse(self.controller.settings["fix_enabled"])
        self.assertEqual(self.controller.failure, "")
        self.assertFalse(self.controller.wanted)
        self.assertEqual(self.controller.status_text(), "Off")

    def test_a_hook_that_wont_stop_is_kept_and_nothing_starts_beside_it(self) -> None:
        self.controller.set_active(True)
        stuck = FakeFilter.instances[0]
        FakeFilter.stuck = True
        self.controller.stop_keeping_choice()  # a rebuild, say
        self.assertTrue(stuck.running, "its thread is still alive")
        self.assertFalse(self.controller.set_active(True))
        self.assertEqual(len(FakeFilter.instances), 1, "no second hook on top of a live one")
        self.assertIn("still stopping", self.controller.failure_detail)
        self.assertEqual(self.controller.diagnostic_state()["old hook still stopping"], True)
        FakeFilter.stuck = False  # it lets go on the next try
        self.assertTrue(self.controller.set_active(True))
        self.assertTrue(stuck.stopped)
        self.assertEqual(len(FakeFilter.instances), 2)
        self.assertEqual(self.controller.failure, "")

    def test_the_updater_saves_its_state_through_a_public_setter(self) -> None:
        from app import settings

        self.controller.store_update_state(auto_check=False, update_attempt_version="1.2.0", update_attempt_count=1)
        saved = settings.load()
        self.assertFalse(saved["auto_check"])
        self.assertEqual((saved["update_attempt_version"], saved["update_attempt_count"]), ("1.2.0", 1))
        with self.assertRaises(ValueError):
            self.controller.store_update_state(fix_enabled=True)
        self.assertFalse(settings.load()["fix_enabled"], "only the updater's own settings")

    def test_tap_check_defaults_to_alive(self) -> None:
        self.assertTrue(self.controller.tap_alive(), "no filter: nothing to check")
        self.controller.set_active(True)
        self.assertTrue(self.controller.tap_alive(), "a filter without tap_alive() counts as alive")
        self.controller._filter.tap_alive = lambda: False
        self.assertFalse(self.controller.tap_alive())



class ControllerFeatureTests(unittest.TestCase):
    """1.0: a window per button, side buttons, the wheel fix, app and
    device exclusions, wear counting and the diagnostics."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from app import settings

        self.directory = Path(tempfile.mkdtemp())
        for target, value in (("config_dir", mock.Mock(return_value=self.directory)),
                              ("LEGACY_PATH", self.directory / "x")):
            patcher = mock.patch.object(settings, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        FakeFilter.fail_with = None
        FakeFilter.stuck = False
        FakeFilter.instances = []
        patcher = mock.patch("app.controller.GlobalClickFilter", FakeFilter)
        patcher.start()
        self.addCleanup(patcher.stop)
        from app.controller import AppController

        self.controller = AppController()
        self.addCleanup(self.controller.shutdown)  # before the patches are undone
        self.changes = []
        self.controller.settings_changed.connect(lambda: self.changes.append(True))

    def running(self) -> FakeFilter:
        self.assertTrue(self.controller.set_active(True))
        return FakeFilter.instances[-1]

    def test_the_filter_starts_with_the_whole_configuration(self) -> None:
        from app.core import Button

        self.controller.set_threshold(Button.RIGHT, 30)
        self.controller.set_buttons([Button.LEFT, Button.RIGHT, Button.BACK])
        self.controller.set_wheel_fix(True, 70)
        self.controller.add_excluded_app("cs2.exe", "CS2")
        self.controller.set_device_ignored("usb:046D:C08B:abc", "G502", True)
        config = self.running().config
        self.assertEqual(config.thresholds[Button.LEFT], 46)
        self.assertEqual(config.thresholds[Button.RIGHT], 30)
        self.assertEqual(set(config.thresholds), set(Button), "every button has a window")
        self.assertEqual(config.buttons, frozenset({Button.LEFT, Button.RIGHT, Button.BACK}))
        self.assertTrue(config.wheel_fix)
        self.assertEqual(config.wheel_window_ms, 70)
        self.assertEqual(config.excluded_apps, frozenset({"cs2.exe"}))
        self.assertEqual(config.ignored_devices, frozenset({"usb:046D:C08B:abc"}))

    def test_every_change_reaches_a_running_filter_and_is_saved(self) -> None:
        from app import settings
        from app.core import Button

        hook = self.running()
        steps = [
            (lambda: self.controller.set_threshold(Button.MIDDLE, 25), lambda c: c.thresholds[Button.MIDDLE] == 25),
            (lambda: self.controller.set_side_buttons(True), lambda c: {Button.BACK, Button.FORWARD} <= c.buttons),
            (lambda: self.controller.set_wheel_fix(True), lambda c: c.wheel_fix),
            (lambda: self.controller.set_wheel_fix(True, 40), lambda c: c.wheel_window_ms == 40),
            (lambda: self.controller.add_excluded_app("com.valvesoftware.steam", "Steam"),
             lambda c: "com.valvesoftware.steam" in c.excluded_apps),
            (lambda: self.controller.set_device_ignored("bt:05AC:0269:x", "Magic Mouse", True),
             lambda c: "bt:05AC:0269:x" in c.ignored_devices),
            (lambda: self.controller.remove_excluded_app("com.valvesoftware.steam"), lambda c: not c.excluded_apps),
            (lambda: self.controller.set_device_ignored("bt:05AC:0269:x", "Magic Mouse", False),
             lambda c: not c.ignored_devices),
            (lambda: self.controller.set_side_buttons(False), lambda c: not ({Button.BACK, Button.FORWARD} & c.buttons)),
        ]
        for index, (change, check) in enumerate(steps):
            change()
            self.assertEqual(len(hook.updates), index + 1, f"step {index} reached the filter")
            self.assertTrue(check(hook.updates[-1]), f"step {index}")
            self.assertEqual(len(self.changes), index + 1, "the UI hears of it")
        saved = settings.load()
        self.assertEqual(saved["thresholds"]["middle"], 25)
        self.assertEqual(saved["wheel_window_ms"], 40)
        self.assertTrue(saved["wheel_fix"])

    def test_a_change_to_nothing_new_does_nothing(self) -> None:
        from app.core import Button

        hook = self.running()
        self.controller.set_threshold(Button.LEFT, 46)
        self.controller.set_buttons([Button.LEFT])
        self.controller.set_wheel_fix(False)
        self.controller.remove_excluded_app("absent.exe")
        self.controller.set_device_ignored("usb:1:2:3", "x", False)
        self.controller.add_excluded_app("a.exe", "A")
        self.controller.add_excluded_app("a.exe", "A again")
        self.assertEqual(len(hook.updates), 1, "only the first add")
        self.assertEqual(self.controller.excluded_apps, [{"key": "a.exe", "name": "A"}])

    def test_each_button_has_its_own_window(self) -> None:
        from app.core import Button

        self.controller.set_threshold(Button.BACK, 20)
        self.controller.set_threshold("right", 300)  # a plain name, as a signal delivers it; clamped
        self.assertEqual(self.controller.threshold_for(Button.BACK), 20)
        self.assertEqual(self.controller.threshold_for(Button.RIGHT), 200)
        self.assertEqual(self.controller.threshold_for(Button.LEFT), 46)
        self.assertEqual(self.controller.threshold_ms, 46, "the left button's")

    def test_one_button_always_stays_filtered(self) -> None:
        from app.core import Button

        self.controller.set_button_filtered(Button.LEFT, False)
        self.assertEqual(self.controller.buttons, [Button.LEFT])
        self.controller.set_button_filtered(Button.FORWARD, True)
        self.controller.set_button_filtered(Button.LEFT, False)
        self.assertEqual(self.controller.buttons, [Button.FORWARD])

    def test_calibration_is_per_button(self) -> None:
        from app import settings
        from app.core import Button

        self.controller.set_calibrated(Button.BACK)
        self.assertTrue(self.controller.is_calibrated(Button.BACK))
        self.assertFalse(self.controller.calibrated, "the left button isn't")
        self.controller.set_calibrated(Button.LEFT, True)
        self.assertEqual(settings.load()["calibrated_buttons"], ["left", "back"])
        self.assertTrue(settings.load()["calibrated"], "an older copy sees the left button's")
        self.controller.set_calibrated(Button.BACK, False)
        self.assertEqual(settings.load()["calibrated_buttons"], ["left"])

    def test_the_wheel_window_stays_in_range(self) -> None:
        from app import settings

        self.controller.set_wheel_fix(True, 1)
        self.assertEqual(self.controller.wheel_window_ms, settings.WHEEL_MIN_MS)
        self.controller.set_wheel_fix(False, 10_000)
        self.assertEqual(self.controller.wheel_window_ms, settings.WHEEL_MAX_MS)
        self.assertFalse(self.controller.wheel_fix)

    def test_tooltip_names_a_window_only_when_they_agree(self) -> None:
        from app.core import Button

        self.running()
        self.controller.set_buttons([Button.LEFT, Button.RIGHT])
        self.assertTrue(self.controller.tooltip_text().endswith("on, 46 ms"))
        self.controller.set_threshold(Button.RIGHT, 30)
        self.assertTrue(self.controller.tooltip_text().endswith(": on"))

    # -- wear --------------------------------------------------------------------
    def test_presses_of_filtered_buttons_count_towards_wear(self) -> None:
        from app.core import Button
        from app.core import ClickEvent

        hook = self.running()
        self.controller.set_threshold(Button.LEFT, 59)
        hook.on_event(ClickEvent(Button.LEFT, True, True, 400.0, None))
        hook.on_event(ClickEvent(Button.LEFT, False, True, None, None))
        hook.on_event(ClickEvent(Button.LEFT, True, False, 12.0, None))  # a bounce
        hook.on_event(ClickEvent(Button.LEFT, True, False, 5.0, None, cancels_held=True))  # a dropout
        hook.on_event(ClickEvent(Button.RIGHT, True, True, 300.0, None))  # not filtered: not counted
        today = self.controller.wear.daily(Button.LEFT)[-1]
        self.assertEqual((today.presses, today.bounces, today.dropouts), (3, 1, 1))
        self.assertEqual(self.controller.wear.daily(Button.RIGHT)[-1].presses, 0)
        self.assertEqual(dict(self.controller.wear.histogram(Button.LEFT).bins)[12], 1)
        self.assertEqual(self.controller.wear.histogram(Button.LEFT).window_ms, 59, "counted against its window")
        self.assertEqual(self.controller.session_filtered, 2, "the blocked count is as before")

    def test_a_button_turned_on_starts_counting(self) -> None:
        from app.core import Button
        from app.core import ClickEvent

        hook = self.running()
        self.controller.set_button_filtered(Button.BACK, True)
        hook.on_event(ClickEvent(Button.BACK, True, True, 300.0, None))
        self.assertEqual(self.controller.wear.daily(Button.BACK)[-1].presses, 1)

    def test_wheel_reports_count_towards_wear(self) -> None:
        hook = self.running()
        hook.on_wheel(1, True)
        hook.on_wheel(1, False)
        self.assertEqual(self.controller.wear.wheel_totals(), (2, 1))

    def test_quitting_ends_the_wait_for_a_write_even_when_the_write_fails(self) -> None:
        # A step in a window box leaves its write waiting (_store_later).
        # Quitting writes it, and if the write fails (a locked file) there is
        # no next try: the timer must not fire after the app has quit.
        from PySide6.QtTest import QTest

        from app import settings
        from app.controller import SAVE_DELAY_MS
        from app.core import Button

        self.controller.set_threshold(Button.LEFT, 50, deferred=True)
        timer = self.controller._save_timer
        self.assertTrue(timer.isActive())
        with mock.patch.object(settings, "write_json", side_effect=OSError("locked")) as write:
            self.controller.shutdown()
            tried = write.call_count
            self.assertGreaterEqual(tried, 1, "quitting tried to write it")
            self.assertFalse(timer.isActive(), "and then the wait is over")
            QTest.qWait(SAVE_DELAY_MS + 100)
            self.assertEqual(write.call_count, tried, "nothing wrote after quitting")

    def test_a_write_waiting_is_written_at_quit_and_leaves_no_timer(self) -> None:
        from app.core import Button

        self.controller.set_threshold(Button.LEFT, 50, deferred=True)
        self.controller.shutdown()
        self.assertEqual(json.loads((self.directory / "settings.json").read_text())["thresholds"]["left"], 50)
        self.assertFalse(self.controller._save_timer.isActive())

    def test_the_history_is_written_when_due_and_at_quit(self) -> None:
        from app.core import Button
        from app.core import ClickEvent

        hook = self.running()
        hook.on_event(ClickEvent(Button.LEFT, True, True, 400.0, None))
        self.controller.flush_stats()
        self.assertFalse((self.directory / "wear.json").exists(), "not every flush")
        with mock.patch.object(self.controller.wear, "_saved_at", 0.0):
            self.controller.flush_stats()
        self.assertTrue((self.directory / "wear.json").exists(), "after five minutes")
        hook.on_event(ClickEvent(Button.LEFT, True, True, 400.0, None))
        self.controller.shutdown()
        from app.wear import WearHistory

        saved = WearHistory()
        saved.load()
        self.assertEqual(saved.daily(Button.LEFT)[-1].presses, 2, "the last counts are written at quit")

    def test_the_history_is_read_at_launch(self) -> None:
        from app.core import Button
        from app.controller import AppController

        self.controller.wear.note_event(mock.Mock(pressed=True, button=Button.LEFT, cancels_held=False,
                                                  is_bounce=False, gap_ms=400.0), 46)
        self.controller.wear.save()
        reopened = AppController()
        self.addCleanup(reopened.shutdown)
        self.assertEqual(reopened.wear.daily(Button.LEFT)[-1].presses, 1)

    def test_presses_from_devices_passed_through_are_not_the_switchs_wear(self) -> None:
        from types import SimpleNamespace

        from app.core import Button

        hook = self.running()

        def click(device):
            return SimpleNamespace(button=Button.LEFT, pressed=True, accepted=True, is_bounce=False,
                                   cancels_held=False, gap_ms=400.0, device=device)

        hook.on_device(self.device("usb:05AC:0342:t", "Trackpad", "trackpad", filtered=False))
        self.controller.set_device_ignored("usb:046D:C08B:g", "G502", True)
        for device in ("usb:05AC:0342:t", "usb:046D:C08B:g", "usb:03F0:1:a", None):
            hook.on_event(click(device))
        self.assertEqual(self.controller.wear.daily(Button.LEFT)[-1].presses, 2, "the filtered mouse, and unknown")
        self.controller.set_device_ignored("usb:046D:C08B:g", "G502", False)
        hook.on_event(click("usb:046D:C08B:g"))
        self.assertEqual(self.controller.wear.daily(Button.LEFT)[-1].presses, 3, "filtered again")

    # -- devices -----------------------------------------------------------------
    def device(self, key, name="HP mouse", kind="mouse", filtered=True, when=100.0):
        from app.platform import DeviceInfo

        return DeviceInfo(key=key, name=name, kind=kind, filtered=filtered, last_seen=when)

    def test_devices_the_filter_sees_are_listed_and_announced(self) -> None:
        hook = self.running()
        heard = []
        self.controller.device_seen.connect(lambda: heard.append(True))
        hook.on_device(self.device("usb:03F0:1:a", when=100.0))
        hook.on_device(self.device("internal:trackpad", "Trackpad", "trackpad", filtered=False, when=200.0))
        self.assertEqual(heard, [True, True])
        devices = self.controller.seen_devices()
        self.assertEqual([device.key for device in devices], ["internal:trackpad", "usb:03F0:1:a"], "newest first")
        self.assertEqual([device.filtered for device in devices], [False, True])

    def test_ignoring_a_device_shows_at_once(self) -> None:
        hook = self.running()
        hook.on_device(self.device("usb:03F0:1:a"))
        self.controller.set_device_ignored("usb:03F0:1:a", "HP mouse", True)
        self.assertFalse(self.controller.seen_devices()[0].filtered)
        self.assertEqual(self.controller.ignored_devices, [{"key": "usb:03F0:1:a", "name": "HP mouse"}])

    def test_a_touch_device_is_never_filtered(self) -> None:
        hook = self.running()
        hook.on_device(self.device("pen:1", "Surface Pen", "pen", filtered=True))
        self.assertFalse(self.controller.seen_devices()[0].filtered)

    def test_devices_stay_listed_after_the_filter_stops(self) -> None:
        hook = self.running()
        hook.devices = [self.device("usb:03F0:1:a", when=300.0)]
        self.controller.set_active(False)
        self.assertEqual([device.last_seen for device in self.controller.seen_devices()], [300.0])

    # -- diagnostics -------------------------------------------------------------
    def test_diagnostics_say_what_1_0_adds_without_private_keys(self) -> None:
        from app.core import Button

        hook = self.running()
        self.controller.set_threshold(Button.RIGHT, 30)
        self.controller.set_button_filtered(Button.BACK, True)
        self.controller.set_wheel_fix(True, 60)
        self.controller.add_excluded_app("com.valvesoftware.steam", "Steam")
        self.controller.set_device_ignored("usb:046D:C08B:SERIAL123", "G502 HERO", True)
        hook.on_device(self.device("bt:05AC:0269:SERIAL456", "Magic Mouse"))
        state = self.controller.diagnostic_state()
        self.assertEqual(state["windows"], "left 46 ms, right 30 ms, middle 46 ms, back 46 ms, forward 46 ms")
        self.assertEqual(state["side buttons"], "back on, forward off")
        self.assertEqual(state["wheel fix"], "on, 60 ms")
        self.assertEqual(state["excluded apps"], 1)
        self.assertEqual(state["ignored devices"], "G502 HERO")
        self.assertEqual(state["seen devices"], "Magic Mouse (mouse)")
        self.assertEqual(state["passed untouched"], "none")
        self.assertEqual(state["device lookup"], "raw input ok", "read from the filter")
        hook.device_lookup = "raw input unavailable: RegisterRawInputDevices failed (error 5)"
        self.assertEqual(self.controller.diagnostic_state()["device lookup"],
                         "raw input unavailable: RegisterRawInputDevices failed (error 5)")
        hook.passed_counts = {"touch": 3, "excluded app": 1}
        hook.wheel_dropped = 2
        state = self.controller.diagnostic_state()
        self.assertEqual(state["passed untouched"], "excluded app 1, touch 3")
        self.assertEqual(state["wheel ticks dropped"], 2)
        shown = self.controller.diagnostic_settings()
        self.assertEqual(shown["excluded_apps"], 1)
        self.assertEqual(shown["ignored_devices"], ["G502 HERO"])
        text = repr(state) + repr(shown)
        for private in ("SERIAL123", "SERIAL456", "com.valvesoftware.steam"):
            self.assertNotIn(private, text)
        self.assertEqual(self.controller.settings["excluded_apps"][0]["key"], "com.valvesoftware.steam",
                         "only the report leaves them out")


if __name__ == "__main__":
    unittest.main()
