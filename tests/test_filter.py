"""Tests for the platform-facing filter that do not need a real mouse."""

import platform
import sys
import threading
import unittest
from time import monotonic
from types import SimpleNamespace
from unittest import mock

from app.core import Button, ClickEvent
from app.platform import (
    INJECTED_MARK,
    MOTION_MARK_FOR,
    RESTORE_MARK,
    TAP_DISABLED_MESSAGE,
    ClickCountRepair,
    GlobalClickFilter,
    HookError,
    is_supported,
)


def fresh_error_log():
    """Each place that ignores an error logs only its first one per run of
    the app; start a test with none of them logged yet."""
    return mock.patch("app.platform._logged_sites", set())


class HandlerTests(unittest.TestCase):
    """Drive the code path both native hooks call, without installing one."""

    def setUp(self) -> None:
        FakeTimer.reset()
        patch = mock.patch("app.platform.threading.Timer", FakeTimer)
        patch.start()
        self.addCleanup(patch.stop)
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
        # Delivered, after the release held before it.
        self.assertFalse(press.is_bounce)
        self.assertTrue(press.flush_held)

    def test_every_event_is_reported_to_the_ui(self) -> None:
        self.click(Button.LEFT, 1.0, 1.05)
        self.click(Button.LEFT, 1.06, 1.07)
        self.assertEqual(len(self.events), 4)
        self.assertEqual([event.is_bounce for event in self.events], [False, False, True, False])

    def test_a_failing_ui_callback_cannot_break_the_hook(self) -> None:
        def explode(_event):
            raise ValueError("UI is gone")

        click_filter = GlobalClickFilter(60, [Button.LEFT], on_event=explode)
        with fresh_error_log(), self.assertLogs("app.platform", "WARNING") as logged:
            self.assertTrue(click_filter._handle(Button.LEFT, True, 1.0).accepted)
            self.assertTrue(click_filter._handle(Button.LEFT, False, 1.1).held)
        self.assertEqual(len(logged.records), 1, "logged once, not on every click")
        self.assertIsInstance(logged.records[0].exc_info[1], ValueError)


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
    """macOS numbers presses before the filter runs, and a suppressed press
    both adds one and restarts the double-click interval. The repair counts
    over the presses apps receive instead, by Apple's rule."""

    INTERVAL = 0.5

    def run_chain(self, chain, interval=None):
        """chain: (time, pressed, os_state, kind) per event, kind one of
        "ok" (let through), "bounce", "held", "flush" or "deferred"
        -> (pressed, state) for each event apps receive."""
        repair = ClickCountRepair(interval=lambda: interval or self.INTERVAL)
        seen = []
        for moment, pressed, state, kind in chain:
            corrected = repair.correct(Button.LEFT, pressed, state, moment)
            result = ClickEvent(
                Button.LEFT, pressed, kind == "ok", None, None,
                held=kind == "held", flush_held=kind == "flush", deferred=kind == "deferred",
            )
            repair.record(Button.LEFT, result)
            if kind != "bounce":
                seen.append((pressed, corrected))
        return seen

    def presses(self, seen):
        return [state for pressed, state in seen if pressed]

    def test_release_chatter_does_not_chain_a_click_made_after_the_interval(self) -> None:
        # The bounce press at 0.105 s keeps macOS's chain going, so the click
        # at interval + 60 ms after the real one arrives numbered 3.
        seen = self.run_chain([
            (0.000, True, 1, "ok"), (0.080, False, 1, "held"),
            (0.105, True, 2, "bounce"), (0.108, False, 2, "bounce"),
            (0.560, True, 3, "ok"), (0.640, False, 3, "held"),
        ])
        self.assertEqual(seen, [(True, 1), (False, 1), (True, 1), (False, 1)])

    def test_a_dropout_in_a_hold_does_not_chain_the_next_click(self) -> None:
        seen = self.run_chain([
            (0.000, True, 1, "ok"),
            (0.400, False, 1, "held"),        # contact drops out...
            (0.405, True, 2, "bounce"),       # ...and comes back: cancels_held
            (0.450, False, 2, "held"),        # the real lift
            (0.800, True, 3, "ok"),           # inside macOS's window from 0.405 only
        ])
        self.assertEqual(self.presses(seen), [1, 1])
        self.assertEqual(seen[1], (False, 1), "the lift carries its press's count")

    def test_double_click_with_a_bounce_stays_a_double_click(self) -> None:
        seen = self.run_chain([
            (0.000, True, 1, "ok"), (0.080, False, 1, "held"),
            (0.085, True, 2, "bounce"), (0.088, False, 2, "bounce"),
            (0.200, True, 3, "ok"), (0.280, False, 3, "held"),
        ])
        self.assertEqual(seen, [(True, 1), (False, 1), (True, 2), (False, 2)])

    def test_triple_click_with_a_bounce_stays_a_triple_click(self) -> None:
        seen = self.run_chain([
            (0.000, True, 1, "ok"), (0.080, False, 1, "held"),
            (0.085, True, 2, "bounce"), (0.088, False, 2, "bounce"),
            (0.200, True, 3, "ok"), (0.280, False, 3, "held"),
            (0.400, True, 4, "ok"), (0.480, False, 4, "held"),
        ])
        self.assertEqual(self.presses(seen), [1, 2, 3])
        self.assertEqual([state for pressed, state in seen if not pressed], [1, 2, 3])

    def test_macos_starting_a_chain_starts_one(self) -> None:
        # Within the interval, but macOS saw the pointer move away: state 1.
        seen = self.run_chain([(0.0, True, 1, "ok"), (0.1, False, 1, "held"), (0.2, True, 1, "ok")])
        self.assertEqual(self.presses(seen), [1, 1])

    def test_never_more_than_macos_counted(self) -> None:
        seen = self.run_chain([(0.0, True, 1, "ok"), (0.1, False, 1, "held"),
                               (0.2, True, 2, "ok"), (0.3, False, 2, "held"),
                               (0.4, True, 2, "ok")])
        self.assertEqual(self.presses(seen), [1, 2, 2])

    def test_deferred_and_reordered_presses_count(self) -> None:
        # Held back behind a re-sent release, or re-ordered after a late
        # one: apps still get them, so the chain moves on.
        seen = self.run_chain([
            (0.0, True, 1, "ok"), (0.1, False, 1, "held"),
            (0.2, True, 2, "flush"), (0.3, False, 2, "held"),
            (0.4, True, 3, "deferred"),
        ])
        self.assertEqual(self.presses(seen), [1, 2, 3])

    def test_the_interval_is_the_users_setting(self) -> None:
        chain = [(0.0, True, 1, "ok"), (0.1, False, 1, "held"), (0.9, True, 2, "ok")]
        self.assertEqual(self.presses(self.run_chain(chain, interval=0.5)), [1, 1])
        self.assertEqual(self.presses(self.run_chain(chain, interval=5.0)), [1, 2])

    def test_the_interval_is_read_live_but_not_on_every_press(self) -> None:
        reads = []
        setting = [0.5]

        def read():
            reads.append(setting[0])
            return setting[0]

        repair = ClickCountRepair(interval=read)
        with mock.patch("app.platform.monotonic", return_value=100.0):
            self.assertEqual(repair.interval(), 0.5)
            setting[0] = 0.8
            self.assertEqual(repair.interval(), 0.5, "cached")
        with mock.patch("app.platform.monotonic", return_value=103.0):
            self.assertEqual(repair.interval(), 0.8, "a changed setting is picked up")
        self.assertEqual(reads, [0.5, 0.8])

    def test_an_unreadable_setting_keeps_the_last_value(self) -> None:
        def broken():
            raise RuntimeError("no preferences")

        with fresh_error_log(), self.assertLogs("app.platform", "WARNING"):
            self.assertEqual(ClickCountRepair(interval=broken).interval(), 0.5)


