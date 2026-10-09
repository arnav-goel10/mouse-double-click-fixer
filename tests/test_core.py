try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

import unittest

from app.core import (
    DEFAULT_ALLOWANCE_MS,
    DEFAULT_THRESHOLD_MS,
    LATENESS_SAMPLES,
    MAX_ALLOWANCE_MS,
    MAX_THRESHOLD_MS,
    MIN_ALLOWANCE_MS,
    MIN_THRESHOLD_MS,
    REQUIRED_DOUBLE_CLICKS,
    BounceFilter,
    Button,
    Calibrator,
    DeliveryDelay,
    clamp_threshold,
)


class BounceFilterTests(unittest.TestCase):
    def click(self, click_filter: BounceFilter, press_at: float, release_at: float):
        press = click_filter.press(timestamp=press_at)
        release = click_filter.release(timestamp=release_at)
        return press, release

    def test_first_press_is_always_accepted(self) -> None:
        press, _ = self.click(BounceFilter(60), 1.0, 1.05)
        self.assertTrue(press.accepted)
        self.assertIsNone(press.gap_ms)

    def test_press_soon_after_release_is_bounce(self) -> None:
        click_filter = BounceFilter(60)
        self.click(click_filter, 1.0, 1.05)
        press = click_filter.press(timestamp=1.06)  # 10 ms after the release
        self.assertFalse(press.accepted)
        self.assertTrue(press.is_bounce)
        self.assertAlmostEqual(press.gap_ms or 0, 10, places=6)

    def test_deliberate_double_click_survives(self) -> None:
        click_filter = BounceFilter(60, hold_releases=False)
        self.click(click_filter, 1.0, 1.06)
        # 120 ms between release and the second press: a human double-click.
        press, _ = self.click(click_filter, 1.18, 1.24)
        self.assertTrue(press.accepted)

    def test_fast_repeated_clicking_is_not_filtered(self) -> None:
        # Six clicks per second, which a fast clicker can reach on purpose.
        click_filter = BounceFilter(60, hold_releases=False)
        accepted = 0
        moment = 1.0
        for _ in range(6):
            press, _ = self.click(click_filter, moment, moment + 0.05)
            accepted += int(press.accepted)
            moment += 0.166
        self.assertEqual(accepted, 6)

    def test_suppressed_press_also_suppresses_its_release(self) -> None:
        click_filter = BounceFilter(60)
        self.click(click_filter, 1.0, 1.05)
        press = click_filter.press(timestamp=1.06)
        release = click_filter.release(timestamp=1.09)
        self.assertFalse(press.accepted)
        self.assertFalse(release.accepted, "half a click must never reach an application")

    def test_bounce_chain_is_measured_from_the_swallowed_release(self) -> None:
        click_filter = BounceFilter(60, hold_releases=False)
        self.click(click_filter, 1.0, 1.05)
        for offset in (0.06, 0.07, 0.08):  # a burst of chatter
            press = click_filter.press(timestamp=1.0 + offset)
            click_filter.release(timestamp=1.005 + offset)
            self.assertFalse(press.accepted)
        # A real click well after the burst is accepted again.
        self.assertTrue(click_filter.press(timestamp=1.5).accepted)

    def test_disabled_filter_accepts_everything(self) -> None:
        click_filter = BounceFilter(60, enabled=False)
        self.click(click_filter, 1.0, 1.05)
        press = click_filter.press(timestamp=1.055)
        self.assertTrue(press.accepted)
        self.assertFalse(press.is_bounce)

    def test_counts_filtered_events(self) -> None:
        click_filter = BounceFilter(60)
        self.click(click_filter, 1.0, 1.05)
        self.click(click_filter, 1.06, 1.065)
        self.click(click_filter, 1.07, 1.075)
        self.assertEqual(click_filter.filtered_count, 2)


class ThresholdTests(unittest.TestCase):
    def test_clamped_to_supported_range(self) -> None:
        self.assertEqual(clamp_threshold(0), MIN_THRESHOLD_MS)
        self.assertEqual(clamp_threshold(10_000), MAX_THRESHOLD_MS)
        self.assertEqual(clamp_threshold("nonsense"), DEFAULT_THRESHOLD_MS)
        self.assertEqual(clamp_threshold(62.4), 62)


