"""Tests for the platform-facing filter that do not need a real mouse."""

import platform
import unittest
from time import monotonic

from app.core import Button
from app.platform import GlobalClickFilter, HookError, is_supported


class HandlerTests(unittest.TestCase):
    """Drive the code path both native hooks call, without installing one."""

    def setUp(self) -> None:
        self.events = []
        self.filter = GlobalClickFilter(60, [Button.LEFT], on_event=self.events.append)
        # Pretend the platform timestamps line up with our clock.
        self.filter._use_os_time = True

    def click(self, button: Button, press_at: float, release_at: float):
        press = self.filter._handle(button, True, press_at)
        release = self.filter._handle(button, False, release_at)
        return press, release

    def test_bounce_is_rejected_and_counted(self) -> None:
        self.click(Button.LEFT, 1.0, 1.05)
        press, release = self.click(Button.LEFT, 1.06, 1.07)
        self.assertFalse(press.accepted)
        self.assertFalse(release.accepted)
        self.assertEqual(self.filter.filtered_count, 1)

    def test_unselected_buttons_pass_through(self) -> None:
        self.click(Button.RIGHT, 1.0, 1.05)
        press, _ = self.click(Button.RIGHT, 1.06, 1.07)
        self.assertTrue(press.accepted, "the right button was never selected for filtering")
        self.assertEqual(self.filter.filtered_count, 0)

    def test_buttons_are_tracked_separately(self) -> None:
        self.filter.update(buttons=[Button.LEFT, Button.RIGHT])
        self.click(Button.LEFT, 1.0, 1.05)
        press, _ = self.click(Button.RIGHT, 1.06, 1.10)
        self.assertTrue(press.accepted, "a right click is not bounce from the left switch")

    def test_threshold_updates_while_running(self) -> None:
        self.filter.update(threshold_ms=10)
        self.click(Button.LEFT, 1.0, 1.02)
        press, _ = self.click(Button.LEFT, 1.05, 1.06)  # 30 ms gap
        self.assertTrue(press.accepted)

    def test_every_event_is_reported_to_the_ui(self) -> None:
        self.click(Button.LEFT, 1.0, 1.05)
        self.click(Button.LEFT, 1.06, 1.07)
        self.assertEqual(len(self.events), 4)
        self.assertEqual([event.is_bounce for event in self.events], [False, False, True, False])

    def test_a_failing_ui_callback_cannot_break_the_hook(self) -> None:
        def explode(_event):
            raise ValueError("UI is gone")

        click_filter = GlobalClickFilter(60, [Button.LEFT], on_event=explode)
        self.assertTrue(click_filter._handle(Button.LEFT, True, 1.0).accepted)


class ClockTests(unittest.TestCase):
    def test_unusable_event_timestamps_fall_back_to_the_local_clock(self) -> None:
        click_filter = GlobalClickFilter(60, [Button.LEFT])
        # Synthetic events can arrive stamped with zero; a gap measured against
        # that would be meaningless.
        first = click_filter._normalise_time(0.0)
        self.assertAlmostEqual(first, monotonic(), delta=1.0)
        self.assertFalse(click_filter._use_os_time)

    def test_plausible_timestamps_are_used_as_given(self) -> None:
        click_filter = GlobalClickFilter(60, [Button.LEFT])
        stamp = monotonic()
        self.assertEqual(click_filter._normalise_time(stamp), stamp)
        self.assertTrue(click_filter._use_os_time)

    def test_missing_timestamp_is_replaced(self) -> None:
        click_filter = GlobalClickFilter(60, [Button.LEFT])
        self.assertAlmostEqual(click_filter._normalise_time(None), monotonic(), delta=1.0)


class LifecycleTests(unittest.TestCase):
    def test_unsupported_platforms_report_clearly(self) -> None:
        if is_supported():
            self.skipTest("this platform does support hooks")
        with self.assertRaises(HookError):
            GlobalClickFilter(60, [Button.LEFT]).start()

    @unittest.skipUnless(platform.system() == "Darwin", "macOS event tap")
    def test_macos_tap_starts_and_stops(self) -> None:
        click_filter = GlobalClickFilter(60, [Button.LEFT])
        try:
            click_filter.start()
        except HookError as error:
            self.skipTest(f"Accessibility permission is not granted here: {error}")
        self.assertTrue(click_filter.running)
        click_filter.stop()
        self.assertFalse(click_filter.running)


if __name__ == "__main__":
    unittest.main()