class ResendOrderTests(unittest.TestCase):
    """A real event must never overtake one the app re-sent just before it."""

    def setUp(self) -> None:
        from unittest import mock

        self.timers = FakeTimer.reset()
        patch = mock.patch("app.platform.threading.Timer", FakeTimer)
        patch.start()
        self.addCleanup(patch.stop)
        self.sent = []
        self.filter = GlobalClickFilter(40, [Button.LEFT])
        self.filter._use_os_time = True
        self.filter._inject = lambda button, pressed, template: self.sent.append((pressed, template))

    def comes_back(self) -> None:
        """The hook sees the oldest re-sent event pass, as the OS delivers it."""
        self.filter._injected_passed(Button.LEFT)

    def test_press_right_as_the_window_ends_waits_for_the_resent_release(self) -> None:
        self.filter._handle(Button.LEFT, True, 1.000, "down1")
        self.filter._handle(Button.LEFT, False, 1.100, "up1")         # held
        self.timers[0].fire()                                         # re-sent...
        self.assertEqual(self.sent, [(False, "up1")])
        # ...but the next press is already queued ahead of it in the system.
        press = self.filter._handle(Button.LEFT, True, 1.150, "down2")
        self.assertFalse(press.accepted, "it must not overtake the re-sent release")
        self.assertTrue(press.deferred)
        self.assertFalse(press.is_bounce, "a deferred press is not a bounce")
        self.comes_back()                                             # up1 delivered
        self.assertEqual(self.sent, [(False, "up1"), (True, "down2")])
        self.comes_back()                                             # down2 delivered
        self.assertTrue(self.filter._handle(Button.LEFT, False, 1.170, "up2").held)
        self.timers[-1].fire()
        self.assertEqual(self.sent, [(False, "up1"), (True, "down2"), (False, "up2")])

    def test_events_flow_normally_once_nothing_is_in_flight(self) -> None:
        self.filter._handle(Button.LEFT, True, 1.0, "down1")
        self.filter._handle(Button.LEFT, False, 1.1, "up1")
        self.timers[0].fire()
        self.comes_back()
        self.assertTrue(self.filter._handle(Button.LEFT, True, 1.3, "down2").accepted)

    def test_a_resent_event_that_never_returns_does_not_block_clicks(self) -> None:
        from unittest import mock

        self.filter._handle(Button.LEFT, True, 1.0, "down1")
        self.filter._handle(Button.LEFT, False, 1.1, "up1")
        self.timers[0].fire()                                         # never comes back
        self.assertTrue(self.filter._handle(Button.LEFT, True, 1.15, "down2").deferred)
        later = monotonic() + 1.0
        with mock.patch("app.platform.monotonic", return_value=later):
            self.timers[-1].fire()                                    # the give-up timer
        self.assertEqual(self.sent[-1], (True, "down2"))

    def test_a_send_that_fails_is_not_waited_for(self) -> None:
        self.filter._inject = lambda button, pressed, template: False
        self.filter._handle(Button.LEFT, True, 1.0, "down1")
        self.filter._handle(Button.LEFT, False, 1.1, "up1")
        self.timers[0].fire()
        self.assertTrue(self.filter._handle(Button.LEFT, True, 1.3, "down2").accepted)


