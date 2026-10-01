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


class FakeTimer:
    """Stands in for threading.Timer: records each timer instead of starting
    it, so a test decides exactly when it fires instead of racing the clock."""

    created: list = []
    daemon = True

    def __init__(self, _interval, function, args=()):
        self.function, self.args = function, args
        FakeTimer.created.append(self)

    @classmethod
    def reset(cls) -> list:
        cls.created = []
        return cls.created

    def start(self):
        pass

    def is_alive(self):
        return False

    def cancel(self):
        pass

    def fire(self):
        self.function(*self.args)


class HeldReleaseTests(unittest.TestCase):
    """The hook re-injects what the core holds back, in the right order."""

    def setUp(self) -> None:
        from unittest import mock

        self.injected = []
        self.timers = FakeTimer.reset()
        patch = mock.patch("app.platform.threading.Timer", FakeTimer)
        patch.start()
        self.addCleanup(patch.stop)
        self.filter = GlobalClickFilter(40, [Button.LEFT])
        self.filter._use_os_time = True
        self.filter._inject = lambda button, pressed, template: self.injected.append((pressed, template))

    def fire_all(self) -> None:
        for timer in list(self.timers):
            timer.fire()

    def test_dropout_is_swallowed_and_nothing_is_injected(self) -> None:
        self.assertTrue(self.filter._handle(Button.LEFT, True, 0.0, "down").accepted)
        self.assertFalse(self.filter._handle(Button.LEFT, False, 0.5, "up").accepted)
        self.assertFalse(self.filter._handle(Button.LEFT, True, 0.51, "down2").accepted)
        self.fire_all()  # the window ends
        self.assertEqual(self.injected, [])

    def test_real_release_is_delivered_after_the_window(self) -> None:
        self.filter._handle(Button.LEFT, True, 0.0, "down")
        self.filter._handle(Button.LEFT, False, 0.5, "up")
        self.assertEqual(self.injected, [], "nothing before the window ends")
        self.fire_all()
        self.assertEqual(self.injected, [(False, "up")])

    def test_late_press_replays_release_then_press(self) -> None:
        self.filter._handle(Button.LEFT, True, 0.0, "down")
        self.filter._handle(Button.LEFT, False, 0.5, "up")
        # Arrives after the window, but before the timer has fired.
        event = self.filter._handle(Button.LEFT, True, 0.6, "down2")
        self.assertFalse(event.accepted)
        self.assertEqual(self.injected, [(False, "up"), (True, "down2")])
        self.fire_all()
        self.assertEqual(len(self.injected), 2, "the late timer must not deliver it twice")

    def test_stopping_delivers_a_held_release(self) -> None:
        self.filter._handle(Button.LEFT, True, 0.0, "down")
        self.filter._handle(Button.LEFT, False, 0.5, "up")
        self.filter.stop()
        self.assertEqual(self.injected, [(False, "up")], "apps must not think the button is stuck")

    def test_bounce_as_the_contact_closes_keeps_the_drag(self) -> None:
        # Press, a 3 ms flicker open, closed again: the start of a drag.
        self.filter._handle(Button.LEFT, True, 0.000, "down")
        self.assertTrue(self.filter._handle(Button.LEFT, False, 0.003, "flicker").held)
        self.assertTrue(self.filter._handle(Button.LEFT, True, 0.008, "back").cancels_held)
        lift = self.filter._handle(Button.LEFT, False, 0.500, "lift")
        self.assertTrue(lift.held, "the real lift must not be swallowed")
        self.fire_all()
        self.assertEqual(self.injected, [(False, "lift")])

    def test_releases_in_one_tick_keep_their_own_windows(self) -> None:
        # Windows stamps events in ~16 ms ticks: these all share one time.
        self.filter._handle(Button.LEFT, True, 1.0, "down")
        self.filter._handle(Button.LEFT, False, 1.3, "up1")
        self.filter._handle(Button.LEFT, True, 1.3, "back1")
        self.filter._handle(Button.LEFT, False, 1.3, "up2")
        first, _second = self.timers
        first.fire()
        self.assertEqual(self.injected, [], "the first timer must not settle the second release")


class TimerTokenTests(unittest.TestCase):
    """A timer settles only the release it was started for."""

    def test_second_dropout_keeps_its_own_window(self) -> None:
        from unittest import mock

        injected = []
        timers = FakeTimer.reset()

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


class ClickCountRepairTests(unittest.TestCase):
    """macOS numbers presses before the filter runs; the repair undoes that."""

    def run_chain(self, chain):
        """chain: (pressed, os_state, accepted, flush) per event -> states apps see."""
        from app.core import ClickEvent
        from app.platform import ClickCountRepair

        repair = ClickCountRepair()
        seen = []
        for pressed, state, accepted, flush in chain:
            corrected = repair.correct(Button.LEFT, pressed, state)
            repair.record(Button.LEFT, ClickEvent(Button.LEFT, pressed, accepted, None, None, flush_held=flush))
            if accepted or flush:
                seen.append((pressed, corrected))
        return seen

    def test_triple_click_with_one_bounce_stays_a_triple_click(self) -> None:
        seen = self.run_chain([
            (True, 1, True, False), (False, 1, True, False),
            (True, 2, False, False), (False, 2, False, False),   # bounce, suppressed
            (True, 3, True, False), (False, 3, True, False),
            (True, 4, True, False), (False, 4, True, False),
        ])
        self.assertEqual([state for pressed, state in seen if pressed], [1, 2, 3])
        self.assertEqual([state for pressed, state in seen if not pressed], [1, 2, 3])

    def test_dropout_mid_drag_keeps_the_release_a_single_click(self) -> None:
        seen = self.run_chain([
            (True, 1, True, False),
            (False, 1, False, False),   # held, then cancelled
            (True, 2, False, False),    # the contact coming back
            (False, 2, True, False),    # the real lift
        ])
        self.assertEqual(seen, [(True, 1), (False, 1)])

    def test_a_new_chain_clears_the_count(self) -> None:
        seen = self.run_chain([
            (True, 1, True, False), (False, 1, True, False),
            (True, 2, False, False), (False, 2, False, False),
            (True, 1, True, False), (False, 1, True, False),     # later, a fresh click
            (True, 2, True, False),                              # a real double-click
        ])
        self.assertEqual([state for pressed, state in seen if pressed], [1, 1, 2])

    def test_a_reordered_press_still_counts(self) -> None:
        seen = self.run_chain([
            (True, 1, True, False), (False, 1, False, False),    # held release
            (True, 2, False, True),                              # flushed: apps get it
        ])
        self.assertEqual(seen, [(True, 1), (True, 2)])


if __name__ == "__main__":
    unittest.main()
