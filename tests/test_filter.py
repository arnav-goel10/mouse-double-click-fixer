"""Tests for the platform-facing filter that do not need a real mouse."""

import platform
import unittest
from time import monotonic
from unittest import mock

from app.core import Button
from app.platform import GlobalClickFilter, HookError, is_supported


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

    def test_a_deferred_press_is_not_counted_as_suppressed(self) -> None:
        from app.core import ClickEvent
        from app.platform import ClickCountRepair

        repair = ClickCountRepair()
        repair.correct(Button.LEFT, True, 1)
        # Held back behind a re-sent release, then delivered: apps get it.
        repair.record(Button.LEFT, ClickEvent(Button.LEFT, True, False, None, None, deferred=True))
        self.assertEqual(repair.correct(Button.LEFT, True, 2), 2)

    def test_a_reordered_press_still_counts(self) -> None:
        seen = self.run_chain([
            (True, 1, True, False), (False, 1, False, False),    # held release
            (True, 2, False, True),                              # flushed: apps get it
        ])
        self.assertEqual(seen, [(True, 1), (True, 2)])


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


class MacTimestampTests(unittest.TestCase):
    def test_event_timestamps_are_nanoseconds(self) -> None:
        from app.platform import _mach_timebase

        self.assertEqual(_mach_timebase()(1_500_000_000), 1.5)


if __name__ == "__main__":
    unittest.main()