class MotionFlushTests(unittest.TestCase):
    """A click made in place ends when the pointer moves off it, and only then."""

    def setUp(self) -> None:
        self.timers = FakeTimer.reset()
        patch = mock.patch("app.platform.threading.Timer", FakeTimer)
        patch.start()
        self.addCleanup(patch.stop)
        self.injected = []
        self.filter = GlobalClickFilter(40, [Button.LEFT])
        self.filter._use_os_time = True
        self.filter._inject = lambda button, pressed, template: self.injected.append((pressed, template))

    def handle(self, pressed, moment, name, at):
        return self.filter._handle(Button.LEFT, pressed, moment, name, location=at)

    def fire_all(self) -> None:
        for timer in list(self.timers):
            timer.fire()

    def test_motion_delivers_a_stationary_release_before_itself(self) -> None:
        self.handle(True, 0.0, "down", (100, 100))
        self.assertTrue(self.handle(False, 0.08, "up", (101, 100)).held)
        self.assertFalse(self.filter._motion("m1", (106, 100)), "the motion waits behind the release")
        self.assertEqual(self.injected, [(False, "up")])
        self.filter._injected_passed(Button.LEFT)                     # the up comes back
        self.assertEqual(self.injected, [(False, "up"), (None, "m1")])
        self.filter._injected_passed(Button.LEFT)                     # the motion comes back
        self.assertTrue(self.filter._motion("m2", (110, 100)), "nothing left in flight")
        self.fire_all()
        self.assertEqual(self.injected, [(False, "up"), (None, "m1")], "the timer must not resend the up")

    def test_motion_within_the_click_passes_and_keeps_the_hold(self) -> None:
        # One count of tremor during a dropout at the start of a drag.
        self.handle(True, 0.0, "down", (100, 100))
        self.assertTrue(self.handle(False, 0.06, "drop", (100, 100)).held)
        self.assertTrue(self.filter._motion("tremor", (101, 100)), "small motion passes unqueued")
        self.assertEqual(self.injected, [])
        self.assertTrue(self.handle(True, 0.07, "back", (101, 100)).cancels_held, "the drag is kept")
        self.handle(False, 1.0, "lift", (300, 100))
        self.fire_all()
        self.assertEqual(self.injected, [(False, "lift")])

    def test_the_click_spot_is_where_the_button_came_up(self) -> None:
        # Released 3 pt from the press: in place. Motion 5 pt from the press
        # but only 2 pt from the release is still on the click.
        self.handle(True, 0.0, "down", (0, 0))
        self.handle(False, 0.08, "up", (3, 0))
        self.assertTrue(self.filter._motion("m", (5, 0)))
        self.assertEqual(self.injected, [])
        self.assertFalse(self.filter._motion("m2", (7, 0)))
        self.assertEqual(self.injected, [(False, "up")])

    def test_motion_during_a_moving_hold_passes_and_keeps_the_drag(self) -> None:
        self.handle(True, 0.0, "down", (0, 0))
        self.assertTrue(self.handle(False, 0.08, "up", (50, 0)).held)
        self.assertTrue(self.filter._motion("m", (60, 0)))
        self.assertEqual(self.injected, [])
        self.assertTrue(self.handle(True, 0.09, "back", (61, 0)).cancels_held)

    def test_a_cancelled_stationary_dropout_lets_motion_through(self) -> None:
        self.handle(True, 0.0, "down", (0, 0))
        self.handle(False, 0.06, "up", (0, 0))
        self.assertTrue(self.handle(True, 0.075, "back", (0, 0)).cancels_held)
        self.assertTrue(self.filter._motion("m", (20, 0)))
        self.assertEqual(self.injected, [])

    def test_motion_never_settles_a_release_as_the_contact_closes(self) -> None:
        # Press, a 3 ms flicker open, motion, then the contact closes again.
        self.handle(True, 0.000, "down", (0, 0))
        flicker = self.handle(False, 0.003, "flicker", (0, 0))
        self.assertEqual(flicker.hold_reason, "closing")
        self.assertTrue(self.filter._motion("m", (10, 0)))
        self.assertEqual(self.injected, [])
        self.assertTrue(self.handle(True, 0.008, "back", (10, 0)).cancels_held, "the drag starts")

    def test_release_chatter_then_moving_off_delivers_the_click_on_the_way(self) -> None:
        # The finger lets go, the contact chatters open once more, and the
        # hand moves straight on: the first motion past the click spot
        # delivers the release, as for a clean click, not the timer.
        self.handle(True, 0.000, "down", (100, 100))
        self.assertTrue(self.handle(False, 0.090, "up", (100, 100)).held)
        self.assertTrue(self.handle(True, 0.093, "back", (100, 100)).cancels_held)
        self.assertEqual(self.handle(False, 0.096, "up2", (100, 100)).hold_reason, "lift")
        self.assertTrue(self.filter._motion("m1", (102, 100)))
        self.assertEqual(self.injected, [])
        self.assertFalse(self.filter._motion("m2", (106, 100)), "waits behind the release")
        self.assertEqual(self.injected, [(False, "up2")])
        self.fire_all()
        self.assertEqual(self.injected, [(False, "up2")], "delivered once")

    def test_two_bounces_as_the_contact_closes_never_settle_on_motion(self) -> None:
        # D, U at 4.7 ms, D at 7.8 ms, U again at 12.7 ms with the hand
        # already moving: the start of a drag, not two clicks.
        self.handle(True, 0.0000, "down", (0, 0))
        self.assertEqual(self.handle(False, 0.0047, "u1", (0, 0)).hold_reason, "closing")
        self.assertTrue(self.handle(True, 0.0078, "d1", (0, 0)).cancels_held)
        self.assertEqual(self.handle(False, 0.0127, "u2", (1, 0)).hold_reason, "closing")
        self.assertTrue(self.filter._motion("m", (10, 0)))
        self.assertTrue(self.handle(True, 0.0135, "d2", (10, 0)).cancels_held, "the drag starts")
        self.assertEqual(self.injected, [])
        self.handle(False, 1.0, "lift", (200, 0))
        self.fire_all()
        self.assertEqual(self.injected, [(False, "lift")])

    def test_two_dropouts_within_the_click_keep_the_drag(self) -> None:
        self.handle(True, 0.000, "down", (0, 0))
        self.assertTrue(self.handle(False, 0.300, "drop1", (0, 0)).held)
        self.assertTrue(self.handle(True, 0.310, "back1", (1, 0)).cancels_held)
        self.assertTrue(self.handle(False, 0.400, "drop2", (2, 0)).held)
        self.assertTrue(self.filter._motion("m", (3, 0)), "motion in the second gap passes")
        self.assertTrue(self.handle(True, 0.410, "back2", (3, 0)).cancels_held)
        self.assertEqual(self.injected, [], "nothing reaches apps before the real lift")
        self.handle(False, 1.500, "lift", (200, 0))
        self.fire_all()
        self.assertEqual(self.injected, [(False, "lift")])

    def test_a_comeback_press_does_not_move_the_click_spot(self) -> None:
        # A slow drag: the first dropout lands 60 pt from the press. Its
        # comeback never reached apps, so a second dropout 2.5 pt further on
        # is still part of the drag, not a click made in place.
        self.handle(True, 0.000, "down", (100, 100))
        self.assertTrue(self.handle(False, 0.600, "drop1", (160, 100)).held)
        self.assertTrue(self.handle(True, 0.617, "back1", (160.5, 100)).cancels_held)
        self.assertTrue(self.handle(False, 0.700, "drop2", (163, 100)).held)
        self.assertFalse(self.filter._held_stationary.get(Button.LEFT))
        self.assertTrue(self.filter._motion("m", (170, 100)))
        self.assertTrue(self.handle(True, 0.703, "back2", (171, 100)).cancels_held)
        self.handle(False, 1.500, "lift", (250, 100))
        self.fire_all()
        self.assertEqual(self.injected, [(False, "lift")])

    def test_a_bounce_press_does_not_move_the_click_spot(self) -> None:
        self.handle(True, 0.000, "down", (0, 0))
        self.handle(False, 0.100, "up", (0, 0))
        self.fire_all()                                               # the click is over
        self.filter._injected_passed(Button.LEFT)
        self.assertTrue(self.handle(True, 0.110, "bounce", (40, 0)).is_bounce)
        self.handle(False, 0.112, "bounce-up", (40, 0))
        self.handle(True, 0.500, "down2", (80, 0))
        self.handle(False, 0.600, "up2", (81, 0))
        self.assertTrue(self.filter._held_stationary.get(Button.LEFT), "measured from down2, not the bounce")

    def test_motion_tap_is_wanted_only_while_it_matters(self) -> None:
        calls = []
        self.filter._set_motion_tap = calls.append
        self.handle(True, 0.0, "down", (0, 0))
        self.assertEqual(calls[-1], False)
        self.handle(False, 0.08, "up", (0, 0))
        self.assertEqual(calls[-1], True, "a release is held in place")
        self.filter._motion("m1", (9, 0))
        self.assertEqual(calls[-1], True, "the up and the motion are still on their way")
        self.filter._injected_passed(Button.LEFT)
        self.filter._injected_passed(Button.LEFT)
        self.assertEqual(calls[-1], False)

    def test_moving_hold_does_not_want_the_motion_tap(self) -> None:
        calls = []
        self.filter._set_motion_tap = calls.append
        self.handle(True, 0.0, "down", (0, 0))
        self.handle(False, 0.08, "up", (30, 0))
        self.assertEqual(calls[-1], False)

    def test_closing_hold_does_not_want_the_motion_tap(self) -> None:
        calls = []
        self.filter._set_motion_tap = calls.append
        self.handle(True, 0.0, "down", (0, 0))
        self.handle(False, 0.004, "flicker", (0, 0))
        self.assertEqual(calls[-1], False)

    def test_a_release_with_no_known_location_is_left_to_the_timer(self) -> None:
        # Windows passes no location yet.
        self.filter._handle(Button.LEFT, True, 0.0, "down")
        self.filter._handle(Button.LEFT, False, 0.08, "up")
        self.assertTrue(self.filter._motion("m", (50, 0)))
        self.assertEqual(self.injected, [])
        self.fire_all()
        self.assertEqual(self.injected, [(False, "up")])


