"""The plain-words health check, the suggestion to raise a window, and the
status sentence."""

try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

try:
    import _trust_helpers as helpers
except ImportError:
    from tests import _trust_helpers as helpers

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from app import health, settings
from app.conflicts import Conflict
from app.core import Button, ClickEvent
from app.health import Suggestion
from app.wear import Histogram, WearHistory


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 9, 12, 0, 0).timestamp()

    def __call__(self) -> float:
        return self.now


def histogram(gaps: dict, window: int, overflow: int = 0) -> Histogram:
    """Bins every 2 ms up to `window`, from {bin's lower edge: count}."""
    return Histogram([(low, gaps.get(low, 0)) for low in range(0, window + 1, 2)], overflow, window)


class PercentileTests(unittest.TestCase):
    def test_read_at_each_bins_upper_edge(self) -> None:
        self.assertEqual(health.percentile_ms(histogram({10: 100}, 46), 0.99), 12.0)
        spread = histogram({10: 98, 40: 2}, 46)
        self.assertEqual(health.percentile_ms(spread, 0.99), 42.0)
        self.assertEqual(health.percentile_ms(spread, 0.5), 12.0)

    def test_never_past_the_longest_window_that_caught_it(self) -> None:
        self.assertEqual(health.percentile_ms(histogram({46: 30}, 46), 0.99), 46.0)

    def test_empty(self) -> None:
        self.assertIsNone(health.percentile_ms(histogram({}, 46), 0.99))

    def test_bounces_beyond_every_bin_count_as_at_the_window(self) -> None:
        self.assertEqual(health.percentile_ms(histogram({10: 10}, 46, overflow=10), 0.99), 46.0)


class SuggestTests(unittest.TestCase):
    def test_gaps_well_inside_the_window_suggest_nothing(self) -> None:
        self.assertEqual(health.suggest(Button.LEFT, histogram({10: 50, 20: 10}, 46), 46), (None, False))

    def test_gaps_crowding_the_window_suggest_raising_it(self) -> None:
        # p99 at 42 ms of a 46 ms window (over 80%): 42 / 0.7 = 60.
        suggestion, near = health.suggest(Button.LEFT, histogram({10: 70, 40: 30}, 46), 46)
        self.assertTrue(near)
        self.assertEqual(suggestion, Suggestion(Button.LEFT, 46, 60, suggestion.reason))
        self.assertIn("about 42 ms", suggestion.reason)
        self.assertIn("close to its 46 ms window", suggestion.reason)
        self.assertIn("Raise it to 60 ms?", suggestion.reason)

    def test_never_above_half_the_fastest_double_click(self) -> None:
        crowded = histogram({10: 70, 40: 30}, 46)
        suggestion, _near = health.suggest(Button.LEFT, crowded, 46, fastest_double_click_ms=110)
        self.assertEqual(suggestion.to_ms, 55)
        suggestion, near = health.suggest(Button.LEFT, crowded, 46, fastest_double_click_ms=90)
        self.assertIsNone(suggestion, "45 ms is no raise")
        self.assertTrue(near, "but the bounces are close: Test and Tune can check")

    def test_with_no_double_click_measured_the_cap_is_60(self) -> None:
        suggestion, _near = health.suggest(Button.LEFT, histogram({10: 50, 54: 50}, 56), 56)
        self.assertEqual(suggestion.to_ms, health.UNKNOWN_DOUBLE_CLICK_CAP_MS)
        suggestion, near = health.suggest(Button.LEFT, histogram({10: 50, 58: 50}, 60), 60)
        self.assertIsNone(suggestion)
        self.assertTrue(near)

    def test_never_lowers(self) -> None:
        # A window longer than the cap already: nothing to suggest, ever lower.
        suggestion, _near = health.suggest(Button.LEFT, histogram({70: 40}, 80), 80, fastest_double_click_ms=120)
        self.assertIsNone(suggestion)

    def test_too_few_bounces_say_nothing(self) -> None:
        few = histogram({44: health.SUGGEST_MIN_BOUNCES - 1}, 46)
        self.assertEqual(health.suggest(Button.LEFT, few, 46), (None, False))

    def test_gaps_past_a_window_made_shorter(self) -> None:
        # Caught under a 60 ms window, the window is 46 now.
        suggestion, _near = health.suggest(Button.RIGHT, histogram({50: 40}, 60), 46, fastest_double_click_ms=200)
        self.assertEqual((suggestion.from_ms, suggestion.to_ms), (46, 75))
        self.assertIn("past its 46 ms window", suggestion.reason)
        self.assertIn("right button", suggestion.reason)

    def test_window_cap(self) -> None:
        self.assertEqual(health.window_cap(None), 60)
        self.assertEqual(health.window_cap(101), 50)
        self.assertEqual(health.window_cap(2000), 200)


