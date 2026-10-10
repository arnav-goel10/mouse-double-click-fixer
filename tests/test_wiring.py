"""The controller wired to the real filter, end to end.

test_controller.py runs the controller against a FakeFilter and test_filter.py
runs the filter without a controller, so each passes while the two disagree:
a keyword renamed on one side, a callback whose arguments changed, a counter
the controller reads with getattr (which turns a rename into a quiet "-" in
the bug report). This module builds the real AppController, lets it start the
real GlobalClickFilter over test_filter's macOS fakes (FakeQuartz, the sender
cache, the foreground app), feeds the fake tap events as the system would,
and checks what the controller makes of them.

Nothing here touches the machine: no tap is installed, no event posted, the
cursor not moved and no app opened.
"""

try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

import dataclasses
import inspect
import logging
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from time import monotonic
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from run import _unhide_qt_plugins

_unhide_qt_plugins()

from PySide6.QtWidgets import QApplication

try:
    from test_filter import FakeCGEvent, FakeFront, FakeQuartz, FakeSenders, FakeTimer
except ImportError:  # run as tests.<module> from the repository root
    from tests.test_filter import FakeCGEvent, FakeFront, FakeQuartz, FakeSenders, FakeTimer

from app.core import TOUCH_KINDS, Button
from app.devices_mac import MacDevice
from app.platform import (
    PASS_APP,
    PASS_DEVICE,
    PASS_TOUCH,
    TAP_DISABLED_MESSAGE,
    DeviceInfo,
    FilterConfig,
    GlobalClickFilter,
)

# The app logs failures on purpose; keep them out of the test output.
logging.getLogger("app").addHandler(logging.NullHandler())
logging.getLogger("app").propagate = False

#: What the controller hands the filter's constructor besides the config.
CALLBACKS = {"on_event", "on_error", "permission_ok", "on_permission_lost", "on_device", "on_wheel"}

MOUSE = MacDevice("usb:046d:c08b:G502", "G502", "mouse")
TRACKPAD = MacDevice("fifo:0000:0000:Apple Internal Keyboard / Trackpad", "Apple Internal Keyboard / Trackpad", "trackpad")
STEAM = "com.valvesoftware.steam"