class QueuedReleaseTests(unittest.TestCase):
    """A held release that comes due while real events wait behind a re-sent
    one goes out after them, so apps never see it before its own press."""

    def setUp(self) -> None:
        self.timers = FakeTimer.reset()
        patch = mock.patch("app.platform.threading.Timer", FakeTimer)
        patch.start()
        self.addCleanup(patch.stop)
        self.sent = []
        self.filter = GlobalClickFilter(40, [Button.LEFT])
        self.filter._use_os_time = True
        self.filter._inject = lambda button, pressed, template: self.sent.append((pressed, template))

    def queue_a_press(self) -> None:
        self.filter._handle(Button.LEFT, True, 1.000, "down1", location=(0, 0))
        self.filter._handle(Button.LEFT, False, 1.100, "up1", location=(0, 0))
        self.timers[0].fire()                                         # up1 re-sent, still on its way
        self.assertTrue(self.filter._handle(Button.LEFT, True, 1.150, "down2", location=(0, 0)).deferred)
        self.assertTrue(self.filter._handle(Button.LEFT, False, 1.200, "up2", location=(0, 0)).held)

    def test_stopping_sends_the_queued_press_before_the_release(self) -> None:
        self.queue_a_press()
        self.filter.stop()
        self.assertEqual(self.sent, [(False, "up1"), (True, "down2"), (False, "up2")])

    def test_the_timer_queues_the_release_behind_the_press(self) -> None:
        self.queue_a_press()
        self.timers[-1].fire()                                        # up2's window ends
        self.assertEqual(self.sent, [(False, "up1")])
        self.filter._injected_passed(Button.LEFT)                     # up1 delivered
        self.assertEqual(self.sent, [(False, "up1"), (True, "down2"), (False, "up2")])

    def test_motion_queues_the_release_behind_the_press(self) -> None:
        self.queue_a_press()
        self.assertFalse(self.filter._motion("m", (10, 0)))
        self.assertEqual(self.sent, [(False, "up1")])
        self.filter._injected_passed(Button.LEFT)
        self.assertEqual(self.sent, [(False, "up1"), (True, "down2"), (False, "up2"), (None, "m")])

    def test_a_late_press_queues_behind_the_press_already_waiting(self) -> None:
        self.queue_a_press()
        # The next press comes after up2's window, before its timer ran.
        event = self.filter._handle(Button.LEFT, True, 1.300, "down3", location=(0, 0))
        self.assertTrue(event.flush_held)
        self.assertEqual(self.sent, [(False, "up1")])
        self.filter._injected_passed(Button.LEFT)
        self.assertEqual(self.sent, [(False, "up1"), (True, "down2"), (False, "up2"), (True, "down3")])


