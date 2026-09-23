import unittest

from app.core import (
    DEFAULT_THRESHOLD_MS,
    MAX_THRESHOLD_MS,
    MIN_THRESHOLD_MS,
    REQUIRED_DOUBLE_CLICKS,
    BounceFilter,
    Button,
    Calibrator,
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
        click_filter = BounceFilter(60)
        self.click(click_filter, 1.0, 1.06)
        # 120 ms between release and the second press: a human double-click.
        press, _ = self.click(click_filter, 1.18, 1.24)
        self.assertTrue(press.accepted)

    def test_fast_repeated_clicking_is_not_filtered(self) -> None:
        # Six clicks per second, which a fast clicker can reach on purpose.
        click_filter = BounceFilter(60)
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
        click_filter = BounceFilter(60)
        self.click(click_filter, 1.0, 1.05)
        for offset in (0.06, 0.07, 0.08):  # a burst of chatter
            press = click_filter.press(timestamp=1.0 + offset)
            click_filter.release(timestamp=1.005 + offset)
            self.assertFalse(press.accepted)
        # A real click well after the burst is accepted again.
        self.assertTrue(click_filter.press(timestamp=1.5).accepted)

    def test_suppressed_run_is_reported_for_click_state_repair(self) -> None:
        click_filter = BounceFilter(60)
        self.click(click_filter, 1.0, 1.05)
        self.click(click_filter, 1.06, 1.07)  # bounce, suppressed
        press = click_filter.press(timestamp=1.30)
        self.assertTrue(press.accepted)
        self.assertEqual(press.suppressed_run, 1)

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


if __name__ == "__main__":
    unittest.main()


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

    def test_ordinary_clicks_gain_no_delay(self) -> None:
        f = BounceFilter(60)
        f.press(timestamp=0.0)
        release = f.release(timestamp=0.08)  # an 80 ms click
        self.assertTrue(release.accepted)
        self.assertFalse(release.held)

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

    def test_click_count_repair_reaches_the_release_too(self) -> None:
        f = BounceFilter(60)
        f.press(timestamp=0.0)
        f.release(timestamp=0.05)
        f.press(timestamp=0.06)              # bounce
        f.release(timestamp=0.065)
        press = f.press(timestamp=0.25)      # real second click
        release = f.release(timestamp=0.3)
        self.assertEqual(press.suppressed_run, 1)
        self.assertEqual(release.suppressed_run, 1, "mouse-up needs the same repair")