class WiredTests(unittest.TestCase):
    Q = FakeQuartz

    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from app import settings
        from app.controller import AppController

        directory = Path(tempfile.mkdtemp())
        for target, value in (("config_dir", mock.Mock(return_value=directory)), ("LEGACY_PATH", directory / "x")):
            patcher = mock.patch.object(settings, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        # Made on the machine's own platform: the login item and the rest of
        # its start-up are not part of what is checked here.
        self.controller = AppController()

        self.quartz = FakeQuartz()
        self.timers = FakeTimer.reset()
        self.senders = FakeSenders()
        self.front = FakeFront()
        self.permitted = True
        self.created: list = []
        self.spy = mock.Mock(side_effect=self._make_filter)
        for patch in (
            mock.patch.dict(sys.modules, {"Quartz": self.quartz}),
            mock.patch("app.platform.platform.system", return_value="Darwin"),
            mock.patch("app.devices_mac.SenderCache", lambda: self.senders),
            mock.patch("app.frontmost.current_app_key", self.front.current_app_key),
            mock.patch("app.frontmost.watch", self.front.watch),
            mock.patch("app.platform.threading.Timer", FakeTimer),
            mock.patch("app.platform._timebase_ratio", return_value=(125, 3)),
            mock.patch("app.platform._double_click_interval", return_value=0.5),
            mock.patch("app.controller.GlobalClickFilter", self.spy),
            # The controller asks macOS for the access it has; here it is ours to give or take.
            mock.patch("app.controller.permissions.needs_accessibility", return_value=True),
            mock.patch("app.controller.permissions.event_tap_allowed", side_effect=lambda: self.permitted),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.controller.shutdown)  # before the patches are undone

        self.bounces: list = []
        self.new_devices = 0
        self.hook_failures: list = []
        self.permission_lost: list = []
        self.controller.global_event.connect(self.bounces.append)
        self.controller.device_seen.connect(self._device_seen)
        self.controller.hook_failed.connect(self.hook_failures.append)
        self.controller.permission_lost.connect(self.permission_lost.append)

        self.assertTrue(self.controller.set_active(True))
        (self.filter,) = self.created
        self.base = monotonic() - 0.5
        self.clock = 0.0

    def _make_filter(self, *args, **kwargs) -> GlobalClickFilter:
        made = GlobalClickFilter(*args, **kwargs)
        self.created.append(made)
        return made

    def _device_seen(self) -> None:
        self.new_devices += 1

    # -- the system, as the tap sees it -------------------------------------------
    @property
    def tap(self):
        """The newest tap: turning the wheel fix on or off makes another."""
        return self.quartz.taps[-1]

    def event(self, kind, offset, sender=0, number=None, subtype=0, state=1, **fields) -> FakeCGEvent:
        self.clock = offset
        values = {
            self.Q.kCGMouseEventClickState: state,
            self.Q.kCGMouseEventSubtype: subtype,
            87: sender,
            self.Q.kCGEventSourceStateID: self.Q.kCGEventSourceStateHIDSystemState,
        }
        if number is not None:
            values[self.Q.kCGMouseEventButtonNumber] = number
        values.update(fields)
        stamp = round((self.base + offset) * 1e9 * 3 / 125)
        return FakeCGEvent(kind, 0, 0, timestamp=stamp, fields=values)

    def feed(self, kind, offset, **kwargs):
        event = self.event(kind, offset, **kwargs)
        return self.tap.callback(None, event.kind, event, None)

    def scroll(self, offset, vertical, tap=None):
        Q = self.Q
        event = self.event(Q.kCGEventScrollWheel, offset)
        event.fields.update({
            Q.kCGScrollWheelEventDeltaAxis1: vertical, Q.kCGScrollWheelEventPointDeltaAxis1: vertical * 10,
            Q.kCGScrollWheelEventFixedPtDeltaAxis1: vertical << 16,
            Q.kCGScrollWheelEventIsContinuous: 0,
        })
        return (tap or self.tap).callback(None, event.kind, event, None)

    def click_twice(self, start: float, **kwargs) -> list:
        """Left press, release, press, release 40 and 5 and 35 ms apart:
        whether each reached apps. For what passes untouched; a filtered
        left button holds its releases, and these tests leave that out."""
        Q = self.Q
        steps = ((Q.kCGEventLeftMouseDown, 0.000), (Q.kCGEventLeftMouseUp, 0.040),
                 (Q.kCGEventLeftMouseDown, 0.045), (Q.kCGEventLeftMouseUp, 0.080))
        return [self.feed(kind, start + offset, **kwargs) is not None for kind, offset in steps]

    def back_bounce(self, start: float, **kwargs) -> list:
        """A back-button click and its bounce, 6 ms after it let go: whether
        each event reached apps. A side button's release is never held, so
        nothing is left waiting."""
        Q = self.Q
        steps = ((Q.kCGEventOtherMouseDown, 0.000), (Q.kCGEventOtherMouseUp, 0.060),
                 (Q.kCGEventOtherMouseDown, 0.066), (Q.kCGEventOtherMouseUp, 0.080))
        return [self.feed(kind, start + offset, number=3, **kwargs) is not None for kind, offset in steps]

    def wait_for_taps(self, count: int) -> None:
        deadline = monotonic() + 2
        while len(self.quartz.taps) < count and monotonic() < deadline:
            threading.Event().wait(0.005)
        self.assertEqual(len(self.quartz.taps), count, "the tap was not replaced")

    # -- the constructor ------------------------------------------------------------
    def test_the_controller_builds_the_filter_with_the_keywords_it_takes(self) -> None:
        self.assertIs(type(self.filter), GlobalClickFilter, "the real filter, not a stand-in")
        self.assertEqual(self.spy.call_count, 1)
        args, kwargs = self.spy.call_args
        self.assertEqual(set(kwargs), CALLBACKS)
        self.assertEqual(len(args), 1)
        self.assertIsInstance(args[0], FilterConfig)
        parameters = inspect.signature(GlobalClickFilter).parameters
        self.assertLessEqual(CALLBACKS, set(parameters), "a keyword the filter no longer takes")
        inspect.signature(GlobalClickFilter).bind(*args, **kwargs)
        self.assertEqual(self.filter.config, self.controller.filter_config())

    def test_the_filter_runs_in_the_system_the_fakes_stand_for(self) -> None:
        self.assertTrue(self.filter.running)
        self.assertTrue(self.controller.active)
        self.assertEqual(len(self.quartz.taps), 1)
        self.assertTrue(self.senders.warmed, "connected devices are looked up ahead")
        self.assertEqual(len(self.front.callbacks), 1, "the app in front is watched")
        self.controller.set_active(False)
        self.assertFalse(self.filter.running)
        self.assertFalse(self.controller.active)
        self.assertEqual(self.front.stopped, 1)

    # -- update(FilterConfig) ---------------------------------------------------------
    def test_every_settings_change_reaches_the_running_filter(self) -> None:
        controller, filter = self.controller, self.filter
        changes = [
            ("a window", lambda: controller.set_threshold(Button.LEFT, 80),
             lambda config: config.thresholds[Button.LEFT] == 80),
            ("a window, saved later", lambda: controller.set_threshold(Button.RIGHT, 70, deferred=True),
             lambda config: config.thresholds[Button.RIGHT] == 70),
            ("the buttons", lambda: controller.set_buttons([Button.LEFT, Button.MIDDLE]),
             lambda config: config.buttons == frozenset({Button.LEFT, Button.MIDDLE})),
            ("the side buttons", lambda: controller.set_side_buttons(True),
             lambda config: {Button.BACK, Button.FORWARD} <= config.buttons),
            ("an excluded app", lambda: controller.add_excluded_app(STEAM, "Steam"),
             lambda config: config.excluded_apps == frozenset({STEAM})),
            ("an app no longer excluded", lambda: controller.remove_excluded_app(STEAM),
             lambda config: config.excluded_apps == frozenset()),
            ("an ignored device", lambda: controller.set_device_ignored(MOUSE.key, MOUSE.name, True),
             lambda config: config.ignored_devices == frozenset({MOUSE.key})),
            ("a device filtered again", lambda: controller.set_device_ignored(MOUSE.key, MOUSE.name, False),
             lambda config: config.ignored_devices == frozenset()),
        ]
        for name, change, expected in changes:
            with self.subTest(name):
                change()
                self.assertTrue(expected(filter.config), "the running filter still has the old settings")
                self.assertEqual(filter.config, controller.filter_config())
        self.assertEqual(len(self.created), 1, "settings change the filter that runs, not a new one")

    def test_a_settings_change_changes_what_the_filter_does(self) -> None:
        # Back is not among the filtered buttons: its bounce is every bit as real as any click.
        self.assertEqual(self.back_bounce(0.0), [True] * 4)
        self.assertEqual(self.bounces, [])
        self.assertEqual(self.controller.wear.totals(Button.BACK).presses, 0)
        self.controller.set_side_buttons(True)
        self.assertEqual(self.back_bounce(1.0), [True, True, False, False], "the bounce is dropped, with its release")
        self.assertEqual(self.controller.session_filtered, 1)
        self.assertEqual(self.controller.filtered_total, 1)
        self.assertEqual(len(self.bounces), 1, "global_event hears of the bounce")
        self.assertEqual(self.bounces[0].button, Button.BACK)
        self.assertTrue(self.bounces[0].is_bounce)

    # -- on_event --------------------------------------------------------------------
    def test_filtered_clicks_reach_the_wear_history(self) -> None:
        self.controller.set_side_buttons(True)
        self.back_bounce(0.0)
        self.back_bounce(1.0)
        totals = self.controller.wear.totals(Button.BACK)
        self.assertEqual((totals.presses, totals.bounces), (4, 2))
        self.assertEqual(self.controller.wear.totals(Button.LEFT).presses, 0, "other buttons count their own")
        self.assertEqual(self.controller.diagnostic_state()["blocked this session"], 2)

    # -- on_device and seen_devices() ---------------------------------------------------
    def test_a_device_reaches_the_devices_list_the_first_time_it_clicks(self) -> None:
        self.controller.set_side_buttons(True)
        self.senders.devices.update({1: MOUSE, 2: TRACKPAD})
        self.back_bounce(0.0, sender=1)
        self.assertEqual(self.new_devices, 1)
        self.click_twice(1.0, sender=2, subtype=3)
        self.assertEqual(self.new_devices, 2)
        self.back_bounce(2.0, sender=1)
        self.assertEqual(self.new_devices, 2, "told of a device once")
        devices = {device.key: device for device in self.controller.seen_devices()}
        self.assertEqual(set(devices), {MOUSE.key, TRACKPAD.key})
        self.assertEqual((devices[MOUSE.key].name, devices[MOUSE.key].kind), ("G502", "mouse"))
        self.assertEqual((devices[TRACKPAD.key].name, devices[TRACKPAD.key].kind),
                         ("Apple Internal Keyboard / Trackpad", "trackpad"))
        self.assertEqual((devices[MOUSE.key].filtered, devices[TRACKPAD.key].filtered), (True, False))
        self.assertIn("G502 (mouse)", self.controller.diagnostic_state()["seen devices"])
        self.assertEqual([event.device for event in self.bounces], [MOUSE.key] * 2, "events name their device")

    def test_the_filters_own_list_is_what_the_devices_list_follows(self) -> None:
        # on_device hears of a device once; the filter's list moves on. A
        # device first seen through a pointer collection and later through a
        # touch one is a touch device, and only seen_devices() knows.
        wacom = "usb:056a:0378:Wacom"
        self.senders.devices.update({4: MacDevice(wacom, "Wacom", "mouse"), 5: MacDevice(wacom, "Wacom", "pen")})
        self.click_twice(0.0, sender=4)
        self.assertEqual([device.kind for device in self.controller.seen_devices()], ["mouse"])
        self.click_twice(1.0, sender=5, subtype=1)
        (device,) = self.controller.seen_devices()
        self.assertEqual((device.kind, device.filtered), ("pen", False))
        self.assertEqual(self.new_devices, 1)
        (from_the_filter,) = self.filter.seen_devices()
        self.assertIsInstance(from_the_filter, DeviceInfo)
        self.assertEqual((from_the_filter.key, from_the_filter.name, from_the_filter.kind), (wacom, "Wacom", "pen"))
        self.assertGreater(from_the_filter.last_seen, 0)
        # What the controller reads off each one.
        self.assertLessEqual(
            {"key", "name", "kind", "filtered", "last_seen"}, {field.name for field in dataclasses.fields(DeviceInfo)}
        )
        self.assertIn(from_the_filter.kind, TOUCH_KINDS)

    def test_devices_stay_listed_after_the_filter_stops(self) -> None:
        self.senders.devices[1] = MOUSE
        self.click_twice(0.0, sender=1)
        self.controller.set_active(False)
        self.assertEqual([device.key for device in self.controller.seen_devices()], [MOUSE.key])

    def test_a_device_the_user_stops_filtering_passes(self) -> None:
        self.controller.set_side_buttons(True)
        self.senders.devices[1] = MOUSE
        self.assertEqual(self.back_bounce(0.0, sender=1), [True, True, False, False])
        self.controller.set_device_ignored(MOUSE.key, MOUSE.name, True)
        self.assertEqual(self.back_bounce(1.0, sender=1), [True] * 4, "its bounce is not dropped now")
        (device,) = self.controller.seen_devices()
        self.assertFalse(device.filtered)
        self.assertEqual(self.filter.passed_counts, {PASS_DEVICE: 2})
        self.controller.set_device_ignored(MOUSE.key, MOUSE.name, False)
        self.assertEqual(self.back_bounce(2.0, sender=1), [True, True, False, False])
        self.assertTrue(self.controller.seen_devices()[0].filtered)

    # -- passed_counts ---------------------------------------------------------------
    def test_what_passes_untouched_is_counted_in_the_diagnostics(self) -> None:
        self.assertEqual(self.controller.diagnostic_state()["passed untouched"], "none")
        self.senders.devices.update({1: MOUSE, 2: TRACKPAD})
        self.controller.set_device_ignored(MOUSE.key, MOUSE.name, True)
        self.controller.add_excluded_app(STEAM, "Steam")
        self.assertEqual(self.click_twice(0.0, sender=2, subtype=3), [True] * 4)
        self.assertEqual(self.click_twice(1.0, sender=1), [True] * 4)
        self.front.bring(STEAM)
        self.assertEqual(self.click_twice(2.0, sender=0), [True] * 4)
        self.assertEqual(self.filter.passed_counts, {PASS_TOUCH: 2, PASS_DEVICE: 2, PASS_APP: 2})
        expected = ", ".join(f"{reason} 2" for reason in sorted((PASS_APP, PASS_DEVICE, PASS_TOUCH)))
        self.assertEqual(self.controller.diagnostic_state()["passed untouched"], expected)
        self.assertEqual(self.bounces, [])
        self.assertEqual(self.controller.session_filtered, 0)

    # -- on_wheel and wheel_dropped -------------------------------------------------------
    def test_wheel_ticks_reach_the_wear_history_and_the_diagnostics(self) -> None:
        self.assertEqual(self.controller.diagnostic_state()["wheel ticks dropped"], 0)
        self.controller.set_wheel_fix(True, 100)
        self.assertTrue(self.filter.config.wheel_fix)
        self.assertEqual(self.filter.config.wheel_window_ms, 100)
        self.wait_for_taps(2)
        tap = self.quartz.taps[1]
        answers = [self.scroll(offset, vertical, tap) is not None
                   for offset, vertical in ((0.000, -1), (0.020, -1), (0.030, 1), (0.040, -1), (0.200, 1))]
        self.assertEqual(answers, [True, True, False, True, True], "the stray notch is dropped")
        self.assertEqual(self.controller.wear.wheel_totals(), (5, 1), "every tick counted, one dropped")
        self.assertEqual(self.filter.wheel_dropped, 1)
        state = self.controller.diagnostic_state()
        self.assertEqual(state["wheel ticks dropped"], 1)
        self.assertEqual(state["wheel fix"], "on, 100 ms")

    # -- the rest of what the controller reads off the filter ------------------------------
    def test_the_tap_counters_and_health_are_read_from_the_filter(self) -> None:
        state = self.controller.diagnostic_state()
        self.assertEqual((state["tap resets"], state["hook re-arms"]), (0, 0), "a counter the filter no longer has reads '-'")
        self.assertTrue(state["filter running"])
        self.assertTrue(state["tap alive"])
        self.tap.enabled = False  # as when the system takes the tap away
        self.assertFalse(self.controller.tap_alive())
        self.assertFalse(self.controller.diagnostic_state()["tap alive"])
        self.tap.callback(None, self.Q.kCGEventTapDisabledByTimeout, None, None)
        self.assertEqual(self.controller.diagnostic_state()["tap resets"], 1)

    def hear(self, heard: list) -> None:
        """Wait for the hook thread to end and its signal to arrive: the hook
        emits from its own thread, so the UI thread gets it from its event
        queue."""
        deadline = monotonic() + 2
        while (self.filter.running or not heard) and monotonic() < deadline:
            self.application.processEvents()
            threading.Event().wait(0.005)
        self.assertFalse(self.filter.running)

    def test_a_hook_that_gives_up_is_heard_by_the_controller(self) -> None:
        for _ in range(3):
            self.tap.enabled = False
            self.tap.callback(None, self.Q.kCGEventTapDisabledByTimeout, None, None)
        self.hear(self.hook_failures)
        self.assertEqual(self.hook_failures, [TAP_DISABLED_MESSAGE])
        self.assertEqual(self.permission_lost, [])

    def test_withdrawn_permission_is_heard_by_the_controller(self) -> None:
        self.permitted = False
        self.tap.enabled = False
        self.tap.callback(None, self.Q.kCGEventTapDisabledByTimeout, None, None)
        self.hear(self.permission_lost)
        self.assertEqual(self.permission_lost, [self.filter], "it names the filter that lost it")
        self.assertEqual(self.hook_failures, [])


if __name__ == "__main__":
    unittest.main()