class WearCase(unittest.TestCase):
    def setUp(self) -> None:
        directory = Path(tempfile.mkdtemp())
        patcher = mock.patch.object(settings, "config_dir", return_value=directory)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.clock = Clock()
        self.wear = WearHistory(clock=self.clock)

    def note(self, button=Button.LEFT, presses=0, bounces=(), window=46, dropouts=0) -> None:
        for _ in range(presses):
            self.wear.note_event(ClickEvent(button, True, True, 300.0, None), window)
        for gap in bounces:
            self.wear.note_event(ClickEvent(button, True, False, float(gap), None), window)
        for _ in range(dropouts):
            self.wear.note_event(ClickEvent(button, True, False, 6.0, None, cancels_held=True), window)

    def assess(self, buttons=(Button.LEFT,), thresholds=None, found=(), doubles=None, watching=True):
        thresholds = thresholds or {button: 46 for button in Button}
        return health.assess(self.wear, buttons, thresholds, found, doubles, watching)


class AssessTests(WearCase):
    def test_bounces_caught(self) -> None:
        self.note(presses=200, bounces=[8] * 14)
        result = self.assess()
        self.assertEqual(result.kind, health.BOUNCING)
        self.assertEqual(result.verdict, "Your left button double-clicks on its own: caught 14 of 14")
        self.assertEqual(result.detail, "Over the last 30 days the filter ignored every extra click.")
        self.assertIsNone(result.suggestion)

    def test_several_buttons_and_dropouts(self) -> None:
        self.note(Button.LEFT, presses=50, bounces=[8, 9])
        self.note(Button.RIGHT, presses=50, dropouts=1)
        result = self.assess(buttons=(Button.LEFT, Button.RIGHT, Button.MIDDLE))
        self.assertEqual(result.verdict, "Your left and right buttons double-click on their own: caught 3 of 3")
        self.assertTrue(result.detail.startswith("Over the last 30 days the filter ignored every extra click."))

    def test_one_bounce(self) -> None:
        self.note(presses=5, bounces=[8])
        result = self.assess()
        self.assertEqual(result.verdict, "Your left button double-clicks on its own: caught 1 of 1")
        self.assertEqual(result.detail, "Over the last 30 days the filter ignored the one extra click.")

    def test_only_filtered_buttons_count(self) -> None:
        self.note(Button.RIGHT, presses=50, bounces=[8, 9])
        self.note(Button.LEFT, presses=50)
        self.assertEqual(self.assess(buttons=(Button.LEFT,)).kind, health.QUIET)

    def test_a_crowded_window_brings_a_suggestion(self) -> None:
        self.note(presses=300, bounces=[10] * 70 + [40] * 30)
        result = self.assess(doubles={Button.LEFT: 140})
        self.assertEqual(result.suggestion.to_ms, 60)
        self.assertIn("Raise it to 60 ms?", result.detail)

    def test_crowded_but_capped_says_test_and_tune_can_check(self) -> None:
        self.note(presses=300, bounces=[10] * 70 + [40] * 30)
        result = self.assess(doubles={"left": 90})
        self.assertIsNone(result.suggestion)
        self.assertIn("Test and Tune can check", result.detail)

    def test_the_biggest_raise_is_the_suggestion(self) -> None:
        self.note(Button.LEFT, presses=300, bounces=[10] * 70 + [40] * 30)
        self.note(Button.RIGHT, presses=300, bounces=[10] * 50 + [44] * 50)
        result = self.assess(buttons=(Button.LEFT, Button.RIGHT), doubles={Button.LEFT: 200, Button.RIGHT: 200})
        self.assertEqual(result.suggestion.button, Button.RIGHT)

    def test_getting_worse_is_said(self) -> None:
        for _day in range(15):
            self.note(presses=30, bounces=[8])
            self.clock.now += 86400
        for _day in range(15):
            self.note(presses=30, bounces=[8] * 6)
            self.clock.now += 86400
        self.clock.now -= 86400
        result = self.assess()
        self.assertIn("bouncing more often than two weeks ago", result.detail)

    def test_another_app_when_no_bounce(self) -> None:
        self.note(presses=100)
        found = [Conflict("Logi Options+", ["side buttons", "wheel"], "logi-options-plus")]
        result = self.assess(found=found)
        self.assertEqual(result.kind, health.CONFLICT)
        self.assertEqual(result.verdict, "Another app may be changing clicks: Logi Options+")
        self.assertTrue(result.detail.startswith("Logi Options+ may take over the side buttons and the wheel."))

    def test_bounces_win_over_another_app_which_is_still_named(self) -> None:
        self.note(presses=100, bounces=[8])
        result = self.assess(found=[Conflict("SteerMouse", ["wheel"])])
        self.assertEqual(result.kind, health.BOUNCING)
        self.assertTrue(result.detail.endswith("SteerMouse is running too and may change clicks as well."))
        result = self.assess(found=[Conflict("SteerMouse", ["wheel"]), Conflict("LinearMouse", ["clicks"])])
        self.assertTrue(result.detail.endswith("SteerMouse and LinearMouse are running too and may change clicks as well."))

    def test_a_raise_too_small_to_matter_isnt_suggested(self) -> None:
        # 59 ms (calibrated in 1.0, no double-click on record): the cap of
        # 60 ms leaves 1 ms, which is no real room.
        self.note(presses=300, bounces=[10] * 70 + [50] * 30, window=59)
        result = self.assess(thresholds={button: 59 for button in Button})
        self.assertIsNone(result.suggestion)
        self.assertIn("Test and Tune can check", result.detail)
        self.assertEqual(health.crowded(self.wear, [Button.LEFT], {Button.LEFT: 59}), [Button.LEFT])
        self.assertEqual(health.crowded(self.wear, [Button.RIGHT], {Button.RIGHT: 59}), [])

    def test_no_bounce_seen(self) -> None:
        self.note(presses=100)
        result = self.assess()
        self.assertEqual((result.kind, result.verdict), (health.QUIET, "No bounce seen"))
        self.assertEqual(result.detail, "Your mouse may be fine, or it bounces only now and then.")

    def test_nothing_counted_yet(self) -> None:
        self.assertEqual(self.assess(watching=True).kind, health.WAITING)
        self.assertIn("Turn on Bounce Filter", self.assess(watching=False).detail)

    def test_older_than_thirty_days_doesnt_count(self) -> None:
        self.note(presses=50, bounces=[8])
        self.clock.now += 31 * 86400
        self.note(presses=50)
        self.assertEqual(self.assess().kind, health.QUIET)


