"""The 1.0 input features in the filter itself (design sections 1 and 2):
the wheel rule, the side buttons' drop rule, a window per button, clicks that
pass untouched (touch, ignored devices, excluded apps) and the order they
reach apps in, and the devices the filter reports. No hook is installed: the
platform hooks' own parts are tested in test_filter (macOS) and
test_windows_hook (Windows)."""

try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

import time
import unittest
from dataclasses import FrozenInstanceError, replace

from app.core import DEFAULT_THRESHOLD_MS, MAX_THRESHOLD_MS, MIN_THRESHOLD_MS, SIDE_BUTTONS, Button, ClickEvent, WheelFilter
from app.platform import (
    PASS_APP,
    PASS_DEVICE,
    PASS_TOUCH,
    WHEEL_DEFAULT_MS,
    DeviceInfo,
    FilterConfig,
    GlobalClickFilter,
)

try:
    from test_filter import Pipeline
except ImportError:  # run as tests.<module> from the repository root
    from tests.test_filter import Pipeline


class WheelFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.wheel = WheelFilter(50)

    def test_a_reversing_tick_inside_the_window_is_dropped(self) -> None:
        self.assertTrue(self.wheel.tick(1, -1, 10.000))
        self.assertFalse(self.wheel.tick(1, +1, 10.030))

    def test_the_window_is_inclusive_and_ends(self) -> None:
        self.assertTrue(self.wheel.tick(1, -1, 10.000))
        self.assertFalse(self.wheel.tick(1, +1, 10.050), "exactly the window: still the stray notch")
        self.assertTrue(self.wheel.tick(1, +1, 10.051))

    def test_ticks_the_same_way_are_never_dropped(self) -> None:
        self.assertEqual([self.wheel.tick(1, -3, 10.0 + i * 0.001) for i in range(20)], [True] * 20)

    def test_the_scroll_carries_on_past_a_dropped_notch(self) -> None:
        # Scrolling down every 20 ms; one notch comes up the wrong way. The
        # window counts from the last tick delivered, so the next notch down
        # is no reversal and goes through.
        verdicts = [self.wheel.tick(1, -1, 10.000), self.wheel.tick(1, -1, 10.020)]
        verdicts.append(self.wheel.tick(1, +1, 10.030))
        verdicts += [self.wheel.tick(1, -1, 10.040), self.wheel.tick(1, -1, 10.060)]
        self.assertEqual(verdicts, [True, True, False, True, True])

    def test_a_deliberate_reversal_comes_through_after_the_window(self) -> None:
        self.assertTrue(self.wheel.tick(1, -1, 10.000))
        rolled_back = [self.wheel.tick(1, +1, 10.000 + ms / 1000) for ms in (20, 40, 60, 80)]
        self.assertEqual(rolled_back, [False, False, True, True], "at most one window of it is lost")

    def test_each_axis_is_judged_on_its_own(self) -> None:
        self.assertTrue(self.wheel.tick(1, -1, 10.000))
        self.assertTrue(self.wheel.tick(2, +1, 10.010), "a first horizontal tick is no reversal")
        self.assertFalse(self.wheel.tick(2, -1, 10.020))
        self.assertTrue(self.wheel.tick(1, -1, 10.030))

    def test_no_direction_is_delivered_and_changes_nothing(self) -> None:
        self.assertTrue(self.wheel.tick(1, -1, 10.000))
        self.assertTrue(self.wheel.tick(1, 0, 10.010))
        self.assertFalse(self.wheel.tick(1, +1, 10.020))

    def test_reset_and_a_new_window(self) -> None:
        self.wheel.tick(1, -1, 10.000)
        self.wheel.reset()
        self.assertTrue(self.wheel.tick(1, +1, 10.010))
        self.wheel.window_ms = 5
        self.assertTrue(self.wheel.tick(1, -1, 10.020))
        self.wheel.window_ms = "junk"
        self.assertEqual(self.wheel.window_ms, 0)