class HeldReleaseTests(unittest.TestCase):
    """The hook re-injects what the core holds back, in the right order."""

    def setUp(self) -> None:
        self.injected = []
        self.filter = GlobalClickFilter(40, [Button.LEFT])
        self.filter._use_os_time = True
        self.filter._inject = lambda button, pressed, template: self.injected.append((pressed, template))

    def test_dropout_is_swallowed_and_nothing_is_injected(self) -> None:
        from time import sleep

        self.assertTrue(self.filter._handle(Button.LEFT, True, 0.0, "down").accepted)
        self.assertFalse(self.filter._handle(Button.LEFT, False, 0.5, "up").accepted)
        self.assertFalse(self.filter._handle(Button.LEFT, True, 0.51, "down2").accepted)
        sleep(0.1)  # past the 40 ms window
        self.assertEqual(self.injected, [])

    def test_real_release_is_delivered_after_the_window(self) -> None:
        from time import sleep

        self.filter._handle(Button.LEFT, True, 0.0, "down")
        self.filter._handle(Button.LEFT, False, 0.5, "up")
        sleep(0.15)
        self.assertEqual(self.injected, [(False, "up")])

    def test_late_press_replays_release_then_press(self) -> None:
        self.filter._handle(Button.LEFT, True, 0.0, "down")
        self.filter._handle(Button.LEFT, False, 0.5, "up")
        # Arrives after the window but before the timer fires.
        self.filter._filters[Button.LEFT].threshold_ms = 40
        event = self.filter._handle(Button.LEFT, True, 0.6, "down2")
        self.assertFalse(event.accepted)
        self.assertEqual(self.injected, [(False, "up"), (True, "down2")])

    def test_held_copy_gets_its_click_count_repaired(self) -> None:
        repaired = []
        self.filter._repair_template = lambda template, run: repaired.append((template, run))
        self.filter._handle(Button.LEFT, True, 0.0, "down")
        self.filter._handle(Button.LEFT, False, 0.08, "up")
        self.filter._handle(Button.LEFT, True, 0.087, "bounce")   # cancels the held up
        self.filter._handle(Button.LEFT, False, 0.095, "up2")     # held again
        self.assertEqual(repaired, [("up2", 1)])
        self.filter.stop()

    def test_stopping_delivers_a_held_release(self) -> None:
        self.filter._handle(Button.LEFT, True, 0.0, "down")
        self.filter._handle(Button.LEFT, False, 0.5, "up")
        self.filter.stop()
        self.assertEqual(self.injected, [(False, "up")], "apps must not think the button is stuck")


class TimerTokenTests(unittest.TestCase):
    """A timer settles only the release it was started for."""

    def test_second_dropout_keeps_its_own_window(self) -> None:
        from unittest import mock

        injected = []
        timers = []

        class FakeTimer:
            """Records the timer instead of starting it, so the test decides
            exactly when each one fires."""

            def __init__(self, _interval, function, args):
                self.function, self.args = function, args
                timers.append(self)

            daemon = True

            def start(self):
                pass

            def is_alive(self):
                return False

            def cancel(self):
                pass

            def fire(self):
                self.function(*self.args)

        click_filter = GlobalClickFilter(40, [Button.LEFT])
        click_filter._use_os_time = True
        click_filter._inject = lambda button, pressed, template: injected.append((pressed, template))
        with mock.patch("app.platform.threading.Timer", FakeTimer):
            click_filter._handle(Button.LEFT, True, 1.000, "down")
            click_filter._handle(Button.LEFT, False, 1.300, "up1")    # dropout one: timer A
            click_filter._handle(Button.LEFT, True, 1.305, "back1")   # cancels it
            click_filter._handle(Button.LEFT, False, 1.310, "up2")    # dropout two: timer B
            timer_a, timer_b = timers
            timer_a.fire()  # A's window is over, but B's is not
            self.assertEqual(injected, [], "the first timer delivered the second release early")
            click_filter._handle(Button.LEFT, True, 1.315, "back2")   # inside B's window
            timer_b.fire()
            self.assertEqual(injected, [], "the drag should carry on through both dropouts")
            click_filter._handle(Button.LEFT, False, 2.000, "lift")   # the real lift: timer C
            timers[-1].fire()
            self.assertEqual(injected, [(False, "lift")])


class TickClockTests(unittest.TestCase):
    def test_wrap_does_not_make_a_negative_gap(self) -> None:
        from app.platform import TickClock

        clock = TickClock()
        before = clock.seconds(0xFFFFFFF0)        # 16 ms before the wrap
        after = clock.seconds(0x00000010)         # 16 ms after it
        self.assertAlmostEqual((after - before) * 1000, 32, places=6)

    def test_slightly_older_event_is_not_a_wrap(self) -> None:
        from app.platform import TickClock

        clock = TickClock()
        first = clock.seconds(1_000_000)
        second = clock.seconds(999_990)
        self.assertAlmostEqual((second - first) * 1000, -10, places=6)

    def test_first_event_lands_on_the_monotonic_clock(self) -> None:
        from app.platform import TickClock

        clock = TickClock(lambda: 5_000_020)
        self.assertAlmostEqual(clock.seconds(5_000_000), monotonic() - 0.02, delta=0.05)

    def test_missing_stamp_falls_back(self) -> None:
        from app.platform import TickClock

        self.assertIsNone(TickClock().seconds(0))


class AllowHoldTests(unittest.TestCase):
    def test_release_goes_straight_through_when_it_cannot_be_resent(self) -> None:
        click_filter = GlobalClickFilter(40, [Button.LEFT])
        click_filter._use_os_time = True
        click_filter._handle(Button.LEFT, True, 0.0, "down")
        release = click_filter._handle(Button.LEFT, False, 0.5, "up", allow_hold=False)
        self.assertTrue(release.accepted)
        self.assertFalse(release.held)