class TodaySentenceTests(unittest.TestCase):
    def test_the_designs_sentences(self) -> None:
        self.assertEqual(health.today_sentence({Button.LEFT: 14}, 1981),
                         "Your left button bounced 14 times today. Every bounce was caught.")
        self.assertEqual(health.today_sentence({Button.LEFT: 0}, 0), "No extra clicks seen yet.")

    def test_one_two_and_several_buttons(self) -> None:
        self.assertEqual(health.today_sentence({Button.LEFT: 1}, 5),
                         "Your left button bounced once today. The bounce was caught.")
        self.assertEqual(health.today_sentence({Button.RIGHT: 2}, 5),
                         "Your right button bounced twice today. Every bounce was caught.")
        self.assertEqual(health.today_sentence({Button.RIGHT: 6, Button.LEFT: 14}, 5),
                         "Your left and right buttons bounced 20 times today. Every bounce was caught.")
        self.assertEqual(health.today_sentence({"left": 1500}, 5),
                         "Your left button bounced 1,500 times today. Every bounce was caught.")

    def test_a_quiet_day(self) -> None:
        self.assertEqual(health.today_sentence({Button.LEFT: 0}, 2298), "No extra clicks today. 2,298 caught so far.")

    def test_bounces_close_to_the_window_arent_called_all_caught(self) -> None:
        self.assertEqual(health.today_sentence({Button.LEFT: 14}, 30, crowded=[Button.LEFT]),
                         "Your left button bounced 14 times today. Some came close to getting past: see History.")
        # Only the buttons that bounced today count.
        self.assertEqual(health.today_sentence({Button.LEFT: 14, Button.RIGHT: 0}, 30, crowded=["right"]),
                         "Your left button bounced 14 times today. Every bounce was caught.")