class FilterConfigTests(unittest.TestCase):
    def test_every_button_gets_a_window_clamped(self) -> None:
        config = FilterConfig(thresholds={Button.LEFT: 30, "right": 1, Button.BACK: 999}, buttons=["left", Button.BACK])
        self.assertEqual(
            dict(config.thresholds),
            {
                Button.LEFT: 30,
                Button.RIGHT: MIN_THRESHOLD_MS,
                Button.MIDDLE: DEFAULT_THRESHOLD_MS,
                Button.BACK: MAX_THRESHOLD_MS,
                Button.FORWARD: DEFAULT_THRESHOLD_MS,
            },
        )
        self.assertEqual(config.buttons, frozenset({Button.LEFT, Button.BACK}))
        self.assertEqual((config.wheel_fix, config.wheel_window_ms), (False, WHEEL_DEFAULT_MS))
        self.assertEqual((config.excluded_apps, config.ignored_devices), (frozenset(), frozenset()))

    def test_it_cannot_be_changed_in_place(self) -> None:
        config = FilterConfig.uniform(40, [Button.LEFT])
        with self.assertRaises(FrozenInstanceError):
            config.wheel_fix = True  # type: ignore[misc]
        with self.assertRaises(TypeError):
            config.thresholds[Button.LEFT] = 10  # type: ignore[index]

    def test_defaults(self) -> None:
        self.assertEqual(DEFAULT_THRESHOLD_MS, 46)
        self.assertEqual(WHEEL_DEFAULT_MS, 50)
        self.assertEqual(SIDE_BUTTONS, frozenset({Button.BACK, Button.FORWARD}))
        self.assertIsNone(ClickEvent(Button.LEFT, True, True, None, None).device)

    def test_the_filter_takes_only_a_config(self) -> None:
        with self.assertRaises(TypeError):
            GlobalClickFilter(60, [Button.LEFT])  # type: ignore[arg-type]
        click_filter = GlobalClickFilter(FilterConfig.uniform(40, []))
        with self.assertRaises(TypeError):
            click_filter.update(threshold_ms=10)  # type: ignore[call-arg]


class Rig:
    """A Pipeline (test_filter) driven through _button_event, as the hooks
    drive it, with every event the filter reports kept."""

    def __init__(self, test: unittest.TestCase, config: FilterConfig) -> None:
        self.pipeline = Pipeline(test)
        self.filter = self.pipeline.filter
        self.filter.update(config)
        self.events: list[ClickEvent] = []
        self.devices: list[DeviceInfo] = []
        self.wheel: list = []
        self.filter._on_event = self.events.append
        self.filter._on_device = self.devices.append
        self.filter._on_wheel = lambda axis, dropped: self.wheel.append((axis, dropped))

    @property
    def sent(self) -> list:
        return self.pipeline.sent

    def button(self, button: Button, pressed: bool, stamp: float, name: str, passes=None, device=None) -> ClickEvent:
        self.pipeline.run_until(stamp + 0.001)
        return self.filter._button_event(button, pressed, stamp, name, location=(0, 0), passes=passes, device=device)

    def held_timers(self) -> list:
        return [timer for timer in self.pipeline.timers if timer.function.__name__ == "_commit_held"]


class SideButtonTests(unittest.TestCase):
    """Back and forward use the drop rule only: no release is ever held."""

    def setUp(self) -> None:
        self.rig = Rig(self, FilterConfig.uniform(40, [Button.BACK, Button.FORWARD]))

    def test_a_bounce_is_dropped_with_its_release_and_nothing_waits(self) -> None:
        rig = self.rig
        verdicts = [
            rig.button(Button.BACK, True, 100.000, "down").accepted,
            rig.button(Button.BACK, False, 100.060, "up").accepted,
            rig.button(Button.BACK, True, 100.068, "bounce").accepted,
            rig.button(Button.BACK, False, 100.080, "bounce up").accepted,
        ]
        self.assertEqual(verdicts, [True, True, False, False], "the release went straight through")
        self.assertEqual(rig.held_timers(), [], "no release was held")
        self.assertEqual(rig.sent, [], "nothing was re-sent")
        self.assertEqual([event.is_bounce for event in rig.events], [False, False, True, False])
        self.assertEqual(rig.filter.filtered_count, 1)

    def test_a_click_after_the_window_is_kept(self) -> None:
        rig = self.rig
        rig.button(Button.FORWARD, True, 100.000, "down")
        rig.button(Button.FORWARD, False, 100.060, "up")
        self.assertTrue(rig.button(Button.FORWARD, True, 100.110, "down").accepted)

    def test_each_button_has_its_own_window(self) -> None:
        rig = self.rig
        rig.filter.update(FilterConfig(thresholds={Button.BACK: 30, Button.FORWARD: 80}, buttons=SIDE_BUTTONS))
        for button, expected in ((Button.BACK, True), (Button.FORWARD, False)):
            rig.button(button, True, 100.000 if button is Button.BACK else 101.000, "down")
            start = 100.0 if button is Button.BACK else 101.0
            rig.button(button, False, start + 0.060, "up")
            self.assertEqual(rig.button(button, True, start + 0.110, "again").accepted, expected, button)