class CalibratorTests(unittest.TestCase):
    def calibrate(self, bounces, double_gaps) -> Calibrator:
        calibrator = Calibrator()
        for _ in range(12):
            calibrator.add_single_click(900.0)
        for gap in bounces:
            calibrator.add_single_click(gap)
        for gap in double_gaps:
            calibrator.add_double_click(gap)
        return calibrator

    def test_no_suggestion_without_double_clicks(self) -> None:
        self.assertIsNone(self.calibrate([8.0], []).suggest())

    def test_suggestion_clears_the_worst_bounce(self) -> None:
        calibrator = self.calibrate([6.0, 9.0, 14.0], [140.0, 160.0, 150.0, 180.0, 210.0])
        suggestion = calibrator.suggest()
        assert suggestion is not None
        self.assertGreater(suggestion.threshold_ms, 14, "must filter the worst bounce measured")
        self.assertLess(suggestion.threshold_ms, 140, "must never reach a real double-click")
        self.assertTrue(suggestion.confident)
        self.assertEqual(suggestion.bounces_seen, 3)

    def test_fast_double_clicker_gets_a_safe_ceiling(self) -> None:
        calibrator = self.calibrate([30.0], [55.0, 60.0, 70.0, 66.0, 58.0])
        suggestion = calibrator.suggest()
        assert suggestion is not None
        self.assertLessEqual(suggestion.threshold_ms, 28, "no more than half the fastest double-click")
        self.assertFalse(suggestion.confident, "too little room should be reported honestly")

    def test_clean_mouse_still_gets_a_default(self) -> None:
        calibrator = self.calibrate([], [200.0, 220.0, 190.0, 240.0, 210.0])
        suggestion = calibrator.suggest()
        assert suggestion is not None
        self.assertEqual(suggestion.threshold_ms, DEFAULT_THRESHOLD_MS)
        self.assertEqual(suggestion.bounces_seen, 0)

    def test_single_phase_separates_bounce_from_clicks(self) -> None:
        calibrator = Calibrator()
        self.assertTrue(calibrator.add_single_click(800.0))
        self.assertFalse(calibrator.add_single_click(9.0))
        self.assertEqual(calibrator.single_clicks, 1)
        self.assertEqual(calibrator.single_gaps_ms, [9.0])

    def test_bounce_inside_the_double_click_phase_counts_as_bounce(self) -> None:
        calibrator = Calibrator()
        self.assertFalse(calibrator.add_double_click(7.0))
        self.assertEqual(calibrator.single_gaps_ms, [7.0])
        self.assertEqual(calibrator.double_clicks, 0)

    def test_progress_reaches_one(self) -> None:
        calibrator = self.calibrate([], [150.0] * REQUIRED_DOUBLE_CLICKS)
        self.assertEqual(calibrator.single_progress, 1.0)
        self.assertEqual(calibrator.double_progress, 1.0)


class ButtonTests(unittest.TestCase):
    def test_labels(self) -> None:
        self.assertEqual([button.label for button in Button], ["Left", "Right", "Middle"])


