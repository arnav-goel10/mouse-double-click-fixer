"""Tests for the platform-facing filter that do not need a real mouse."""

try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

import math
import platform
import sys
import threading
import unittest
from time import monotonic
from types import SimpleNamespace
from typing import Optional
from unittest import mock

from app.core import Button, ClickEvent
from app.platform import (
    DEFAULT_DOUBLE_CLICK_S,
    DOUBLE_CLICK_KEY,
    INJECTED_MARK,
    MOTION_MARK_FOR,
    RESTORE_MARK,
    TAP_DISABLE_LIMIT,
    TAP_DISABLED_MESSAGE,
    ClickCountRepair,
    GlobalClickFilter,
    HookError,
    _double_click_interval,
    is_supported,
    windows_event_time,
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

    @unittest.skipUnless(platform.system() == "Darwin", "macOS event tap")
    def test_macos_taps_are_released_on_stop(self) -> None:
        import os

        import Quartz

        def taps_of_this_process() -> int:
            error, taps, count = Quartz.CGGetEventTapList(64, None, None)
            self.assertEqual(error, 0)
            return sum(1 for tap in (taps or [])[:count] if tap.tappingProcess == os.getpid())

        before = taps_of_this_process()
        for _ in range(10):
            # No buttons: nothing is held back or re-sent; the tap only watches.
            click_filter = GlobalClickFilter(60, [])
            try:
                click_filter.start()
            except HookError as error:
                self.skipTest(f"macOS refused the event tap here: {error}")
            try:
                self.assertEqual(taps_of_this_process(), before + 1, "one tap sees clicks and motion")
            finally:
                click_filter.stop()
        self.assertEqual(taps_of_this_process(), before, "a stopped filter leaves no tap registered")


class FakeTimer:
    """Stands in for threading.Timer: records each timer instead of starting
    it, so a test decides exactly when it fires instead of racing the clock."""

    created: list = []
    daemon = True

    def __init__(self, interval, function, args=()):
        self.interval, self.function, self.args = interval, function, args
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


class EventTimeTests(unittest.TestCase):
    """A held release is settled by the events' own timestamps. Events reach
    the hook late, by uneven amounts, but in the order they happened, so the
    first event stamped past the window settles the release, however late
    it arrives. The timer is only for a release with nothing after it, and
    waits out this machine's lateness on top of the window."""

    WINDOW = 0.040

    def setUp(self) -> None:
        self.timers = FakeTimer.reset()
        # monotonic(), as the hook reads it when an event arrives.
        self.clock = [100.0]
        for patch in (
            mock.patch("app.platform.threading.Timer", FakeTimer),
            mock.patch("app.platform.monotonic", lambda: self.clock[0]),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.filter, self.sent = self.make_filter()

    def make_filter(self):
        sent = []
        click_filter = GlobalClickFilter(40, [Button.LEFT, Button.RIGHT])
        click_filter._use_os_time = True
        click_filter._inject = lambda button, pressed, template: sent.append((button, pressed, template))
        return click_filter, sent

    def handle(self, button, pressed, stamp, name, late_ms=1.0, at=None, allow_hold=True):
        """An event that happened at `stamp` and reaches the hook `late_ms` later."""
        self.clock[0] = stamp + late_ms / 1000
        return self.filter._handle(button, pressed, stamp, name, allow_hold=allow_hold, location=at)

    def move(self, stamp, name, at, late_ms=1.0) -> bool:
        self.clock[0] = stamp + late_ms / 1000
        return self.filter._motion(name, at, stamp)

    def busy(self, late_ms: float, count: int = 64) -> None:
        """Earlier clicks of a button nobody filters, each reaching the hook
        `late_ms` after it happened: what the allowance is learnt from."""
        for index in range(count):
            self.handle(Button.MIDDLE, index % 2 == 0, 50.0 + index * 0.2, "busy", late_ms=late_ms)

    def releases(self) -> list:
        return [entry for entry in self.sent if entry[1] is False]

    def test_a_comeback_press_that_arrives_late_still_cancels(self) -> None:
        # A busy machine: events reach the hook 90 ms after they happen.
        self.busy(late_ms=90)
        self.handle(Button.LEFT, True, 100.000, "down")
        self.handle(Button.LEFT, False, 100.300, "drop", late_ms=10)
        timer = self.timers[-1]
        due_at = self.clock[0] + timer.interval
        self.assertAlmostEqual(due_at, 100.300 + self.WINDOW + 0.090, places=6)
        # Motion inside the window, delivered late: it passes and settles nothing.
        self.assertTrue(self.move(100.320, "m", (5, 0), late_ms=60))
        # The contact comes back 25 ms into the window, and that press reaches
        # the hook 80 ms late: after a timer counting the window from the
        # release's arrival would have delivered the release.
        back = self.handle(Button.LEFT, True, 100.325, "back", late_ms=80)
        self.assertGreater(self.clock[0], 100.310 + self.WINDOW)
        self.assertLess(self.clock[0], due_at)
        self.assertTrue(back.cancels_held, "the drag carries on")
        timer.fire()
        self.assertEqual(self.sent, [])
        self.handle(Button.LEFT, False, 101.000, "lift")
        self.timers[-1].fire()
        self.assertEqual(self.sent, [(Button.LEFT, False, "lift")])

    def test_motion_stamped_past_the_window_settles_the_release_ahead_of_itself(self) -> None:
        self.handle(Button.LEFT, True, 100.000, "down", at=(0, 0))
        self.handle(Button.LEFT, False, 100.500, "up", at=(50, 0))   # a drag let go while moving
        self.assertTrue(self.move(100.520, "m1", (60, 0)), "inside the window: it passes")
        self.assertEqual(self.sent, [])
        self.assertFalse(self.move(100.541, "m2", (70, 0)), "past it: it waits behind the release")
        self.assertEqual(self.sent, [(Button.LEFT, False, "up")])
        self.filter._injected_passed(Button.LEFT)                     # the up comes back
        self.assertEqual(self.sent[-1], (Button.LEFT, None, "m2"))
        self.filter._injected_passed(Button.LEFT)                     # and the motion
        self.assertTrue(self.move(100.550, "m3", (80, 0)), "nothing left in flight")
        for timer in list(self.timers):
            timer.fire()
        self.assertEqual(self.releases(), [(Button.LEFT, False, "up")], "the timer must not send it again")

    def test_late_motion_stamped_inside_the_window_never_settles(self) -> None:
        self.handle(Button.LEFT, True, 100.000, "down", at=(0, 0))
        self.handle(Button.LEFT, False, 100.500, "drop", at=(50, 0))
        self.assertTrue(self.move(100.536, "m", (90, 0), late_ms=170))
        self.assertTrue(self.handle(Button.LEFT, True, 100.538, "back", late_ms=171).cancels_held)
        self.assertEqual(self.sent, [])

    def test_a_press_of_another_button_settles_it_and_goes_out_behind_it(self) -> None:
        self.handle(Button.LEFT, True, 100.000, "down")
        self.handle(Button.LEFT, False, 100.100, "up")
        right = self.handle(Button.RIGHT, True, 100.200, "rdown")
        self.assertFalse(right.accepted)
        self.assertTrue(right.deferred)
        self.assertFalse(right.is_bounce)
        self.assertEqual(self.sent, [(Button.LEFT, False, "up"), (Button.RIGHT, True, "rdown")])
        self.assertEqual((self.filter._in_flight[Button.LEFT], self.filter._in_flight[Button.RIGHT]), (1, 1))
        self.filter._injected_passed(Button.LEFT)
        self.filter._injected_passed(Button.RIGHT)
        self.assertTrue(self.handle(Button.RIGHT, False, 100.300, "rup").held)
        for timer in list(self.timers):
            timer.fire()
        self.assertEqual(self.releases(), [(Button.LEFT, False, "up"), (Button.RIGHT, False, "rup")])

    def test_a_press_of_another_button_inside_the_window_leaves_the_hold(self) -> None:
        self.handle(Button.LEFT, True, 100.000, "down")
        self.handle(Button.LEFT, False, 100.100, "drop")
        self.assertTrue(self.handle(Button.RIGHT, True, 100.130, "rdown").accepted)
        self.assertTrue(self.handle(Button.LEFT, True, 100.135, "back").cancels_held)
        self.assertEqual(self.sent, [])

    def test_releases_due_together_go_out_oldest_first(self) -> None:
        self.handle(Button.LEFT, True, 100.000, "ldown", at=(0, 0))
        self.handle(Button.RIGHT, True, 100.010, "rdown", at=(0, 0))
        self.handle(Button.RIGHT, False, 100.100, "rup", at=(0, 0))
        self.handle(Button.LEFT, False, 100.110, "lup", at=(0, 0))
        self.assertFalse(self.move(100.200, "m", (1, 0)))
        self.assertEqual(self.sent, [(Button.RIGHT, False, "rup"), (Button.LEFT, False, "lup")])

    def test_a_settled_release_waiting_in_its_queue_keeps_the_event_behind_it(self) -> None:
        self.handle(Button.LEFT, True, 100.000, "down1")
        self.handle(Button.LEFT, False, 100.100, "up1")
        self.timers[-1].fire()                                        # up1 re-sent, still on its way
        self.assertTrue(self.handle(Button.LEFT, True, 100.150, "down2").deferred)
        self.assertTrue(self.handle(Button.LEFT, False, 100.200, "up2").held)
        # A right press past up2's window: up2 must follow down2, already
        # waiting, and the right press must follow up2.
        right = self.handle(Button.RIGHT, True, 100.250, "rdown")
        self.assertTrue(right.deferred)
        self.assertEqual(self.sent, [(Button.LEFT, False, "up1")])
        self.filter._injected_passed(Button.LEFT)                     # up1 delivered
        self.assertEqual(self.sent, [
            (Button.LEFT, False, "up1"),
            (Button.LEFT, True, "down2"),
            (Button.LEFT, False, "up2"),
            (Button.RIGHT, True, "rdown"),
        ])
        self.assertEqual((self.filter._in_flight[Button.LEFT], self.filter._in_flight[Button.RIGHT]), (2, 1))

    def test_a_buttons_later_events_follow_one_parked_in_another_queue(self) -> None:
        # rdown settles up2, which waits behind down2 in the left queue, so
        # rdown waits there too. rup must not overtake it, filtered or not,
        # or apps would see the right button go up before it went down.
        for active in ([Button.LEFT, Button.RIGHT], [Button.LEFT]):
            with self.subTest(active=active):
                self.timers.clear()
                self.filter, self.sent = self.make_filter()
                self.filter.update(buttons=active)
                self.handle(Button.LEFT, True, 100.000, "down1")
                self.handle(Button.LEFT, False, 100.100, "up1")
                self.timers[-1].fire()                                # up1 re-sent, still on its way
                self.assertTrue(self.handle(Button.LEFT, True, 100.150, "down2").deferred)
                self.assertTrue(self.handle(Button.LEFT, False, 100.200, "up2").held)
                self.assertTrue(self.handle(Button.RIGHT, True, 100.250, "rdown").deferred)
                rup = self.handle(Button.RIGHT, False, 100.300, "rup")
                if rup.held:
                    self.timers[-1].fire()                            # its window ends
                else:
                    self.assertTrue(rup.deferred)
                self.assertEqual(self.sent, [(Button.LEFT, False, "up1")])
                self.filter._injected_passed(Button.LEFT)             # up1 delivered
                self.assertEqual(self.sent, [
                    (Button.LEFT, False, "up1"),
                    (Button.LEFT, True, "down2"),
                    (Button.LEFT, False, "up2"),
                    (Button.RIGHT, True, "rdown"),
                    (Button.RIGHT, False, "rup"),
                ])

    def test_a_release_that_cannot_be_resent_is_never_held_behind_one(self) -> None:
        # Windows can't send input to a window running as administrator.
        self.handle(Button.LEFT, True, 100.000, "down")
        self.handle(Button.LEFT, False, 100.100, "up")
        self.handle(Button.RIGHT, True, 100.120, "rdown")
        rup = self.handle(Button.RIGHT, False, 100.200, "rup", allow_hold=False)
        self.assertTrue(rup.accepted)
        self.assertEqual(self.sent, [(Button.LEFT, False, "up")])

    def test_the_timer_settles_a_release_with_nothing_after_it(self) -> None:
        self.handle(Button.LEFT, True, 100.000, "down", late_ms=0)
        self.handle(Button.LEFT, False, 100.100, "up", late_ms=0)
        (timer,) = self.timers
        self.assertAlmostEqual(timer.interval, self.WINDOW + 0.005, places=9, msg="prompt events: 5 ms on top")
        self.assertEqual(self.sent, [])
        timer.fire()
        self.assertEqual(self.sent, [(Button.LEFT, False, "up")])

    def test_the_timer_waits_out_this_machines_lateness(self) -> None:
        for late_ms, allowance_ms in ((0.2, 5.0), (60.0, 60.0), (400.0, 150.0)):
            with self.subTest(late_ms=late_ms):
                self.timers.clear()
                self.filter, self.sent = self.make_filter()
                self.busy(late_ms=late_ms)
                self.handle(Button.LEFT, True, 100.000, "down", late_ms=0)
                self.handle(Button.LEFT, False, 100.100, "up", late_ms=0)
                self.assertAlmostEqual(self.timers[-1].interval, self.WINDOW + allowance_ms / 1000, places=9)

    def test_the_timer_counts_from_when_the_release_happened(self) -> None:
        self.busy(late_ms=100)
        self.handle(Button.LEFT, True, 100.000, "down", late_ms=20)
        self.handle(Button.LEFT, False, 100.100, "up", late_ms=20)
        # Due the window and 100 ms after the release, 20 ms of which had passed.
        self.assertAlmostEqual(self.timers[-1].interval, self.WINDOW + 0.100 - 0.020, places=9)

    def test_the_timer_never_fires_sooner_than_the_window_after_the_release_arrived(self) -> None:
        # A release out of a backlog: its comeback press would be right behind it.
        self.busy(late_ms=5, count=62)
        self.handle(Button.LEFT, True, 100.000, "down", late_ms=1)
        self.handle(Button.LEFT, False, 100.100, "up", late_ms=180)
        self.assertAlmostEqual(self.timers[-1].interval, self.WINDOW, places=9)

    def test_the_timer_does_nothing_after_an_event_settled_its_release(self) -> None:
        self.handle(Button.LEFT, True, 100.000, "down", at=(0, 0))
        self.handle(Button.LEFT, False, 100.100, "up", at=(30, 0))
        self.assertFalse(self.move(100.150, "m", (40, 0)))
        self.timers[0].fire()
        self.assertEqual(self.releases(), [(Button.LEFT, False, "up")])

    def test_an_event_does_nothing_after_the_timer_settled_its_release(self) -> None:
        self.handle(Button.LEFT, True, 100.000, "down", at=(0, 0))
        self.handle(Button.LEFT, False, 100.100, "up", at=(30, 0))
        self.timers[0].fire()
        self.assertFalse(self.move(100.150, "m", (40, 0)), "waits behind the release on its way")
        self.assertTrue(self.handle(Button.LEFT, True, 100.400, "down2").deferred)
        self.filter._injected_passed(Button.LEFT)
        self.assertEqual(
            self.sent, [(Button.LEFT, False, "up"), (Button.LEFT, None, "m"), (Button.LEFT, True, "down2")]
        )

    def test_a_real_race_between_the_timer_and_an_event_delivers_once(self) -> None:
        for _round in range(300):
            click_filter, sent = self.make_filter()
            click_filter._handle(Button.LEFT, True, 100.000, "down", location=(0, 0))
            click_filter._handle(Button.LEFT, False, 100.100, "up", location=(30, 0))
            held_id = click_filter._filters[Button.LEFT].held_id
            start = threading.Barrier(2)

            def timer_thread() -> None:
                start.wait()
                click_filter._commit_held(Button.LEFT, held_id)

            thread = threading.Thread(target=timer_thread)
            thread.start()
            start.wait()
            passes = click_filter._motion("m", (50, 0), 100.200)
            thread.join()
            self.assertEqual([entry for entry in sent if entry[1] is False], [(Button.LEFT, False, "up")])
            self.assertFalse(passes, "either way the motion waits behind the release")


def quantized_tick(ms: float) -> int:
    """GetTickCount at `ms` milliseconds since boot: it advances only with the
    64 Hz system timer, so it reads whole milliseconds, 15 or 16 apart."""
    return int(math.floor(math.floor(ms / 15.625) * 15.625))


class WindowsEventTimeTests(unittest.TestCase):
    """Windows stamps input with GetTickCount (15.6 ms steps). The hook times
    each event by its arrival on the precise clock instead, and the stamp
    only says how late the hook ran."""

    def test_a_prompt_event_is_timed_by_its_arrival(self) -> None:
        # Up to one tick between the stamp and the hook's reading is the tick
        # being coarse, not the hook being late.
        for late in (0, 1, 15, 16):
            self.assertEqual(windows_event_time(5.0, 1_000 + late, 1_000), 5.0)

    def test_a_late_hook_takes_its_lateness_off(self) -> None:
        self.assertAlmostEqual(windows_event_time(5.0, 1_045, 1_000), 5.0 - 0.029, places=9)

    def test_the_tick_count_wrapping_changes_nothing(self) -> None:
        # Stamped 10 ms before GetTickCount wrapped to 0, seen 30 ms after.
        self.assertAlmostEqual(windows_event_time(5.0, 0x1E, 0xFFFFFFF6), 5.0 - 0.024, places=9)
        self.assertEqual(windows_event_time(5.0, 0x05, 0xFFFFFFFB), 5.0)

    def test_a_stamp_after_the_hooks_reading_is_not_late(self) -> None:
        self.assertEqual(windows_event_time(5.0, 1_000, 1_004), 5.0)

    def test_an_absurd_lateness_is_capped(self) -> None:
        self.assertAlmostEqual(windows_event_time(5.0, 0x7FFFFFFF, 0), 3.0, places=9)

    def test_a_long_idle_is_just_a_long_gap(self) -> None:
        # 30 days without a click (time asleep counts): nothing is carried
        # from one event to the next, so the next click is simply late.
        click_filter = GlobalClickFilter(60, [Button.LEFT])
        click_filter._use_os_time = True
        click_filter._inject = lambda _button, _pressed, _template: False  # not waited for
        days = 30 * 24 * 3600
        tick = (days * 1000) & 0xFFFFFFFF
        with mock.patch("app.platform.threading.Timer", FakeTimer):
            click_filter._handle(Button.LEFT, True, windows_event_time(1.0, 1_000, 1_000), "down")
            click_filter._handle(Button.LEFT, False, windows_event_time(1.1, 1_100, 1_100), "up")
            FakeTimer.created[-1].fire()
            press = click_filter._handle(Button.LEFT, True, windows_event_time(1.0 + days, tick, tick), "down2")
        self.assertTrue(press.accepted)
        self.assertAlmostEqual(press.gap_ms, (days - 0.1) * 1000, places=0)


class WindowsClickTimingTests(unittest.TestCase):
    """The click timing the hook feeds the filter, end to end through
    GlobalClickFilter, with GetTickCount's coarse stamps."""

    BOOT_MS = 15_624.0  # 1 ms before a tick: a 3 ms flicker crosses into the next

    def setUp(self) -> None:
        self.timers = FakeTimer.reset()
        patch = mock.patch("app.platform.threading.Timer", FakeTimer)
        patch.start()
        self.addCleanup(patch.stop)
        self.injected = []

    def make_filter(self, threshold: int) -> GlobalClickFilter:
        click_filter = GlobalClickFilter(threshold, [Button.LEFT])
        click_filter._use_os_time = True
        click_filter._inject = lambda button, pressed, template: self.injected.append((pressed, template))
        return click_filter

    def stamp(self, happened_ms: float, seen_ms: Optional[float] = None) -> float:
        """What the hook passes the filter for an event that happened at
        `happened_ms` and reached the hook at `seen_ms`."""
        seen = happened_ms + 0.2 if seen_ms is None else seen_ms
        return windows_event_time(
            (self.BOOT_MS + seen) / 1000, quantized_tick(self.BOOT_MS + seen), quantized_tick(self.BOOT_MS + happened_ms)
        )

    def tick_stamp(self, happened_ms: float) -> float:
        """What the hook passed before 1.0: the tick itself."""
        return quantized_tick(self.BOOT_MS + happened_ms) / 1000

    def make_bounce_with_motion(self, stamp) -> None:
        # Press, a 3 ms flicker open as the contact closes, the hand already
        # moving, the contact closed again, the real lift much later.
        click_filter = self.make_filter(60)
        click_filter._handle(Button.LEFT, True, stamp(0), "down", location=(100, 100))
        click_filter._handle(Button.LEFT, False, stamp(3), "flicker", location=(100, 100))
        click_filter._motion("move", (140, 100))
        click_filter._handle(Button.LEFT, True, stamp(4), "back", location=(140, 100))
        click_filter._handle(Button.LEFT, False, stamp(800), "lift", location=(300, 100))
        for timer in list(self.timers):
            timer.fire()

    def test_a_make_bounce_keeps_the_drag(self) -> None:
        self.make_bounce_with_motion(self.stamp)
        self.assertNotIn((False, "flicker"), self.injected, "the drag became a 3 ms click")
        self.assertEqual([entry for entry in self.injected if entry[0] is False], [(False, "lift")])

    def test_tick_stamps_would_have_lost_it(self) -> None:
        # Why the clock changed: the flicker reads 15 ms, a finger letting go.
        self.make_bounce_with_motion(self.tick_stamp)
        self.assertIn((False, "flicker"), self.injected)

    def test_a_late_press_callback_still_blocks_a_bounce(self) -> None:
        for threshold, seen_ms in ((60, 130), (40, 155)):
            with self.subTest(threshold=threshold, late_ms=seen_ms - 110):
                self.timers.clear()
                click_filter = self.make_filter(threshold)
                click_filter._handle(Button.LEFT, True, self.stamp(0), "down")
                click_filter._handle(Button.LEFT, False, self.stamp(100), "up")
                self.timers[-1].fire()  # timers run while the hook's thread is stalled
                bounce = click_filter._handle(Button.LEFT, True, self.stamp(110, seen_ms), "bounce")
                self.assertTrue(bounce.is_bounce, f"gap read as {bounce.gap_ms:.1f} ms")

    def test_a_late_release_callback_keeps_a_double_click(self) -> None:
        click_filter = self.make_filter(60)
        click_filter._handle(Button.LEFT, True, self.stamp(0), "down")
        click_filter._handle(Button.LEFT, False, self.stamp(100, seen_ms=170), "up")
        second = click_filter._handle(Button.LEFT, True, self.stamp(220), "down2")
        self.assertTrue(second.flush_held, "the double-click was taken for a dropout")
        self.assertEqual(self.injected, [(False, "up"), (True, "down2")])


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


class DoubleClickIntervalTests(unittest.TestCase):
    """The interval is read from the preference macOS keeps it in, never
    through AppKit: it is read on the hook thread, and AppKit belongs to the
    main thread."""

    def read(self, stored):
        asked = []

        def copy_app_value(key, application):
            asked.append((key, application))
            return stored

        core_foundation = SimpleNamespace(
            CFPreferencesCopyAppValue=copy_app_value, kCFPreferencesAnyApplication="any application"
        )
        # None in sys.modules makes any import of AppKit fail.
        with mock.patch.dict(sys.modules, {"CoreFoundation": core_foundation, "AppKit": None}):
            return _double_click_interval(), asked

    def test_the_users_setting_is_read_from_the_global_preferences(self) -> None:
        seconds, asked = self.read(5.0)
        self.assertEqual(seconds, 5.0)
        self.assertEqual(asked, [("com.apple.mouse.doubleClickThreshold", "any application")])
        self.assertEqual(DOUBLE_CLICK_KEY, "com.apple.mouse.doubleClickThreshold")

    def test_an_unset_or_unusable_setting_means_the_default(self) -> None:
        for stored in (None, 0, -1.0, float("nan")):
            with self.subTest(stored=stored):
                self.assertEqual(self.read(stored)[0], DEFAULT_DOUBLE_CLICK_S)

    @unittest.skipUnless(platform.system() == "Darwin", "macOS preferences")
    def test_it_agrees_with_appkit(self) -> None:
        try:
            from AppKit import NSEvent
        except ImportError:
            self.skipTest("AppKit is not installed")
        self.assertAlmostEqual(_double_click_interval(), float(NSEvent.doubleClickInterval()), places=6)


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

    def watched(self) -> list:
        """Each time the filter says whether motion needs judging, as the
        Windows hook hears it (its switch) and as the macOS tap reads it
        (the flag): both must agree every time."""
        calls = []

        def switch(wanted: bool) -> None:
            self.assertEqual(self.filter._motion_wanted, wanted, "the flag and the switch agree")
            calls.append(wanted)

        self.filter._set_motion_tap = switch
        return calls

    def test_motion_is_judged_only_while_it_matters(self) -> None:
        calls = self.watched()
        self.handle(True, 0.0, "down", (0, 0))
        self.assertEqual(calls[-1], False)
        self.handle(False, 0.08, "up", (0, 0))
        self.assertEqual(calls[-1], True, "a release is held in place")
        self.filter._motion("m1", (9, 0))
        self.assertEqual(calls[-1], True, "the up and the motion are still on their way")
        self.filter._injected_passed(Button.LEFT)
        self.filter._injected_passed(Button.LEFT)
        self.assertEqual(calls[-1], False)

    def test_motion_in_a_moving_holds_window_is_judged_and_passes(self) -> None:
        # Judged, so that motion stamped past the window can settle the
        # release; inside the window it goes through and the hold stays.
        calls = self.watched()
        self.handle(True, 0.0, "down", (0, 0))
        self.handle(False, 0.08, "up", (30, 0))
        self.assertEqual(calls[-1], True)
        self.assertTrue(self.filter._motion("m", (60, 0), 0.10))
        self.assertEqual(self.injected, [])
        self.assertTrue(self.filter._filters[Button.LEFT].holding_release)

    def test_motion_in_a_closing_holds_window_is_judged_and_passes(self) -> None:
        calls = self.watched()
        self.handle(True, 0.0, "down", (0, 0))
        self.handle(False, 0.004, "flicker", (0, 0))
        self.assertEqual(calls[-1], True)
        self.assertTrue(self.filter._motion("m", (10, 0), 0.006))
        self.assertTrue(self.handle(True, 0.008, "back", (10, 0)).cancels_held, "the drag starts")
        self.assertEqual(calls[-1], False, "nothing pending any more")

    def test_motion_is_judged_before_a_release_is_resent(self) -> None:
        # The timer delivers a drag's release from its own thread. A move the
        # tap decides while the release is going out must already be judged,
        # so it waits behind the release instead of overtaking it.
        self.handle(True, 0.0, "down", (0, 0))
        self.handle(False, 0.08, "up", (30, 0))
        judged_as_sent = []
        self.filter._inject = lambda button, pressed, template: judged_as_sent.append(self.filter._motion_wanted)
        self.fire_all()
        self.assertEqual(judged_as_sent, [True])
        self.assertFalse(self.filter._motion("m", (40, 0)), "it waits behind the release")

    def test_a_release_with_no_known_location_is_left_to_the_timer(self) -> None:
        # Windows passes none where the pointer's place means nothing (a
        # hidden pointer, pen and touch, a remote session). Motion is never
        # held back there, not even motion stamped past the window.
        calls = self.watched()
        self.filter._handle(Button.LEFT, True, 0.0, "down")
        self.filter._handle(Button.LEFT, False, 0.08, "up")
        self.assertEqual(calls[-1], False, "motion is not judged for it")
        self.assertTrue(self.filter._motion("m", (50, 0)))
        self.assertTrue(self.filter._motion("m2", (60, 0), 0.5))
        self.assertEqual(self.injected, [])
        self.fire_all()
        self.assertEqual(self.injected, [(False, "up")])

    def test_a_press_still_settles_a_release_with_no_known_location(self) -> None:
        self.filter.update(buttons=[Button.LEFT, Button.RIGHT])
        self.filter._handle(Button.LEFT, True, 0.0, "down")
        self.filter._handle(Button.LEFT, False, 0.08, "up")
        self.assertTrue(self.filter._handle(Button.RIGHT, True, 0.2, "rdown").deferred)
        self.assertEqual(self.injected, [(False, "up"), (True, "rdown")])


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
        # Whether each tap was enabled as each event was posted.
        self.taps_enabled_at_post = []
        self.pointer = (0.0, 0.0)
        self.pointer_error = None  # raised when the pointer is read, if set
        self.refuse_taps = False  # as macOS does without the permission
        self._wake = threading.Event()

    def CGEventMaskBit(self, kind):
        return 1 << kind

    def CGEventTapCreate(self, where, place, options, mask, callback, refcon):
        if self.refuse_taps:
            return None
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
        self.taps_enabled_at_post.append([tap.enabled for tap in self.taps])

    def CGEventCreate(self, source):
        if self.pointer_error is not None:
            raise self.pointer_error
        return FakeCGEvent(0, *self.pointer)

    def CGEventCreateMouseEvent(self, source, kind, point, button):
        return FakeCGEvent(kind, point.x, point.y)


class UntouchableEvent:
    """An event nothing may look inside: any Quartz call on it fails."""

    def __getattr__(self, name):
        raise AssertionError(f"the event's {name} was read")


class MacTapTests(unittest.TestCase):
    """The macOS tap callback, run against a fake Quartz: no real tap is
    installed and nothing is posted to the system."""

    Q = FakeQuartz
    MOTION_KINDS = (
        FakeQuartz.kCGEventMouseMoved,
        FakeQuartz.kCGEventLeftMouseDragged,
        FakeQuartz.kCGEventRightMouseDragged,
        FakeQuartz.kCGEventOtherMouseDragged,
    )

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
        self.permitted = True
        # Per call of on_permission_lost: its thread, and how many events
        # had been posted by then.
        self.lost = []
        self.filter = GlobalClickFilter(
            60,
            [Button.LEFT],
            on_event=self.events.append,
            on_error=self.errors.append,
            permission_ok=lambda: self.permitted,
            on_permission_lost=lambda: self.lost.append((threading.current_thread().name, len(self.quartz.posted))),
        )
        self.filter.start()
        self.addCleanup(self.filter.stop)  # runs before the patches are undone
        (self.tap,) = self.quartz.taps
        # Event times, as offsets in seconds from here, always near now.
        self.base = monotonic() - 0.5
        # The offset of the latest button event made; motion defaults to
        # just after it.
        self.clock = 0.0

    def ticks(self, offset: float) -> int:
        return round((self.base + offset) * 1e9 * 3 / 125)

    def hid(self, kind, at, offset, state=1, ns=False) -> FakeCGEvent:
        """A hardware button event."""
        self.clock = offset
        stamp = round((self.base + offset) * 1e9) if ns else self.ticks(offset)
        return FakeCGEvent(kind, *at, timestamp=stamp, fields={
            self.Q.kCGEventSourceStateID: self.Q.kCGEventSourceStateHIDSystemState,
            self.Q.kCGMouseEventClickState: state,
        })

    def move(self, at, mark=0, kind=FakeQuartz.kCGEventMouseMoved, offset=None, hardware=True) -> FakeCGEvent:
        """Pointer motion, stamped `offset` (by default 1 ms after the
        latest button event); from the mouse, or else posted by an app."""
        offset = self.clock + 0.001 if offset is None else offset
        fields = {self.Q.kCGEventSourceUserData: mark}
        if hardware:
            fields[self.Q.kCGEventSourceStateID] = self.Q.kCGEventSourceStateHIDSystemState
        return FakeCGEvent(kind, *at, timestamp=self.ticks(offset), fields=fields)

    def button(self, kind, at, offset, state=1, ns=False):
        """Feed one hardware button event to the tap."""
        event = self.hid(kind, at, offset, state, ns)
        return self.tap.callback(None, kind, event, None)

    def motion(self, at, mark=0, offset=None):
        event = self.move(at, mark, offset=offset)
        return self.tap.callback(None, event.kind, event, None)

    def pass_back(self, posted):
        """A posted event comes back through the tap."""
        return self.tap.callback(None, posted.kind, posted, None)

    def stream(self, *events) -> list:
        """Run events through the tap as WindowServer does with one tap: one
        at a time, in order, each held until the callback answers. What the
        app posts joins the stream behind the events already in it. Returns
        what apps see, in order, as (kind, x)."""
        seen = []
        waiting = list(events)
        posted = len(self.quartz.posted)
        while waiting:
            event = waiting.pop(0)
            answer = self.tap.callback(None, event.kind, event, None)
            if answer is not None:
                seen.append((answer.kind, answer.location.x))
            waiting += self.quartz.posted[posted:]
            posted = len(self.quartz.posted)
        return seen

    def mark(self, event):
        return event.fields.get(self.Q.kCGEventSourceUserData)

    def test_one_tap_sees_clicks_and_motion(self) -> None:
        self.assertEqual(len(self.quartz.taps), 1)
        clicks = (
            self.Q.kCGEventLeftMouseDown, self.Q.kCGEventLeftMouseUp,
            self.Q.kCGEventRightMouseDown, self.Q.kCGEventRightMouseUp,
            self.Q.kCGEventOtherMouseDown, self.Q.kCGEventOtherMouseUp,
        )
        for kind in clicks + self.MOTION_KINDS:
            self.assertTrue(self.tap.mask & self.quartz.CGEventMaskBit(kind), f"event type {kind}")
        self.assertTrue(self.tap.enabled)

    def assert_motion_goes_straight_through(self) -> None:
        with mock.patch.object(self.filter, "_motion") as judged, fresh_error_log():
            with self.assertNoLogs("app.platform", "WARNING"):
                for kind in self.MOTION_KINDS:
                    event = UntouchableEvent()
                    self.assertIs(self.tap.callback(None, kind, event, None), event)
        judged.assert_not_called()

    def test_motion_with_nothing_pending_goes_straight_through(self) -> None:
        self.assertFalse(self.filter._motion_wanted)
        self.assert_motion_goes_straight_through()

    def test_motion_during_a_moving_hold_is_judged_and_passes(self) -> None:
        self.button(self.Q.kCGEventLeftMouseDown, (0, 0), 0.0)
        self.assertIsNone(self.button(self.Q.kCGEventLeftMouseUp, (50, 0), 0.5), "held: a drag let go")
        self.assertTrue(self.filter._motion_wanted, "motion stamped past the window settles it")
        with mock.patch.object(self.filter, "_motion", wraps=self.filter._motion) as judged:
            self.assertIsNotNone(self.motion((60, 0), offset=0.52), "inside the window: it passes")
        judged.assert_called_once()
        self.assertAlmostEqual(judged.call_args.args[2], self.base + 0.52, places=6)
        self.assertEqual(self.quartz.posted, [])

    def test_motion_once_a_click_is_over_goes_straight_through(self) -> None:
        self.button(self.Q.kCGEventLeftMouseDown, (100, 100), 0.0)
        self.button(self.Q.kCGEventLeftMouseUp, (100, 100), 0.1)
        self.quartz.pointer = (100.0, 100.0)
        self.stream(self.move((106, 100)))                            # the up, then the motion
        self.assertEqual(self.filter._in_flight[Button.LEFT], 0)
        self.assert_motion_goes_straight_through()

    def test_motion_during_a_hold_in_place_is_judged_and_queued(self) -> None:
        self.button(self.Q.kCGEventLeftMouseDown, (100, 100), 0.0)
        self.assertIsNone(self.button(self.Q.kCGEventLeftMouseUp, (100, 100), 0.1))
        self.assertTrue(self.filter._motion_wanted, "a release is held in place")
        self.quartz.pointer = (100.0, 100.0)
        with mock.patch.object(self.filter, "_motion", wraps=self.filter._motion) as judged:
            self.assertIsNone(self.motion((106, 100)), "queued behind the release it settled")
        judged.assert_called_once()
        self.assertEqual(judged.call_args.args[1], (106.0, 100.0))
        self.assertEqual([self.mark(event) for event in self.quartz.posted], [INJECTED_MARK])
        self.assertEqual([entry[0] for entry in self.filter._queued[Button.LEFT]], [None])

    def test_a_held_release_reaches_apps_before_the_motion_after_it(self) -> None:
        # The first moves after a click made in place: the one that leaves
        # the spot settles the release, and every move waits behind it.
        self.quartz.pointer = (100.0, 100.0)
        seen = self.stream(
            self.hid(self.Q.kCGEventLeftMouseDown, (100, 100), 0.00),
            self.hid(self.Q.kCGEventLeftMouseUp, (100, 100), 0.08),
            self.move((106, 100)),
            self.move((112, 100)),
            self.move((118, 100), kind=self.Q.kCGEventLeftMouseDragged),
        )
        moved, up = self.Q.kCGEventMouseMoved, self.Q.kCGEventLeftMouseUp
        self.assertEqual(
            [(event.kind, event.location.x) for event in self.quartz.posted],
            [(up, 100), (moved, 106), (moved, 112), (moved, 118)],
            "posted in order: the release, then the motion",
        )
        self.assertEqual(
            seen, [(self.Q.kCGEventLeftMouseDown, 100), (up, 100), (moved, 106), (moved, 112), (moved, 118)]
        )
        self.assertEqual(self.filter._in_flight[Button.LEFT], 0, "everything re-sent came back")
        self.assertFalse(self.filter._motion_wanted)
        for timer in list(self.timers):
            timer.fire()
        self.assertEqual(len(self.quartz.posted), 4, "the timers must not resend anything")

    def test_motion_stamped_past_the_window_delivers_a_drag_release_first(self) -> None:
        self.quartz.pointer = (80.0, 0.0)
        moved, up = self.Q.kCGEventMouseMoved, self.Q.kCGEventLeftMouseUp
        seen = self.stream(
            self.hid(self.Q.kCGEventLeftMouseDown, (0, 0), 0.0),
            self.move((20, 0), kind=self.Q.kCGEventLeftMouseDragged, offset=0.2),
            self.hid(up, (50, 0), 0.5),
            self.move((60, 0), offset=0.52),                          # inside the window
            self.move((80, 0), offset=0.561),                         # past it
        )
        self.assertEqual(
            [(event.kind, event.location.x, self.mark(event)) for event in self.quartz.posted],
            [(up, 50, INJECTED_MARK), (moved, 80, RESTORE_MARK), (moved, 80, MOTION_MARK_FOR[Button.LEFT])],
        )
        self.assertEqual(seen[2:], [(moved, 60), (up, 50), (moved, 80), (moved, 80)])
        for timer in list(self.timers):
            timer.fire()
        self.assertEqual(len(self.quartz.posted), 3, "the timer must not resend the release")
        self.assertFalse(self.filter._motion_wanted)

    def test_another_apps_motion_never_settles_a_release_by_its_time(self) -> None:
        # Stamped when it was posted, it can overtake the hardware's own
        # events, the press that would have cancelled the release among them.
        self.button(self.Q.kCGEventLeftMouseDown, (0, 0), 0.0)
        self.assertIsNone(self.button(self.Q.kCGEventLeftMouseUp, (50, 0), 0.5))
        posted = self.move((300, 0), offset=0.6, hardware=False)
        self.assertIs(self.tap.callback(None, posted.kind, posted, None), posted)
        self.assertEqual(self.quartz.posted, [])
        self.assertTrue(self.filter._filters[Button.LEFT].holding_release)
        self.assertIsNone(self.button(self.Q.kCGEventLeftMouseDown, (50, 0), 0.52), "the contact came back")
        self.assertTrue(self.events[-1].cancels_held)
        self.assertEqual(self.quartz.posted, [], "the drag carries on")

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
        self.quartz.pointer = (10.3, 10.0)
        self.timers[-1].fire()
        self.assertEqual([event.kind for event in self.quartz.posted], [self.Q.kCGEventLeftMouseUp])

    def test_a_click_whose_small_motion_passed_gets_the_pointer_back(self) -> None:
        # A nudge under the click radius reaches apps while the release is
        # held; the release, re-sent where the button came up, must not
        # leave the pointer behind the hand.
        self.button(self.Q.kCGEventLeftMouseDown, (10, 10), 0.0)
        self.button(self.Q.kCGEventLeftMouseUp, (10, 10), 0.1)
        self.assertIsNotNone(self.motion((12, 10)), "a nudge passes")
        self.quartz.pointer = (12.0, 10.0)
        self.timers[-1].fire()
        release, restore = self.quartz.posted
        self.assertEqual((release.kind, release.location.x), (self.Q.kCGEventLeftMouseUp, 10))
        self.assertEqual((restore.kind, restore.location.x, self.mark(restore)), (self.Q.kCGEventMouseMoved, 12, RESTORE_MARK))

    def test_the_release_goes_out_when_the_pointer_cannot_be_read(self) -> None:
        self.button(self.Q.kCGEventLeftMouseDown, (0, 0), 0.0)
        self.button(self.Q.kCGEventLeftMouseUp, (50, 0), 0.5)
        self.quartz.pointer_error = RuntimeError("no event")
        with fresh_error_log(), self.assertLogs("app.platform", "WARNING"):
            self.timers[-1].fire()
        self.assertEqual([(event.kind, self.mark(event)) for event in self.quartz.posted], [(self.Q.kCGEventLeftMouseUp, INJECTED_MARK)])

    def test_motion_is_judged_by_where_it_went(self) -> None:
        self.button(self.Q.kCGEventLeftMouseDown, (100, 100), 0.0)
        self.assertIsNone(self.button(self.Q.kCGEventLeftMouseUp, (100, 100), 0.1))
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

    def test_stopping_invalidates_the_tap(self) -> None:
        self.filter.stop()
        self.assertFalse(self.filter.running)
        self.assertTrue(self.tap.invalidated, "a disabled tap stays registered until invalidated")
        self.assertFalse(self.tap.enabled)
        self.assertFalse(self.filter.tap_alive())

    def test_tap_alive_follows_the_tap(self) -> None:
        self.assertTrue(self.filter.tap_alive())
        self.tap.enabled = False  # macOS disabled it
        self.assertFalse(self.filter.tap_alive())

    def disable(self) -> object:
        self.tap.enabled = False
        return self.tap.callback(None, self.Q.kCGEventTapDisabledByTimeout, None, None)

    def test_a_disabled_tap_is_rearmed(self) -> None:
        self.disable()
        self.assertTrue(self.tap.enabled)
        self.assertEqual(self.filter.tap_resets, 1)
        self.assertTrue(self.filter.running)

    def test_a_disabled_tap_is_rearmed_while_motion_is_judged(self) -> None:
        self.button(self.Q.kCGEventLeftMouseDown, (0, 0), 0.0)
        self.button(self.Q.kCGEventLeftMouseUp, (0, 0), 0.1)            # held in place
        self.assertTrue(self.filter._motion_wanted)
        self.disable()
        self.assertTrue(self.tap.enabled)
        self.assertEqual(self.filter.tap_resets, 1)

    def test_macos_disabling_the_tap_again_and_again_stops_the_filter(self) -> None:
        self.button(self.Q.kCGEventLeftMouseDown, (0, 0), 0.0)
        self.button(self.Q.kCGEventLeftMouseUp, (30, 0), 0.4)          # held when it happens
        self.disable()
        self.disable()
        self.assertEqual(self.errors, [])
        self.disable()
        self.filter._thread.join(2)
        self.assertFalse(self.filter.running)
        self.assertEqual(self.errors, [TAP_DISABLED_MESSAGE])
        self.assertEqual(self.filter.tap_resets, 2, "not re-armed a third time")
        self.assertTrue(self.tap.invalidated)
        released = [event for event in self.quartz.posted if event.kind == self.Q.kCGEventLeftMouseUp]
        self.assertEqual(len(released), 1, "the held release still reaches apps")
        self.assertEqual(self.lost, [])

    def test_a_tap_disabled_after_the_permission_is_gone_fails_open(self) -> None:
        self.button(self.Q.kCGEventLeftMouseDown, (0, 0), 0.0)
        self.button(self.Q.kCGEventLeftMouseUp, (0, 0), 0.1)            # held in place
        self.assertTrue(self.filter._motion_wanted)
        self.permitted = False
        with self.assertLogs("app.platform", "INFO") as logged:
            self.disable()
            self.filter._thread.join(2)
        self.assertFalse(self.filter.running, "the hook ends")
        self.assertEqual(self.errors, [], "not reported as an error")
        self.assertEqual(self.lost, [("dcf-hook", 1)], "told once, from the hook thread, after the release went out")
        released = [event for event in self.quartz.posted if event.kind == self.Q.kCGEventLeftMouseUp]
        self.assertEqual(len(released), 1, "the held release still reaches apps")
        self.assertEqual(self.quartz.taps_enabled_at_post, [[False]], "posted once the tap was off")
        self.assertTrue(self.tap.invalidated)
        self.assertEqual((self.filter.tap_resets, self.filter._tap_disables), (0, []))
        self.assertIn("permission gone", "\n".join(logged.output))

    def test_a_failing_permission_check_still_rearms(self) -> None:
        def broken() -> bool:
            raise OSError("no answer")

        self.filter._permission_ok = broken
        with fresh_error_log(), self.assertLogs("app.platform", "WARNING"):
            self.disable()
        self.assertTrue(self.tap.enabled)
        self.assertEqual(self.filter.tap_resets, 1)

    def test_the_hook_logs_its_rearms_when_it_ends(self) -> None:
        self.disable()
        with self.assertLogs("app.platform", "INFO") as logged:
            self.filter.stop()
        self.assertIn("tap resets 1, hook re-arms 0", "\n".join(logged.output))

    def test_a_refused_tap_says_where_to_allow_the_app(self) -> None:
        self.filter.stop()
        self.quartz.refuse_taps = True
        with mock.patch("app.permissions.pane_name", return_value="Device Control and Data Access"):
            with self.assertRaises(HookError) as raised:
                GlobalClickFilter(60, [Button.LEFT]).start()
        self.assertEqual(
            str(raised.exception),
            "macOS refused the event tap. Allow DoubleClick Fixer in System Settings › Privacy & Security "
            "› Device Control and Data Access, then try again.",
        )


if __name__ == "__main__":
    unittest.main()