class PassThroughTests(unittest.TestCase):
    """Clicks that pass untouched still reach apps in the order they
    happened: a release the filter holds goes first."""

    def setUp(self) -> None:
        self.rig = Rig(self, FilterConfig.uniform(40, [Button.LEFT]))

    def test_a_touch_press_while_the_mouses_release_is_held_goes_out_behind_it(self) -> None:
        rig = self.rig
        self.assertTrue(rig.button(Button.LEFT, True, 100.000, "mouse down").accepted)
        self.assertFalse(rig.button(Button.LEFT, False, 100.080, "mouse up").accepted, "held")
        touch = rig.button(Button.LEFT, True, 100.090, "touch down", passes=PASS_TOUCH)
        self.assertFalse(touch.accepted)
        self.assertTrue(touch.deferred)
        self.assertEqual(
            rig.sent, [(Button.LEFT, False, "mouse up"), (Button.LEFT, True, "touch down")], "apps saw down, down"
        )
        # The touch's release follows its press untouched, once that is back.
        self.assertTrue(rig.button(Button.LEFT, False, 100.150, "touch up", passes=PASS_TOUCH).accepted)
        self.assertEqual([(event.pressed, event.accepted) for event in rig.events], [(True, True), (False, False)],
                         "on_event heard only the mouse's click")

    def test_a_touch_release_never_overtakes_its_re_sent_press(self) -> None:
        rig = self.rig
        rig.pipeline.round_trip = 0.050  # a busy machine
        rig.button(Button.LEFT, True, 100.000, "mouse down")
        rig.button(Button.LEFT, False, 100.080, "mouse up")
        rig.button(Button.LEFT, True, 100.090, "touch down", passes=PASS_TOUCH)
        self.assertFalse(rig.button(Button.LEFT, False, 100.095, "touch up", passes=PASS_TOUCH).accepted)
        rig.pipeline.run_until(100.400)
        self.assertEqual([entry[2] for entry in rig.sent], ["mouse up", "touch down", "touch up"])

    def test_a_double_tap_is_never_filtered(self) -> None:
        rig = self.rig
        taps = [
            rig.button(Button.LEFT, pressed, 100.000 + ms / 1000, name, passes=PASS_TOUCH).accepted
            for ms, pressed, name in ((0, True, "d1"), (40, False, "u1"), (41, True, "d2"), (80, False, "u2"))
        ]
        self.assertEqual(taps, [True] * 4, "a 1 ms release-to-press gap is how a touchpad double-taps")
        self.assertEqual((rig.sent, rig.events, rig.filter.filtered_count), ([], [], 0))
        self.assertEqual(rig.filter.passed_counts, {PASS_TOUCH: 2})

    def test_a_release_goes_the_way_its_press_went(self) -> None:
        rig = self.rig
        # Pressed on the trackpad, released after the touch ended: still untouched.
        rig.button(Button.LEFT, True, 100.000, "touch down", passes=PASS_TOUCH)
        self.assertTrue(rig.button(Button.LEFT, False, 100.080, "up").accepted)
        self.assertEqual(rig.held_timers(), [])
        # Pressed on the mouse, released while a touch reports (a race of
        # the device reports): still the mouse's click, filtered and held.
        rig.button(Button.LEFT, True, 101.000, "mouse down")
        self.assertFalse(rig.button(Button.LEFT, False, 101.080, "mouse up", passes=PASS_TOUCH).accepted)
        self.assertEqual(len(rig.held_timers()), 1)

    def test_other_buttons_releases_due_go_first(self) -> None:
        rig = Rig(self, FilterConfig.uniform(40, [Button.LEFT, Button.RIGHT]))
        rig.button(Button.RIGHT, True, 100.000, "right down")
        rig.button(Button.RIGHT, False, 100.050, "right up")
        # Stamped past the right button's window, before its timer: it
        # settles that release, and goes out behind it.
        self.assertFalse(rig.button(Button.LEFT, True, 100.092, "touch down", passes=PASS_TOUCH).accepted)
        self.assertEqual([entry[2] for entry in rig.sent], ["right up", "touch down"])

    def test_an_ignored_device_and_the_reasons_in_order(self) -> None:
        click_filter = self.rig.filter
        click_filter.update(replace(click_filter.config, ignored_devices=frozenset({"usb:046d:c08b:G502"})))
        self.assertEqual(click_filter._passes(False, "mouse", "usb:046d:c08b:G502"), PASS_DEVICE)
        self.assertIsNone(click_filter._passes(False, "mouse", "usb:046d:c08b:OTHER"))
        self.assertIsNone(click_filter._passes(False, "unknown", None))
        self.assertEqual(click_filter._passes(False, "trackpad", None), PASS_TOUCH)
        self.assertEqual(click_filter._passes(True, "mouse", "usb:046d:c08b:G502"), PASS_TOUCH)
        click_filter.update(replace(click_filter.config, excluded_apps=frozenset({"cs2.exe"})))
        click_filter._front_changed("cs2.exe")
        self.assertEqual(click_filter._passes(True, "trackpad", "usb:046d:c08b:G502"), PASS_APP)

    def test_a_filtered_click_carries_its_device(self) -> None:
        rig = self.rig
        rig.button(Button.LEFT, True, 100.000, "down", device="usb:046d:c08b:G502")
        self.assertEqual(rig.events[0].device, "usb:046d:c08b:G502")


class ExcludedAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rig = Rig(self, FilterConfig.uniform(40, [Button.LEFT], excluded_apps=frozenset({"com.valvesoftware.steam"})))

    def test_coming_to_the_front_settles_a_held_release_then_everything_passes(self) -> None:
        rig = self.rig
        rig.button(Button.LEFT, True, 100.000, "down")
        rig.button(Button.LEFT, False, 100.080, "up")
        self.assertEqual(rig.sent, [])
        rig.filter._front_changed("com.valvesoftware.steam")
        self.assertEqual(rig.sent, [(Button.LEFT, False, "up")], "settled the moment the app came to the front")
        reasons = rig.filter._passes()
        self.assertEqual(reasons, PASS_APP)
        rig.pipeline.run_until(100.200)
        verdicts = [
            rig.button(Button.LEFT, pressed, 100.200 + ms / 1000, name, passes=rig.filter._passes()).accepted
            for ms, pressed, name in ((0, True, "d"), (50, False, "u"), (55, True, "bounce"), (90, False, "u2"))
        ]
        self.assertEqual(verdicts, [True] * 4, "nothing filtered while the app is in front")
        self.assertEqual(len(rig.events), 2, "only the click from before")
        rig.filter._front_changed("com.apple.finder")
        self.assertIsNone(rig.filter._passes())

    def test_a_click_pressed_before_ends_unheld_and_a_bounce_stays_dropped(self) -> None:
        rig = Rig(self, FilterConfig.uniform(40, [Button.LEFT, Button.BACK], excluded_apps=frozenset({"game"})))
        rig.button(Button.LEFT, True, 100.000, "left down")
        rig.button(Button.BACK, True, 100.010, "back down")
        rig.button(Button.BACK, False, 100.050, "back up")
        self.assertFalse(rig.button(Button.BACK, True, 100.055, "back bounce").accepted)
        rig.filter._front_changed("game")
        self.assertTrue(rig.button(Button.LEFT, False, 100.100, "left up", passes=rig.filter._passes()).accepted,
                        "not held once the app is in front")
        self.assertEqual(rig.held_timers(), [])
        self.assertFalse(rig.button(Button.BACK, False, 100.110, "bounce up", passes=rig.filter._passes()).accepted,
                         "the bounce's release goes with its press")

    def test_excluding_the_app_already_in_front_settles_at_once(self) -> None:
        rig = Rig(self, FilterConfig.uniform(40, [Button.LEFT]))
        rig.filter._front_changed("game")
        rig.button(Button.LEFT, True, 100.000, "down")
        rig.button(Button.LEFT, False, 100.080, "up")
        rig.filter.update(replace(rig.filter.config, excluded_apps=frozenset({"game"})))
        self.assertEqual(rig.sent, [(Button.LEFT, False, "up")])
        self.assertEqual(rig.filter._passes(), PASS_APP)
        rig.filter.update(replace(rig.filter.config, excluded_apps=frozenset()))
        self.assertIsNone(rig.filter._passes())


class WheelAndDeviceReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rig = Rig(self, FilterConfig.uniform(40, [Button.LEFT], wheel_fix=True, wheel_window_ms=50))

    def test_each_judged_tick_is_reported(self) -> None:
        click_filter = self.rig.filter
        verdicts = [click_filter._wheel_tick(1, delta, 100.0 + ms / 1000) for ms, delta in ((0, -1), (20, 1), (30, -1))]
        self.assertEqual(verdicts, [True, False, True])
        self.assertEqual(self.rig.wheel, [(1, False), (1, True), (1, False)])
        self.assertEqual(click_filter.wheel_dropped, 1)

    def test_a_new_window_applies_live_and_turning_off_forgets(self) -> None:
        click_filter = self.rig.filter
        click_filter._wheel_tick(1, -1, 100.000)
        click_filter.update(replace(click_filter.config, wheel_window_ms=10))
        self.assertTrue(click_filter._wheel_tick(1, 1, 100.020))
        self.assertFalse(click_filter._wheel_tick(1, -1, 100.025))
        click_filter.update(replace(click_filter.config, wheel_fix=False))
        self.assertFalse(click_filter._wheel_on)
        click_filter.update(replace(click_filter.config, wheel_fix=True))
        self.assertTrue(click_filter._wheel_tick(1, -1, 100.026), "turning it off and on starts afresh")

    def test_a_device_is_announced_once_a_run_and_listed(self) -> None:
        click_filter = self.rig.filter
        before = time.time()
        click_filter._device_seen("usb:046d:c08b:G502", "G502 HERO", "mouse")
        click_filter._device_seen("usb:046d:c08b:G502", "G502 HERO", "mouse")
        click_filter._device_seen("fifo:0000:0000:Apple Internal Keyboard / Trackpad", "Trackpad", "trackpad")
        click_filter._device_seen(None, "nothing", "mouse")
        self.assertEqual([device.key for device in self.rig.devices],
                         ["usb:046d:c08b:G502", "fifo:0000:0000:Apple Internal Keyboard / Trackpad"])
        self.assertEqual([device.filtered for device in self.rig.devices], [True, False])
        listed = {device.key: device for device in click_filter.seen_devices()}
        self.assertEqual(set(listed), {"usb:046d:c08b:G502", "fifo:0000:0000:Apple Internal Keyboard / Trackpad"})
        self.assertGreaterEqual(listed["usb:046d:c08b:G502"].last_seen, before, "epoch seconds")
        self.assertLessEqual(listed["usb:046d:c08b:G502"].last_seen, time.time())
        click_filter.update(replace(click_filter.config, ignored_devices=frozenset({"usb:046d:c08b:G502"})))
        self.assertFalse({device.key: device for device in click_filter.seen_devices()}["usb:046d:c08b:G502"].filtered)

    def test_a_touch_collection_makes_its_device_a_touch_device(self) -> None:
        click_filter = self.rig.filter
        click_filter._device_seen("hid:04f3:3087:ELAN Touchpad", "ELAN Touchpad", "mouse")
        click_filter._device_seen("hid:04f3:3087:ELAN Touchpad", "ELAN Touchpad", "trackpad")
        self.assertEqual(click_filter.seen_devices()[0].kind, "trackpad")
        self.assertFalse(click_filter.seen_devices()[0].filtered)

    def test_the_device_lookup_says_whether_devices_can_be_told_apart(self) -> None:
        from unittest import mock

        from app import devices_mac

        click_filter = self.rig.filter
        with mock.patch("app.platform.platform.system", return_value="Windows"):
            self.assertEqual(click_filter.device_lookup, "not started")
            click_filter._device_lookup = "raw input unavailable: RegisterRawInputDevices failed (error 5)"
            self.assertEqual(click_filter.device_lookup, "raw input unavailable: RegisterRawInputDevices failed (error 5)")
        with mock.patch("app.platform.platform.system", return_value="Darwin"), \
                mock.patch.object(devices_mac, "lookup_status", return_value="iokit ok"):
            self.assertEqual(click_filter.device_lookup, "iokit ok")
        with mock.patch.object(devices_mac, "_shared_registry", [None]), \
                mock.patch.object(devices_mac, "_registry_failure", ["OSError: no IOKit"]):
            self.assertEqual(devices_mac.lookup_status(), "iokit unavailable: OSError: no IOKit")
        with mock.patch.object(devices_mac, "_shared_registry", [object()]):
            self.assertEqual(devices_mac.lookup_status(), "iokit ok")


if __name__ == "__main__":
    unittest.main()