class ControllerHealthTests(helpers.ControllerCase):
    def bounces(self, button=Button.LEFT, gaps=(8,), presses=0) -> None:
        window = self.controller.threshold_for(button)
        for _ in range(presses):
            self.controller.wear.note_event(ClickEvent(button, True, True, 300.0, None), window)
        for gap in gaps:
            self.controller.wear.note_event(ClickEvent(button, True, False, float(gap), None), window)

    def test_health_reads_the_controllers_wear_and_utilities(self) -> None:
        self.controller.set_active(True)
        self.assertEqual(self.controller.health().kind, health.WAITING)
        self.bounces(presses=20, gaps=())
        self.found = [Conflict("BetterMouse", ["wheel"], "bettermouse")]
        self.controller.refresh_conflicts()
        self.assertEqual(self.controller.health().kind, health.CONFLICT)
        self.bounces(gaps=(8, 9))
        self.assertEqual(self.controller.health().verdict, "Your left button double-clicks on its own: caught 2 of 2")

    def test_the_measured_double_click_caps_the_suggestion(self) -> None:
        self.bounces(presses=300, gaps=[10] * 70 + [40] * 30)
        self.assertEqual(self.controller.health().suggestion.to_ms, 60)
        self.controller.note_double_click_speed(Button.LEFT, 110.4)
        self.assertEqual(self.controller.double_click_speeds(), {Button.LEFT: 110})
        self.assertEqual(self.controller.health().suggestion.to_ms, 55)

    def test_applying_a_suggestion_in_automatic(self) -> None:
        controller = self.controller
        controller.set_active(True)
        self.bounces(presses=300, gaps=[10] * 70 + [40] * 30)
        suggestion = controller.health().suggestion
        controller.apply_suggestion(suggestion)
        self.assertEqual(controller.threshold_for(Button.LEFT), 60)
        self.assertEqual(controller.strength, "automatic", "still the app's choice")
        self.assertEqual(controller.automatic_window(Button.LEFT), 60)
        self.assertEqual(helpers.FakeFilter.instances[0].config.thresholds[Button.LEFT], 60, "in effect at once")
        self.assertEqual(settings.load()["thresholds"]["left"], 60)

    def test_applying_a_suggestion_in_custom(self) -> None:
        controller = self.controller
        controller.set_strength("custom")
        controller.set_threshold(Button.LEFT, 50)
        controller.apply_suggestion(Suggestion(Button.LEFT, 50, 58, "r"))
        self.assertEqual(controller.threshold_for(Button.LEFT), 58)
        self.assertEqual(controller.custom_window(Button.LEFT), 58)
        self.assertEqual(controller.automatic_window(Button.LEFT), 46, "Automatic's is its own")

    def test_a_suggestion_never_lowers(self) -> None:
        self.controller.set_strength("custom")
        self.controller.set_threshold(Button.LEFT, 70)
        self.controller.apply_suggestion(Suggestion(Button.LEFT, 46, 60, "stale"))
        self.assertEqual(self.controller.threshold_for(Button.LEFT), 70)


class StatusSentenceTests(helpers.ControllerCase):
    def test_off(self) -> None:
        self.assertEqual(self.controller.status_sentence(), "Off.")

    def test_on_with_todays_bounces(self) -> None:
        controller = self.controller
        controller.set_active(True)
        self.assertEqual(controller.status_sentence(), "No extra clicks seen yet.")
        for _ in range(14):
            controller.wear.note_event(ClickEvent(Button.LEFT, True, False, 8.0, None), 46)
        self.assertEqual(controller.status_sentence(), "Your left button bounced 14 times today. Every bounce was caught.")

    def test_bounces_crowding_the_window_are_said_to(self) -> None:
        controller = self.controller
        controller.set_active(True)
        for gap in [10] * 14 + [44] * 10:
            controller.wear.note_event(ClickEvent(Button.LEFT, True, False, float(gap), None), 46)
        self.assertEqual(
            controller.status_sentence(),
            "Your left button bounced 24 times today. Some came close to getting past: see History.",
        )

    def test_a_button_not_filtered_isnt_counted(self) -> None:
        controller = self.controller
        controller.set_active(True)
        controller.wear.note_event(ClickEvent(Button.RIGHT, True, False, 8.0, None), 46)
        self.assertEqual(controller.status_sentence(), "No extra clicks seen yet.")

    def test_waiting_failing_and_calibrating(self) -> None:
        controller = self.controller
        controller.set_waiting_for_permission(True)
        self.assertTrue(controller.status_sentence().startswith("Waiting for "))
        controller.set_waiting_for_permission(False)
        helpers.FakeFilter.fail_with = "busy"
        controller._store(fix_enabled=True)
        controller.set_active(True)
        self.assertEqual(controller.status_sentence(), "Couldn’t start the filter.")
        helpers.FakeFilter.fail_with = None
        controller.set_active(True)
        controller.suspend()
        self.assertEqual(controller.status_sentence(), "Paused while you test your mouse.")

    def test_on_but_stopped_for_now(self) -> None:
        self.controller.set_active(True)
        self.controller.stop_keeping_choice()
        self.assertEqual(self.controller.status_sentence(), "Not filtering right now.")

    def test_not_supported(self) -> None:
        with mock.patch("app.controller.is_supported", return_value=False):
            self.assertEqual(self.controller.status_sentence(), "Not available on this system.")


if __name__ == "__main__":
    unittest.main()