class TapBreakerTests(unittest.TestCase):
    """macOS disabling the tap over and over stops the filter, failing open."""

    def test_the_third_disable_within_the_window_gives_up(self) -> None:
        click_filter = GlobalClickFilter(60, [Button.LEFT])
        self.assertFalse(click_filter._tap_disabled(100.0))
        self.assertFalse(click_filter._tap_disabled(110.0))
        self.assertTrue(click_filter._tap_disabled(120.0))

    def test_disables_spread_out_keep_being_rearmed(self) -> None:
        click_filter = GlobalClickFilter(60, [Button.LEFT])
        for moment in (0.0, 20.0, 40.0, 60.0, 80.0, 100.0):
            self.assertFalse(click_filter._tap_disabled(moment))


class MacTimestampTests(unittest.TestCase):
    """Hardware events carry mach ticks, posted ones nanoseconds."""

    def setUp(self) -> None:
        from app.platform import _mach_timebase

        patch = mock.patch("app.platform._timebase_ratio", return_value=(125, 3))  # Apple silicon
        patch.start()
        self.addCleanup(patch.stop)
        self.to_seconds = _mach_timebase()
        self.now = 50_000.0

    def ticks(self, seconds: float) -> int:
        return round(seconds * 1e9 * 3 / 125)

    def ns(self, seconds: float) -> int:
        return round(seconds * 1e9)

    def test_both_units_land_on_the_monotonic_clock(self) -> None:
        self.assertAlmostEqual(self.to_seconds(self.ticks(self.now - 0.01), self.now), self.now - 0.01, places=6)
        self.assertAlmostEqual(self.to_seconds(self.ns(self.now - 0.01), self.now), self.now - 0.01, places=6)

    def test_mixed_streams_measure_true_gaps(self) -> None:
        stamps = [self.ticks(self.now - 0.300), self.ns(self.now - 0.250), self.ticks(self.now - 0.240)]
        seconds = [self.to_seconds(stamp, self.now) for stamp in stamps]
        self.assertAlmostEqual((seconds[1] - seconds[0]) * 1000, 50, places=3)
        self.assertAlmostEqual((seconds[2] - seconds[1]) * 1000, 10, places=3)

    def test_unusable_stamps_are_timed_on_arrival(self) -> None:
        self.assertEqual(self.to_seconds(0, self.now), self.now)
        self.assertEqual(self.to_seconds(self.ns(self.now - 30.0), self.now), self.now)

    def test_a_posted_click_first_does_not_squeeze_later_real_ones(self) -> None:
        # A software click (ns) is the first thing the filter sees; two real
        # clicks (ticks) follow 300 ms apart and must not look like bounce.
        click_filter = GlobalClickFilter(60, [Button.LEFT])
        click_filter._use_os_time = True  # as _run_macos sets it

        def at(seconds: float, stamp: int) -> float:
            return self.to_seconds(stamp, self.now + seconds)

        with mock.patch("app.platform.threading.Timer", FakeTimer):
            click_filter._handle(Button.LEFT, True, at(0.0, self.ns(self.now)))
            click_filter._handle(Button.LEFT, False, at(0.05, self.ns(self.now + 0.05)), allow_hold=False)
            click_filter._handle(Button.LEFT, True, at(1.0, self.ticks(self.now + 1.0)))
            click_filter._handle(Button.LEFT, False, at(1.08, self.ticks(self.now + 1.08)), allow_hold=False)
            press = click_filter._handle(Button.LEFT, True, at(1.38, self.ticks(self.now + 1.38)))
        self.assertTrue(press.accepted)
        self.assertAlmostEqual(press.gap_ms or 0, 300, places=2)

    def test_intel_ticks_are_nanoseconds(self) -> None:
        from app.platform import _mach_timebase

        with mock.patch("app.platform._timebase_ratio", return_value=(1, 1)):
            to_seconds = _mach_timebase()
        self.assertEqual(to_seconds(self.ns(self.now - 0.5), self.now), self.now - 0.5)


