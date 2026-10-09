"""Wear history: daily counts per button, the file, the trend and the histogram."""

try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

import json
import logging
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from app import settings, wear
from app.core import Button
from app.core import ClickEvent
from app.wear import DayStats, WearHistory, classify_trend

# The app logs failures on purpose; keep them out of the test output.
logging.getLogger("app").addHandler(logging.NullHandler())
logging.getLogger("app").propagate = False


class Clock:
    """Local time under the test's control, starting at noon on 9 Oct 2026."""

    def __init__(self) -> None:
        self.now = datetime(2026, 10, 9, 12, 0, 0).timestamp()

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float = 0.0, days: int = 0) -> None:
        self.now += seconds + days * 86400


def press(button=Button.LEFT, gap=None, bounce=False):
    return ClickEvent(button, True, not bounce, gap, None)


def dropout(button=Button.LEFT, gap=6.0):
    return ClickEvent(button, True, False, gap, None, cancels_held=True)


def release(button=Button.LEFT):
    return ClickEvent(button, False, True, None, None)


class WearTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp())
        patcher = mock.patch.object(settings, "config_dir", return_value=self.directory)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.clock = Clock()
        self.history = WearHistory(clock=self.clock)

    def clicks(self, count: int, bounces: int = 0, button=Button.LEFT, gap: float = 9.0, window: int = 46) -> None:
        for _ in range(count):
            self.history.note_event(press(button, 400.0), window)
        for _ in range(bounces):
            self.history.note_event(press(button, gap, bounce=True), window)

    # -- counting --------------------------------------------------------------
    def test_presses_bounces_and_dropouts_are_counted_per_button(self) -> None:
        self.clicks(10, bounces=2)
        self.history.note_event(dropout(), 46)
        self.history.note_event(release(), 46)  # releases aren't counted
        self.history.note_event(press(Button.BACK, 300.0), 30)
        today = self.history.daily(Button.LEFT)[-1]
        self.assertEqual((today.presses, today.bounces, today.dropouts), (13, 2, 1))
        self.assertEqual(today.clicks, 10, "the user's own presses")
        self.assertAlmostEqual(today.rate, 20.0, msg="2 bounces per 10 clicks")
        self.assertEqual(self.history.daily(Button.BACK)[-1].presses, 1)
        self.assertEqual(self.history.buttons_with_data(), [Button.LEFT, Button.BACK])

    def test_a_day_without_clicks_has_no_rate(self) -> None:
        self.assertIsNone(DayStats("2026-10-09").rate)
        self.assertIsNone(self.history.daily(Button.LEFT)[0].rate)

    def test_the_last_30_days_end_today(self) -> None:
        days = self.history.daily(Button.LEFT)
        self.assertEqual(len(days), wear.SHOWN_DAYS)
        self.assertEqual(days[-1].day, "2026-10-09")
        self.assertEqual(days[0].day, "2026-09-10")

    def test_counts_after_midnight_go_to_the_new_day(self) -> None:
        self.clock.now = datetime(2026, 10, 9, 23, 59, 59).timestamp()
        self.clicks(3)
        self.clock.advance(2)
        self.clicks(5)
        days = self.history.daily(Button.LEFT)
        self.assertEqual((days[-2].day, days[-2].presses), ("2026-10-09", 3))
        self.assertEqual((days[-1].day, days[-1].presses), ("2026-10-10", 5))

    def test_bounce_gaps_fill_2_ms_bins_up_to_the_window(self) -> None:
        for gap in (0.4, 9.0, 9.9, 10.0, 46.0):
            self.history.note_event(press(gap=gap, bounce=True), 46)
        histogram = self.history.histogram(Button.LEFT)
        self.assertEqual(histogram.window_ms, 46)
        self.assertEqual([low for low, _count in histogram.bins], list(range(0, 47, 2)))
        counts = dict(histogram.bins)
        self.assertEqual((counts[0], counts[8], counts[10], counts[46]), (1, 2, 1, 1))
        self.assertEqual(histogram.overflow, 0)
        self.assertEqual(histogram.total, 5)

    def test_a_window_shortened_during_the_day_leaves_earlier_gaps_in_the_overflow_bin(self) -> None:
        self.history.note_event(press(gap=40.0, bounce=True), 30)  # arrives after the window shrank
        self.history.note_event(press(gap=12.0, bounce=True), 30)
        histogram = self.history.histogram(Button.LEFT)
        self.assertEqual(histogram.overflow, 1)
        self.assertEqual(dict(histogram.bins)[12], 1)
        self.history.save()
        stored = json.loads((self.directory / "wear.json").read_text())["days"]["2026-10-09"]["left"]
        self.assertEqual((stored["gaps"], stored["over"]), ({"12": 1}, 1), "bins up to the window, then one more")

    def test_the_histogram_spans_the_longest_window_of_the_period(self) -> None:
        self.history.note_event(press(gap=50.0, bounce=True), 60)
        self.clock.advance(days=1)
        self.history.note_event(press(gap=20.0, bounce=True), 30)
        histogram = self.history.histogram(Button.LEFT, window_ms=30)
        self.assertEqual(histogram.window_ms, 60)
        counts = dict(histogram.bins)
        self.assertEqual((counts[50], counts[20]), (1, 1))

    def test_dropouts_are_not_bounce_gaps(self) -> None:
        self.history.note_event(dropout(gap=6.0), 46)
        self.assertEqual(self.history.histogram(Button.LEFT).total, 0)

    def test_the_wheel(self) -> None:
        for dropped in (False, False, True):
            self.history.note_wheel(1, dropped)
        self.assertEqual(self.history.wheel_totals(), (3, 1))
        self.assertEqual(self.history.buttons_with_data(), [], "the wheel isn't a button")

    # -- the file --------------------------------------------------------------
    def test_save_and_load(self) -> None:
        self.clicks(7, bounces=1, gap=12.0)
        self.history.note_wheel(0, True)
        self.assertTrue(self.history.save())
        stored = json.loads((self.directory / "wear.json").read_text())
        self.assertEqual(stored["version"], wear.FORMAT_VERSION)
        entry = stored["days"]["2026-10-09"]["left"]
        self.assertEqual((entry["presses"], entry["bounces"], entry["gaps"], entry["window"]), (8, 1, {"12": 1}, 46))
        loaded = WearHistory(clock=self.clock)
        loaded.load()
        self.assertEqual(loaded.daily(Button.LEFT)[-1], self.history.daily(Button.LEFT)[-1])
        self.assertEqual(loaded.wheel_totals(), (1, 1))

    def test_nothing_new_writes_nothing(self) -> None:
        self.assertFalse(self.history.save())
        self.assertFalse((self.directory / "wear.json").exists())

    def test_saved_every_five_minutes_at_most(self) -> None:
        self.clicks(1)
        self.assertFalse(self.history.save_if_due(), "not yet")
        self.clock.advance(wear.SAVE_INTERVAL_S)
        self.assertTrue(self.history.save_if_due())
        self.clicks(1)
        self.clock.advance(60)
        self.assertFalse(self.history.save_if_due())

    def test_written_the_safe_way_with_a_spare(self) -> None:
        self.clicks(2)
        with mock.patch.object(settings, "write_json", wraps=settings.write_json) as write:
            self.history.save()
        write.assert_called_once()
        self.clicks(3)
        self.history.save()
        spare = json.loads((self.directory / "wear.json.bak").read_text())
        self.assertEqual(spare["days"]["2026-10-09"]["left"]["presses"], 2)

    def test_a_damaged_file_falls_back_to_the_spare(self) -> None:
        self.clicks(2)
        self.history.save()
        self.clicks(3)
        self.history.save()
        (self.directory / "wear.json").write_bytes(b"\x00" * 32)
        loaded = WearHistory(clock=self.clock)
        loaded.load()
        self.assertEqual(loaded.daily(Button.LEFT)[-1].presses, 2)

    def locked(self, *names: str):
        """Make the files called `names` unreadable, as a backup tool or a
        virus scanner holding them would."""
        real = Path.read_bytes

        def read_bytes(path):
            if path.name in names:
                raise PermissionError(32, "locked")
            return real(path)

        return mock.patch.object(Path, "read_bytes", read_bytes)

    def a_year_on_file(self) -> str:
        (self.directory / "wear.json").write_text(json.dumps({"version": 1, "days": {
            "2026-01-05": {"left": {"presses": 900, "bounces": 30}},
            "2026-10-08": {"left": {"presses": 500, "bounces": 9}},
        }}))
        return (self.directory / "wear.json").read_text()

    def test_a_file_locked_at_launch_is_never_written_over(self) -> None:
        # Locked through every try, and no spare to stand in.
        stored = self.a_year_on_file()
        with self.locked("wear.json"), mock.patch.object(settings, "sleep"):
            self.assertFalse(self.history.load())
            self.clicks(3)
            self.assertFalse(self.history.save(), "nothing is written over what couldn't be read")
        self.assertEqual((self.directory / "wear.json").read_text(), stored)
        self.assertFalse((self.directory / "wear.json.bak").exists())
        self.assertEqual(self.history.daily(Button.LEFT)[-1].presses, 3, "the counts since launch are kept")
        # The lock lifts: the next save reads the file first and adds to it.
        self.assertTrue(self.history.save())
        days = json.loads((self.directory / "wear.json").read_text())["days"]
        self.assertEqual(days["2026-01-05"]["left"]["presses"], 900)
        self.assertEqual(days["2026-10-08"]["left"]["presses"], 500)
        self.assertEqual(days["2026-10-09"]["left"]["presses"], 3)
        self.clicks(1)
        self.assertTrue(self.history.save())
        spare = json.loads((self.directory / "wear.json.bak").read_text())["days"]
        self.assertEqual(spare["2026-01-05"]["left"]["presses"], 900, "the spare is the whole history too")
        self.assertEqual(self.history.daily(Button.LEFT)[-1].presses, 4, "nothing counted twice")

    def test_a_locked_spare_does_not_stand_in_for_a_locked_file(self) -> None:
        self.a_year_on_file()
        (self.directory / "wear.json.bak").write_text(json.dumps({"version": 1, "days": {}}))
        with self.locked("wear.json", "wear.json.bak"), mock.patch.object(settings, "sleep"):
            self.assertFalse(self.history.load())
            self.clicks(1)
            self.assertFalse(self.history.save())
            self.history._saved_at -= wear.SAVE_INTERVAL_S
            self.assertFalse(self.history.save_if_due(), "tried again when due, still locked")
        self.assertIn("2026-01-05", json.loads((self.directory / "wear.json").read_text())["days"])

    def test_a_good_spare_stands_in_for_a_locked_file(self) -> None:
        self.clicks(2)
        self.history.save()
        self.clicks(3)
        self.history.save()  # the 2-press file is now the spare
        loaded = WearHistory(clock=self.clock)
        with self.locked("wear.json"), mock.patch.object(settings, "sleep"):
            self.assertTrue(loaded.load())
        self.assertEqual(loaded.daily(Button.LEFT)[-1].presses, 2)

    def test_starting_afresh_needs_no_read(self) -> None:
        self.a_year_on_file()
        with self.locked("wear.json"), mock.patch.object(settings, "sleep"):
            self.assertFalse(self.history.load())
        self.history.clear()
        self.clicks(1)
        self.assertTrue(self.history.save())
        self.assertEqual(list(json.loads((self.directory / "wear.json").read_text())["days"]), ["2026-10-09"])

    def test_a_failed_write_is_tried_again(self) -> None:
        self.clicks(1)
        with mock.patch.object(settings, "write_json", side_effect=OSError("disk full")):
            self.assertFalse(self.history.save())
        self.assertTrue(self.history.save(), "still has something to write")

    def test_a_year_is_kept(self) -> None:
        self.clicks(1)
        self.clock.advance(days=wear.KEEP_DAYS - 1)
        self.clicks(1)
        self.history.save()
        self.assertIn("2026-10-09", json.loads((self.directory / "wear.json").read_text())["days"])
        self.clock.advance(days=1)
        self.clicks(1)
        self.history.save()
        days = json.loads((self.directory / "wear.json").read_text())["days"]
        self.assertNotIn("2026-10-09", days, "older than 365 days")
        self.assertEqual(len(days), 2)

    def test_old_days_in_the_file_are_dropped_on_load(self) -> None:
        old = (datetime(2026, 10, 9) - timedelta(days=wear.KEEP_DAYS)).date().isoformat()
        (self.directory / "wear.json").write_text(json.dumps({"version": 1, "days": {
            old: {"left": {"presses": 5}},
            "2026-10-08": {"left": {"presses": 4}},
        }}))
        self.history.load()
        self.assertEqual(self.history.totals(Button.LEFT).presses, 4)
        self.assertNotIn(old, self.history._days)

    def test_malformed_entries_are_left_out(self) -> None:
        (self.directory / "wear.json").write_text(json.dumps({"version": 1, "days": {
            "not a day": {"left": {"presses": 5}},
            "2026-10-08": {"left": {"presses": "3", "bounces": -2, "gaps": {"8": 2, "7": 1, "x": 4}, "over": True},
                           "x9": {"presses": 9}, "right": "nonsense", "wheel": {"ticks": 4, "dropped": 1}},
            "2026-10-07": [],
        }}))
        self.history.load()
        day = self.history.daily(Button.LEFT)[-2]
        self.assertEqual((day.presses, day.bounces), (3, 0))
        self.assertEqual(dict(self.history.histogram(Button.LEFT, window_ms=10).bins)[8], 2)
        self.assertEqual(self.history.wheel_totals(), (4, 1))

    def test_counting_from_another_thread_while_saving_loses_nothing(self) -> None:
        def hook() -> None:
            for _ in range(2000):
                self.history.note_event(press(gap=400.0), 46)

        threads = [threading.Thread(target=hook) for _ in range(4)]
        for thread in threads:
            thread.start()
        for _ in range(20):
            self.history.save()
        for thread in threads:
            thread.join()
        self.history.save()
        stored = json.loads((self.directory / "wear.json").read_text())
        self.assertEqual(stored["days"]["2026-10-09"]["left"]["presses"], 8000)

    # -- the trend -------------------------------------------------------------
    def period(self, clicks: int, bounces: int) -> DayStats:
        return DayStats("", clicks + bounces, bounces, 0)

    def test_trend_words(self) -> None:
        self.assertEqual(classify_trend(self.period(5000, 50), self.period(5000, 150)), wear.WORSE)
        self.assertEqual(classify_trend(self.period(5000, 150), self.period(5000, 50)), wear.BETTER)
        self.assertEqual(classify_trend(self.period(5000, 50), self.period(5000, 60)), wear.SAME)
        self.assertEqual(classify_trend(self.period(5000, 0), self.period(5000, 0)), wear.SAME)

    def test_too_few_clicks_is_no_trend(self) -> None:
        self.assertEqual(classify_trend(self.period(150, 0), self.period(5000, 300)), wear.UNKNOWN)
        self.assertEqual(classify_trend(self.period(5000, 1), self.period(199, 50)), wear.UNKNOWN)

    def test_a_big_ratio_on_a_few_bounces_is_chance(self) -> None:
        # One bounce, then two: twice the rate, but nothing to go on.
        self.assertEqual(classify_trend(self.period(3000, 1), self.period(3000, 2)), wear.SAME)
        # And from none to a handful.
        self.assertEqual(classify_trend(self.period(3000, 0), self.period(3000, 2)), wear.SAME)

    def test_a_certain_but_small_change_is_about_the_same(self) -> None:
        # 2.0 against 2.4 per 100: clear with this many clicks, but not 1.5x.
        self.assertEqual(classify_trend(self.period(200000, 4000), self.period(200000, 4800)), wear.SAME)

    def test_the_trend_compares_the_older_half_with_the_newer(self) -> None:
        for day in range(30):
            self.clicks(400, bounces=2 if day < 15 else 12)
            self.clock.advance(days=1)
        self.clock.advance(days=-1)
        trend = self.history.trend(Button.LEFT)
        self.assertEqual(trend.key, wear.WORSE)
        self.assertEqual(trend.words, "Getting worse")
        self.assertAlmostEqual(trend.earlier_rate, 0.5)
        self.assertAlmostEqual(trend.recent_rate, 3.0)
        self.assertFalse(trend.window_changed)

    def test_a_changed_window_is_noted(self) -> None:
        self.clicks(10, window=46)
        self.clock.advance(days=1)
        self.clicks(10, window=60)
        self.assertTrue(self.history.trend(Button.LEFT).window_changed)


if __name__ == "__main__":
    unittest.main()