class DragDropoutTests(unittest.TestCase):
    """A worn switch can drop contact for a few ms while the button is held."""

    def test_dropout_mid_drag_is_invisible(self) -> None:
        f = BounceFilter(60)
        self.assertTrue(f.press(timestamp=0.0).accepted)          # start dragging
        release = f.release(timestamp=0.5)                        # contact drops
        self.assertTrue(release.held, "a release after a long hold is held back")
        self.assertFalse(release.accepted)
        press = f.press(timestamp=0.508)                          # contact returns
        self.assertTrue(press.cancels_held)
        self.assertFalse(press.accepted)
        self.assertFalse(f.commit_held(), "nothing left to deliver")
        final = f.release(timestamp=2.0)                          # the real lift
        self.assertTrue(final.held)
        self.assertTrue(f.commit_held(), "the real lift is delivered after the window")

    def test_brief_taps_are_held_too(self) -> None:
        # No length of press is safe to skip: a release this soon can be the
        # contact bouncing as it closes, the start of a drag.
        f = BounceFilter(60)
        f.press(timestamp=0.0)
        release = f.release(timestamp=0.02)  # a 20 ms tap
        self.assertTrue(release.held)
        self.assertEqual(release.hold_reason, "lift")
        self.assertTrue(f.commit_held(), "it is delivered once the window passes")

    def test_a_release_as_the_contact_closes_is_held_as_closing(self) -> None:
        f = BounceFilter(60)
        f.press(timestamp=0.0)
        release = f.release(timestamp=0.004)
        self.assertTrue(release.held)
        self.assertEqual(release.hold_reason, "closing")

    def test_a_bounce_as_the_contact_closes_restarts_the_closing_time(self) -> None:
        # The comeback press never reaches apps, but the contact did close,
        # and it is still settling: a second bounce soon after is closing too.
        f = BounceFilter(60)
        f.press(timestamp=0.0)
        self.assertEqual(f.release(timestamp=0.004).hold_reason, "closing")
        self.assertTrue(f.press(timestamp=0.008).cancels_held)
        self.assertEqual(f.release(timestamp=0.015).hold_reason, "closing", "7 ms after the contact closed again")

    def test_release_chatter_is_still_a_lift(self) -> None:
        # The finger lets go and the contact chatters open: the comeback
        # cancels a lift, so the release after it is the finger letting go.
        f = BounceFilter(60)
        f.press(timestamp=0.0)
        self.assertEqual(f.release(timestamp=0.090).hold_reason, "lift")
        self.assertTrue(f.press(timestamp=0.093).cancels_held)
        self.assertEqual(f.release(timestamp=0.096).hold_reason, "lift")
        self.assertTrue(f.commit_held(), "one click, delivered")

    def test_only_held_releases_carry_a_reason(self) -> None:
        f = BounceFilter(60, hold_releases=False)
        self.assertIsNone(f.press(timestamp=0.0).hold_reason)
        self.assertIsNone(f.release(timestamp=0.1).hold_reason)

    def test_two_bounces_as_the_contact_closes_keep_the_drag(self) -> None:
        # D, U 4.7 ms, D 7.8 ms, then a second bounce opening at 12.7 ms (past
        # the impossible-tap time from the first press) or at 20 ms.
        for second_bounce in (0.0127, 0.020):
            with self.subTest(second_bounce=second_bounce):
                f = BounceFilter(60)
                self.assertTrue(f.press(timestamp=0.0).accepted)
                self.assertTrue(f.release(timestamp=0.0047).held)
                self.assertTrue(f.press(timestamp=0.0078).cancels_held)
                release = f.release(timestamp=second_bounce)
                self.assertTrue(release.held, "a release delivered here would turn the drag into a click")
                # 4.9 ms after the contact closed again: still closing. At
                # 20 ms it reads as a lift, which is held all the same.
                self.assertEqual(release.hold_reason, "closing" if second_bounce < 0.019 else "lift")
                self.assertTrue(f.press(timestamp=second_bounce + 0.0008).cancels_held)
                lift = f.release(timestamp=1.365)
                self.assertTrue(lift.held)
                self.assertEqual(lift.hold_reason, "lift")
                self.assertTrue(f.commit_held(), "the real lift is delivered")

    def test_early_dropout_keeps_the_drag(self) -> None:
        # Captured on a real worn switch: contact lost 60 ms into a drag.
        f = BounceFilter(46)
        f.press(timestamp=0.0)
        self.assertTrue(f.release(timestamp=0.060).held)
        self.assertTrue(f.press(timestamp=0.075).cancels_held)
        self.assertTrue(f.release(timestamp=0.5).held)
        self.assertTrue(f.commit_held())

    def test_bouncy_click_becomes_one_click(self) -> None:
        f = BounceFilter(46)
        f.press(timestamp=0.0)
        self.assertTrue(f.release(timestamp=0.080).held)
        self.assertTrue(f.press(timestamp=0.087).cancels_held)
        self.assertTrue(f.release(timestamp=0.095).held)
        self.assertTrue(f.commit_held())
        self.assertFalse(f.commit_held())

    def test_release_is_delivered_when_no_press_follows(self) -> None:
        f = BounceFilter(60)
        f.press(timestamp=0.0)
        self.assertTrue(f.release(timestamp=0.3).held)
        self.assertTrue(f.commit_held())
        # A later press is measured from that release, as usual.
        press = f.press(timestamp=1.0)
        self.assertTrue(press.accepted)
        self.assertAlmostEqual(press.gap_ms or 0, 700, places=6)

    def test_press_after_the_window_flushes_the_release_first(self) -> None:
        f = BounceFilter(60)
        f.press(timestamp=0.0)
        f.release(timestamp=0.3)
        press = f.press(timestamp=0.5)  # before the timer ran, after the window
        self.assertTrue(press.flush_held, "the held release must go out first")
        self.assertFalse(press.is_bounce)

    def test_disabled_button_never_holds(self) -> None:
        f = BounceFilter(60, enabled=False)
        f.press(timestamp=0.0)
        self.assertTrue(f.release(timestamp=0.5).accepted)