class FakeCGEvent:
    """A CGEvent as the fake Quartz below hands it around."""

    def __init__(self, kind: int, x: float = 0.0, y: float = 0.0, timestamp: int = 0, fields=None) -> None:
        self.kind = kind
        self.location = SimpleNamespace(x=float(x), y=float(y))
        self.timestamp = timestamp
        self.fields = dict(fields or {})

    def copy(self) -> "FakeCGEvent":
        return FakeCGEvent(self.kind, self.location.x, self.location.y, self.timestamp, self.fields)


class FakeQuartz:
    """Just enough of Quartz for GlobalClickFilter._run_macos. Taps keep their
    callbacks so a test can feed them events; posted events are recorded,
    never sent anywhere."""

    kCGEventLeftMouseDown, kCGEventLeftMouseUp = 1, 2
    kCGEventRightMouseDown, kCGEventRightMouseUp = 3, 4
    kCGEventMouseMoved, kCGEventLeftMouseDragged, kCGEventRightMouseDragged = 5, 6, 7
    kCGEventOtherMouseDown, kCGEventOtherMouseUp, kCGEventOtherMouseDragged = 25, 26, 27
    kCGEventTapDisabledByTimeout, kCGEventTapDisabledByUserInput = 0xFFFFFFFE, 0xFFFFFFFF
    kCGMouseEventClickState, kCGMouseEventButtonNumber = 1, 3
    kCGEventSourceUserData, kCGEventSourceStateID = 42, 45
    kCGEventSourceStateHIDSystemState = 1
    kCGHIDEventTap = kCGHeadInsertEventTap = kCGEventTapOptionDefault = kCGMouseButtonLeft = 0
    kCFRunLoopCommonModes, kCFRunLoopDefaultMode = "common", "default"

    def __init__(self) -> None:
        self.taps = []
        self.posted = []
        self.pointer = (0.0, 0.0)
        self._wake = threading.Event()

    def CGEventMaskBit(self, kind):
        return 1 << kind

    def CGEventTapCreate(self, where, place, options, mask, callback, refcon):
        tap = SimpleNamespace(mask=mask, callback=callback, enabled=False, invalidated=False)
        self.taps.append(tap)
        return tap

    def CGEventTapEnable(self, tap, enabled):
        tap.enabled = bool(enabled)

    def CGEventTapIsEnabled(self, tap):
        return tap.enabled

    def CFMachPortInvalidate(self, tap):
        tap.invalidated = True

    def CFMachPortCreateRunLoopSource(self, allocator, tap, order):
        return ("source", id(tap))

    def CFRunLoopGetCurrent(self):
        return "run loop"

    def CFRunLoopAddSource(self, loop, source, mode):
        pass

    def CFRunLoopRemoveSource(self, loop, source, mode):
        pass

    def CFRunLoopRunInMode(self, mode, seconds, return_after_source):
        self._wake.wait(0.002)
        self._wake.clear()

    def CFRunLoopStop(self, loop):
        self._wake.set()

    def CGEventGetIntegerValueField(self, event, field):
        return event.fields.get(field, 0)

    def CGEventSetIntegerValueField(self, event, field, value):
        event.fields[field] = value

    def CGEventGetLocation(self, event):
        return event.location

    def CGEventGetTimestamp(self, event):
        return event.timestamp

    def CGEventCreateCopy(self, event):
        return event.copy()

    def CGEventGetType(self, event):
        return event.kind

    def CGEventSetType(self, event, kind):
        event.kind = kind

    def CGEventPost(self, where, event):
        self.posted.append(event.copy())

    def CGEventCreate(self, source):
        return FakeCGEvent(0, *self.pointer)

    def CGEventCreateMouseEvent(self, source, kind, point, button):
        return FakeCGEvent(kind, point.x, point.y)


