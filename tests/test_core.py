import unittest

from app.core import DoubleClickEngine, ThresholdCalibrator
from app.platform import GlobalMouseMonitor


class DoubleClickEngineTests(unittest.TestCase):
    def test_first_click_is_accepted(self) -> None:
        result = DoubleClickEngine().process_click(timestamp=1.0)
        self.assertTrue(result.accepted)
        self.assertIsNone(result.interval_ms)

    def test_clicks_inside_threshold_are_double_clicks(self) -> None:
        engine = DoubleClickEngine(threshold_ms=400)
        engine.process_click(timestamp=1.0)
        result = engine.process_click(timestamp=1.35)
        self.assertTrue(result.is_double_click)
        self.assertAlmostEqual(result.interval_ms or 0, 350)

    def test_suppression_rejects_second_click(self) -> None:
        engine = DoubleClickEngine(threshold_ms=400, suppression_enabled=True)
        self.assertTrue(engine.process_click(timestamp=1.0).accepted)
        self.assertFalse(engine.process_click(timestamp=1.2).accepted)
        self.assertTrue(engine.process_click(timestamp=2.0).accepted)


class ThresholdCalibratorTests(unittest.TestCase):
    def test_needs_a_small_sample_before_suggesting(self) -> None:
        calibrator = ThresholdCalibrator()
        calibrator.add(250)
        calibrator.add(300)
        self.assertIsNone(calibrator.suggested_threshold())

    def test_suggests_a_bounded_threshold_from_intervals(self) -> None:
        calibrator = ThresholdCalibrator()
        for _ in range(10):
            calibrator.record_isolated_single_click()
        for interval in (180, 220, 240, 260, 300, 320, 200, 210, 230, 280):
            calibrator.add(interval)
        suggestion = calibrator.suggested_threshold()
        self.assertIsNotNone(suggestion)
        self.assertGreaterEqual(suggestion or 0, 20)
        self.assertLessEqual(suggestion or 0, 120)

    def test_labeled_samples_prioritize_false_positive_avoidance(self) -> None:
        calibrator = ThresholdCalibrator()
        for interval in (90, 100, 110, 115, 120):
            calibrator.add_single_click(interval)
        for interval in (180, 220, 200, 190, 210, 230, 240, 205, 215, 225):
            calibrator.add_intentional_double(interval)
        for _ in range(10):
            calibrator.record_isolated_single_click()
        suggestion = calibrator.suggested_threshold() or 0
        self.assertGreaterEqual(suggestion, 120)
        self.assertLess(suggestion, 180)


class GlobalMouseMonitorTests(unittest.TestCase):
    def test_monitor_suppresses_second_click_pair(self) -> None:
        events = []
        monitor = GlobalMouseMonitor(lambda is_double, interval: events.append((is_double, interval)), 400)
        self.assertTrue(monitor._handle_click(True))
        self.assertTrue(monitor._handle_click(False))
        self.assertFalse(monitor._handle_click(True))
        self.assertFalse(monitor._handle_click(False))
        self.assertTrue(events[-1][0])

    def test_conservative_filter_keeps_normal_double_clicks(self) -> None:
        engine = DoubleClickEngine(80, suppression_enabled=True)
        engine.process_click(timestamp=1.0)
        self.assertTrue(engine.process_click(timestamp=1.15).accepted)


if __name__ == "__main__":
    unittest.main()