if __name__ == "__main__":
    unittest.main()


class DueTests(unittest.TestCase):
    """An event stamped past a held release's window settles it."""

    def test_only_an_event_stamped_past_the_window_settles_the_release(self) -> None:
        # 125 ms: a threshold whose edge is exact in binary.
        click_filter = BounceFilter(125)
        click_filter.press(timestamp=1.0)
        self.assertFalse(click_filter.due(5.0), "nothing is held")
        self.assertTrue(click_filter.release(timestamp=1.5).held)
        self.assertEqual(click_filter.held_at, 1.5)
        self.assertFalse(click_filter.due(1.6))
        self.assertFalse(click_filter.due(1.625), "the edge is still inside, as for a press")
        self.assertTrue(click_filter.due(1.626))

    def test_due_and_a_press_draw_the_line_in_the_same_place(self) -> None:
        for threshold in (30, 40, 46, 60):
            for gap_ms in range(threshold - 3, threshold + 4):
                with self.subTest(threshold=threshold, gap_ms=gap_ms):
                    click_filter = BounceFilter(threshold)
                    click_filter.press(timestamp=1.0)
                    click_filter.release(timestamp=1.3)
                    at = 1.3 + gap_ms / 1000
                    settles = click_filter.due(at)
                    event = click_filter.press(timestamp=at)
                    self.assertEqual(event.flush_held, settles)
                    self.assertEqual(event.cancels_held, not settles)

    def test_the_threshold_in_force_decides(self) -> None:
        click_filter = BounceFilter(40)
        click_filter.press(timestamp=1.0)
        click_filter.release(timestamp=1.5)
        click_filter.threshold_ms = 80
        self.assertFalse(click_filter.due(1.55))
        self.assertTrue(click_filter.due(1.59))


class DeliveryDelayTests(unittest.TestCase):
    """The allowance the timer for a held release waits on top of the window."""

    def test_before_any_event_it_is_the_default(self) -> None:
        self.assertEqual(DeliveryDelay().allowance_ms(), DEFAULT_ALLOWANCE_MS)

    def test_it_covers_ninety_five_percent_of_recent_events(self) -> None:
        delay = DeliveryDelay()
        for ms in range(1, 101):  # 1..100 ms; only the latest 64 count
            delay.add(ms / 1000)
        # 37..100 kept: the 61st of 64 is 97 ms.
        self.assertAlmostEqual(delay.allowance_ms(), 97.0, places=6)

    def test_it_is_clamped_both_ways(self) -> None:
        quick, slow = DeliveryDelay(), DeliveryDelay()
        for _ in range(LATENESS_SAMPLES):
            quick.add(0.0002)
            slow.add(0.400)
        self.assertEqual(quick.allowance_ms(), MIN_ALLOWANCE_MS)
        self.assertEqual(slow.allowance_ms(), MAX_ALLOWANCE_MS)

    def test_a_busy_spell_ages_out(self) -> None:
        delay = DeliveryDelay()
        for _ in range(LATENESS_SAMPLES):
            delay.add(0.120)
        self.assertAlmostEqual(delay.allowance_ms(), 120.0, places=6)
        for _ in range(LATENESS_SAMPLES):
            delay.add(0.008)
        self.assertAlmostEqual(delay.allowance_ms(), 8.0, places=6)

    def test_a_few_slow_events_set_it_when_they_are_over_five_percent(self) -> None:
        delay = DeliveryDelay()
        for index in range(LATENESS_SAMPLES):
            delay.add(0.090 if index % 16 == 0 else 0.010)  # 4 of 64 slow
        self.assertAlmostEqual(delay.allowance_ms(), 90.0, places=6)
        delay = DeliveryDelay()
        for index in range(LATENESS_SAMPLES):
            delay.add(0.090 if index % 32 == 0 else 0.010)  # 2 of 64 slow
        self.assertAlmostEqual(delay.allowance_ms(), 10.0, places=6)

    def test_stamps_from_another_clock_are_ignored(self) -> None:
        delay = DeliveryDelay()
        for seconds in (5_000.0, -3.0, float("nan"), float("inf")):
            delay.add(seconds)
        self.assertEqual(delay.allowance_ms(), DEFAULT_ALLOWANCE_MS)

    def test_a_stamp_a_hair_ahead_counts_as_on_time(self) -> None:
        delay = DeliveryDelay()
        delay.add(-0.0004)
        self.assertEqual(delay.allowance_ms(), MIN_ALLOWANCE_MS)