class MacTapTests(unittest.TestCase):
    """The macOS tap callbacks, run against a fake Quartz: no real tap is
    installed and nothing is posted to the system."""

    Q = FakeQuartz

    def setUp(self) -> None:
        self.quartz = FakeQuartz()
        self.timers = FakeTimer.reset()
        for patch in (
            mock.patch.dict(sys.modules, {"Quartz": self.quartz}),
            mock.patch("app.platform.platform.system", return_value="Darwin"),
            mock.patch("app.platform.threading.Timer", FakeTimer),
            mock.patch("app.platform._timebase_ratio", return_value=(125, 3)),
            mock.patch("app.platform._double_click_interval", return_value=0.5),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.events = []
        self.errors = []
        self.filter = GlobalClickFilter(60, [Button.LEFT], on_event=self.events.append, on_error=self.errors.append)
        self.filter.start()
        self.addCleanup(self.filter.stop)  # runs before the patches are undone
        self.main_tap, self.motion_tap = self.quartz.taps
        # Event times, as offsets in seconds from here, always near now.
        self.base = monotonic() - 0.5

    def ticks(self, offset: float) -> int:
        return round((self.base + offset) * 1e9 * 3 / 125)

    def button(self, kind, at, offset, state=1, ns=False):
        """Feed one hardware button event to the main tap."""
        stamp = round((self.base + offset) * 1e9) if ns else self.ticks(offset)
        event = FakeCGEvent(kind, *at, timestamp=stamp, fields={
            self.Q.kCGEventSourceStateID: self.Q.kCGEventSourceStateHIDSystemState,
            self.Q.kCGMouseEventClickState: state,
        })
        return self.main_tap.callback(None, kind, event, None)

    def motion(self, at, mark=0):
        event = FakeCGEvent(self.Q.kCGEventMouseMoved, *at, fields={self.Q.kCGEventSourceUserData: mark})
        return self.motion_tap.callback(None, event.kind, event, None)

    def pass_back(self, posted):
        """A posted event comes back through the tap that watches its kind."""
        tap = self.motion_tap if posted.kind == self.Q.kCGEventMouseMoved else self.main_tap
        return tap.callback(None, posted.kind, posted, None)

    def mark(self, event):
        return event.fields.get(self.Q.kCGEventSourceUserData)

    def test_a_drag_let_go_while_moving_puts_the_pointer_back(self) -> None:
        self.assertIsNotNone(self.button(self.Q.kCGEventLeftMouseDown, (0, 0), 0.0))
        self.assertIsNone(self.button(self.Q.kCGEventLeftMouseUp, (50, 0), 0.5), "held back")
        self.quartz.pointer = (90.0, 0.0)  # the hand carried on
        self.timers[-1].fire()
        release, restore = self.quartz.posted
        self.assertEqual((release.kind, release.location.x, self.mark(release)), (self.Q.kCGEventLeftMouseUp, 50, INJECTED_MARK))
        self.assertEqual((restore.kind, restore.location.x, self.mark(restore)), (self.Q.kCGEventMouseMoved, 90, RESTORE_MARK))
        self.assertIs(self.pass_back(release), release)
        self.assertIs(self.pass_back(restore), restore, "the restore passes untouched")
        self.assertEqual(self.filter._in_flight[Button.LEFT], 0, "the restore is not waited for")

    def test_a_release_made_in_place_posts_no_restore(self) -> None:
        self.button(self.Q.kCGEventLeftMouseDown, (10, 10), 0.0)
        self.button(self.Q.kCGEventLeftMouseUp, (10, 10), 0.1)
        self.quartz.pointer = (11.0, 10.0)
        self.timers[-1].fire()
        self.assertEqual([event.kind for event in self.quartz.posted], [self.Q.kCGEventLeftMouseUp])

    def test_the_motion_tap_judges_motion_by_where_it_went(self) -> None:
        self.button(self.Q.kCGEventLeftMouseDown, (100, 100), 0.0)
        self.assertIsNone(self.button(self.Q.kCGEventLeftMouseUp, (100, 100), 0.1))
        self.assertTrue(self.motion_tap.enabled, "a release is held in place")
        self.quartz.pointer = (100.0, 100.0)
        self.assertIsNotNone(self.motion((101, 100)), "a nudge passes")
        self.assertEqual(self.quartz.posted, [])
        self.assertIsNone(self.motion((106, 100)), "leaving the click waits behind its release")
        self.assertEqual([self.mark(event) for event in self.quartz.posted], [INJECTED_MARK])
        self.pass_back(self.quartz.posted[0])
        moved = self.quartz.posted[-1]
        self.assertEqual((moved.location.x, self.mark(moved)), (106, MOTION_MARK_FOR[Button.LEFT]))

    def test_restore_motion_never_settles_a_held_release(self) -> None:
        self.button(self.Q.kCGEventLeftMouseDown, (100, 100), 0.0)
        self.button(self.Q.kCGEventLeftMouseUp, (100, 100), 0.1)
        self.assertIsNotNone(self.motion((300, 100), mark=RESTORE_MARK))
        self.assertEqual(self.quartz.posted, [])
        self.assertTrue(self.filter._filters[Button.LEFT].holding_release)

    def test_a_posted_click_first_does_not_squeeze_later_real_ones(self) -> None:
        self.button(self.Q.kCGEventLeftMouseDown, (0, 0), 0.00, ns=True)
        self.button(self.Q.kCGEventLeftMouseUp, (0, 0), 0.05, ns=True)
        self.button(self.Q.kCGEventLeftMouseDown, (0, 0), 0.50)
        self.button(self.Q.kCGEventLeftMouseUp, (0, 0), 0.58)
        self.button(self.Q.kCGEventLeftMouseDown, (0, 0), 0.88)
        press = self.events[-1]
        self.assertFalse(press.is_bounce)
        self.assertAlmostEqual(press.gap_ms or 0, 300, delta=0.01)

    def test_the_double_click_interval_is_read_before_the_tap_goes_live(self) -> None:
        from app import platform as platform_module

        self.assertEqual(platform_module._double_click_interval.call_count, 1)

    def test_the_click_count_ignores_a_suppressed_bounce(self) -> None:
        # Release chatter at 105 ms keeps macOS's chain going; the next
        # click, 560 ms after the first, arrives numbered 3.
        self.button(self.Q.kCGEventLeftMouseDown, (0, 0), 0.000, state=1)
        self.button(self.Q.kCGEventLeftMouseUp, (0, 0), 0.080, state=1)
        self.button(self.Q.kCGEventLeftMouseDown, (0, 0), 0.105, state=2)
        self.button(self.Q.kCGEventLeftMouseUp, (0, 0), 0.108, state=2)
        self.button(self.Q.kCGEventLeftMouseDown, (0, 0), 0.560, state=3)
        press = [event for event in self.quartz.posted if event.kind == self.Q.kCGEventLeftMouseDown][-1]
        self.assertEqual(press.fields[self.Q.kCGMouseEventClickState], 1)

    def test_stopping_invalidates_both_taps(self) -> None:
        self.filter.stop()
        self.assertFalse(self.filter.running)
        for tap in (self.main_tap, self.motion_tap):
            self.assertTrue(tap.invalidated, "a disabled tap stays registered until invalidated")
            self.assertFalse(tap.enabled)
        self.assertFalse(self.filter.tap_alive())

    def test_tap_alive_follows_the_main_tap(self) -> None:
        self.assertTrue(self.filter.tap_alive())
        self.main_tap.enabled = False  # macOS disabled it
        self.assertFalse(self.filter.tap_alive())

    def disable(self, tap) -> object:
        tap.enabled = False
        return tap.callback(None, self.Q.kCGEventTapDisabledByTimeout, None, None)

    def test_a_disabled_tap_is_rearmed(self) -> None:
        self.disable(self.main_tap)
        self.assertTrue(self.main_tap.enabled)
        self.assertEqual(self.filter.tap_resets, 1)
        self.assertTrue(self.filter.running)

    def test_macos_disabling_the_tap_again_and_again_stops_the_filter(self) -> None:
        self.button(self.Q.kCGEventLeftMouseDown, (0, 0), 0.0)
        self.button(self.Q.kCGEventLeftMouseUp, (30, 0), 0.4)          # held when it happens
        self.disable(self.main_tap)
        self.disable(self.main_tap)
        self.assertEqual(self.errors, [])
        self.disable(self.main_tap)
        self.filter._thread.join(2)
        self.assertFalse(self.filter.running)
        self.assertEqual(self.errors, [TAP_DISABLED_MESSAGE])
        self.assertFalse(self.main_tap.enabled, "not re-armed a third time")
        self.assertTrue(self.main_tap.invalidated and self.motion_tap.invalidated)
        released = [event for event in self.quartz.posted if event.kind == self.Q.kCGEventLeftMouseUp]
        self.assertEqual(len(released), 1, "the held release still reaches apps")

    def test_the_hook_logs_its_rearms_when_it_ends(self) -> None:
        self.disable(self.main_tap)
        with self.assertLogs("app.platform", "INFO") as logged:
            self.filter.stop()
        self.assertIn("tap resets 1, hook re-arms 0", "\n".join(logged.output))


if __name__ == "__main__":
    unittest.main()
