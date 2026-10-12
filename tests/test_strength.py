"""Strength: Automatic (the app picks each window) or Custom (the user's),
and how 1.0 settings become one or the other without a window changing."""

try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

try:
    import _trust_helpers as helpers
except ImportError:
    from tests import _trust_helpers as helpers

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import settings
from app.core import DEFAULT_THRESHOLD_MS, Button

#: The owner's settings as 1.0.1 saved them (10 Oct 2026; private parts out):
#: left and right calibrated and filtered, the rest 59 from 0.x.
OWNER_1_0_1 = {
    "version": 3,
    "thresholds": {"left": 59, "right": 25, "middle": 59, "back": 59, "forward": 59},
    "fix_enabled": True,
    "buttons": ["left", "right"],
    "calibrated_buttons": ["left", "right"],
    "wheel_fix": False,
    "wheel_window_ms": 50,
    "excluded_apps": [],
    "ignored_devices": [],
    "threshold_ms": 59,
    "calibrated": True,
    "start_at_login": True,
    "start_minimized": False,
    "filtered_total": 2298,
    "window_geometry": "",
    "auto_update": True,
    "last_update_check": 1791624357.64769,
    "pending_update": "",
    "update_attempt_version": "",
    "update_attempt_count": 0,
    "tray_hint_shown": False,
    "auto_check": True,
}


class MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp())
        for target, value in (("config_dir", mock.Mock(return_value=self.directory)),
                              ("LEGACY_PATH", self.directory / "absent.json")):
            patcher = mock.patch.object(settings, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def write(self, values) -> None:
        (self.directory / "settings.json").write_text(json.dumps(values))

    def stored(self) -> dict:
        return json.loads((self.directory / "settings.json").read_text())

    def test_the_owners_settings_are_automatic_with_every_window_kept(self) -> None:
        self.write(OWNER_1_0_1)
        values = settings.load()
        self.assertEqual(values["strength"], "automatic")
        self.assertEqual(values["thresholds"], OWNER_1_0_1["thresholds"], "no window moves")
        self.assertEqual(values["automatic_windows"], {"left": 59, "right": 25})
        self.assertEqual(values["custom_windows"], {"middle": 59, "back": 59, "forward": 59},
                         "the old 0.x windows wait in Custom")
        for key, value in OWNER_1_0_1.items():
            if key not in ("thresholds",):
                self.assertEqual(values[key], value, key)
        self.assertFalse(values["auto_pause_games"])
        self.assertTrue(values["pause_shortcut_enabled"])
        self.assertEqual(settings.coerce(values), values, "settled")
        settings.save({"filtered_total": 2300}, current=values)
        self.assertEqual(settings.load(), {**values, "filtered_total": 2300})
        self.assertEqual(self.stored()["version"], 3, "a 1.0 copy still reads it")

    def test_a_new_install_is_automatic(self) -> None:
        values = settings.load()
        self.assertEqual((values["strength"], values["automatic_windows"], values["custom_windows"]),
                         ("automatic", {}, {}))
        self.assertEqual(values["double_click_ms"], {})

    def test_a_filtered_window_set_by_hand_is_custom(self) -> None:
        self.write({"version": 3, "thresholds": {"left": 70}, "buttons": ["left"]})
        values = settings.load()
        self.assertEqual(values["strength"], "custom")
        self.assertEqual(values["thresholds"]["left"], 70)
        self.assertEqual(values["custom_windows"], {"left": 70})

    def test_a_hand_set_window_on_a_button_not_filtered_stays_automatic(self) -> None:
        self.write({"version": 3, "thresholds": {"left": 46, "right": 80}, "buttons": ["left"]})
        values = settings.load()
        self.assertEqual(values["strength"], "automatic")
        self.assertEqual(values["thresholds"]["right"], 80, "kept")
        self.assertEqual(values["custom_windows"], {"right": 80})

    def test_a_calibrated_window_changed_by_hand_is_taken_as_measured(self) -> None:
        # 1.0 kept "calibrated" after a hand edit; the window it has is the one.
        self.write({"version": 3, "thresholds": {"left": 72}, "buttons": ["left"], "calibrated_buttons": ["left"]})
        values = settings.load()
        self.assertEqual((values["strength"], values["automatic_windows"]), ("automatic", {"left": 72}))

    def test_a_0_x_file_goes_through_both_migrations(self) -> None:
        self.write({"version": 2, "threshold_ms": 59, "calibrated": True, "buttons": ["left"]})
        values = settings.load()
        self.assertEqual(values["strength"], "automatic")
        self.assertEqual(values["thresholds"]["left"], 59)
        self.assertEqual(values["automatic_windows"], {"left": 59})

    def test_a_window_a_downgraded_copy_moved_makes_it_custom(self) -> None:
        values = settings.save({"buttons": ["left", "right"]}, current=settings.load())
        self.assertEqual(values["strength"], "automatic")
        old = self.stored()
        old["thresholds"]["right"] = 33  # 1.0 changed it, keeping the keys it doesn't know
        self.write(old)
        values = settings.load()
        self.assertEqual(values["strength"], "custom")
        self.assertEqual(values["thresholds"]["right"], 33)
        self.assertEqual(values["custom_windows"]["right"], 33)

    def test_custom_takes_in_a_window_changed_behind_its_back(self) -> None:
        values = settings.save({"strength": "custom", "custom_windows": {"left": 50},
                                "thresholds": {**settings.load()["thresholds"], "left": 50}}, current=settings.load())
        self.assertEqual(values["custom_windows"], {"left": 50})
        old = self.stored()
        old["thresholds"]["left"] = 64
        self.write(old)
        self.assertEqual(settings.load()["custom_windows"]["left"], 64)

    def test_a_downgraded_copys_calibration_becomes_measured(self) -> None:
        settings.save({"buttons": ["left", "right"]}, current=settings.load())
        old = self.stored()
        old["thresholds"]["right"] = 30
        old["calibrated_buttons"] = ["right"]
        self.write(old)
        values = settings.load()
        self.assertEqual(values["automatic_windows"], {"right": 30})
        self.assertEqual(values["strength"], "automatic")

    def test_damaged_new_values_are_repaired(self) -> None:
        self.write({"version": 3, "strength": "loud", "automatic_windows": {"left": "x", "right": 9000, "x": 5},
                    "custom_windows": [1], "double_click_ms": {"left": 0, "right": 120.6, "middle": "y"},
                    "auto_pause_games": "yes", "pause_shortcut_enabled": 0})
        values = settings.load()
        self.assertEqual(values["strength"], "automatic", "an unknown strength is worked out again")
        self.assertEqual(values["automatic_windows"], {"right": 200})
        self.assertEqual(values["double_click_ms"], {"right": 121}, "no speed of 0 ms")
        self.assertIs(values["auto_pause_games"], True)
        self.assertIs(values["pause_shortcut_enabled"], False)


class MergeTests(unittest.TestCase):
    """settings.merge: a write's meaning for the windows in effect."""

    def base(self, **changes) -> dict:
        return settings.coerce({**OWNER_1_0_1, **changes})

    def test_a_strength_switch_moves_every_window(self) -> None:
        values = settings.merge({"strength": "custom"}, self.base())
        self.assertEqual(values["thresholds"], OWNER_1_0_1["thresholds"], "Custom starts where Automatic was")
        values = settings.merge({"custom_windows": {"left": 80}}, values)
        self.assertEqual(values["thresholds"]["left"], 59, "custom_windows alone isn't a window in effect")
        values = settings.merge({"strength": "automatic"}, settings.merge({"strength": "custom"}, values))
        self.assertEqual(values["strength"], "automatic")
        self.assertEqual(
            values["thresholds"], {"left": 59, "right": 25, "middle": 46, "back": 46, "forward": 46}
        )

    def test_a_write_with_its_own_windows_is_taken_as_it_is(self) -> None:
        values = settings.merge({"thresholds": {**OWNER_1_0_1["thresholds"], "left": 61},
                                 "automatic_windows": {"left": 61, "right": 25}}, self.base())
        self.assertEqual((values["strength"], values["thresholds"]["left"]), ("automatic", 61))

    def test_unrelated_writes_change_no_window(self) -> None:
        values = settings.merge({"filtered_total": 3000}, self.base())
        self.assertEqual(values["thresholds"], OWNER_1_0_1["thresholds"])
        self.assertEqual(values["strength"], "automatic")


class ControllerStrengthTests(helpers.ControllerCase):
    def test_switching_keeps_each_ones_windows(self) -> None:
        controller = self.controller
        controller.set_strength("custom")
        controller.set_threshold(Button.LEFT, 70)
        self.assertEqual(controller.threshold_for(Button.LEFT), 70)
        controller.set_strength("automatic")
        self.assertEqual(controller.threshold_for(Button.LEFT), DEFAULT_THRESHOLD_MS)
        controller.set_strength("custom")
        self.assertEqual(controller.threshold_for(Button.LEFT), 70, "Custom's window was kept")
        self.assertEqual(settings.load()["strength"], "custom")

    def test_switching_reaches_a_running_filter_at_once(self) -> None:
        controller = self.controller
        controller.set_active(True)
        controller.set_strength("custom")
        controller.set_threshold(Button.LEFT, 70)
        controller.set_strength("automatic")
        self.assertEqual(helpers.FakeFilter.instances[0].config.thresholds[Button.LEFT], DEFAULT_THRESHOLD_MS)

    def test_an_unknown_strength_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.controller.set_strength("medium")

    def test_custom_starts_from_the_automatic_windows(self) -> None:
        controller = self.controller
        controller.apply_calibration(Button.LEFT, 62)
        controller.set_strength("custom")
        self.assertEqual(controller.threshold_for(Button.LEFT), 62)

    def test_the_1_0_calibration_steps_keep_automatic(self) -> None:
        # The 1.0 window applies calibration as set_threshold, then set_calibrated.
        controller = self.controller
        controller.set_threshold(Button.LEFT, 64)
        controller.set_calibrated(Button.LEFT, True)
        self.assertEqual(controller.strength, "automatic")
        self.assertEqual(controller.automatic_window(Button.LEFT), 64)
        self.assertTrue(controller.is_calibrated(Button.LEFT))
        controller.set_calibrated(Button.LEFT, False)
        self.assertEqual(controller.threshold_for(Button.LEFT), DEFAULT_THRESHOLD_MS, "back to the default")

    def test_apply_calibration_in_automatic(self) -> None:
        controller = self.controller
        controller.apply_calibration(Button.RIGHT, 33, fastest_double_click_ms=120)
        self.assertEqual(controller.threshold_for(Button.RIGHT), 33)
        self.assertIn(Button.RIGHT, controller.buttons, "worth measuring, so filtered")
        self.assertTrue(controller.is_calibrated(Button.RIGHT))
        self.assertEqual(controller.double_click_speeds(), {Button.RIGHT: 120})
        self.assertEqual(controller.strength, "automatic")

    def test_apply_calibration_in_custom_sets_both(self) -> None:
        controller = self.controller
        controller.set_strength("custom")
        controller.apply_calibration(Button.LEFT, 58)
        self.assertEqual((controller.custom_window(Button.LEFT), controller.automatic_window(Button.LEFT)), (58, 58))

    def test_turning_a_button_on_in_automatic_gives_it_automatics_window(self) -> None:
        # The owner's shape: an old 59 on the middle button, never in effect.
        (self.directory / "settings.json").write_text(json.dumps(OWNER_1_0_1))
        controller = self.make_controller()
        self.assertEqual(controller.strength, "automatic")
        controller.set_button_filtered(Button.MIDDLE, True)
        self.assertEqual(controller.threshold_for(Button.MIDDLE), DEFAULT_THRESHOLD_MS)
        self.assertEqual(controller.threshold_for(Button.LEFT), 59)
        self.assertEqual(controller.threshold_for(Button.RIGHT), 25)
        self.assertEqual(controller.strength, "automatic")
        controller.set_strength("custom")
        self.assertEqual(controller.threshold_for(Button.MIDDLE), 59, "the old window waits in Custom")

    def test_a_held_arrow_key_in_custom(self) -> None:
        controller = self.controller
        controller.set_strength("custom")
        for value in range(47, 60):
            controller.set_threshold(Button.LEFT, value, deferred=True)
        controller.flush_settings()
        stored = settings.load()
        self.assertEqual((stored["thresholds"]["left"], stored["custom_windows"]["left"]), (59, 59))
        self.assertEqual(stored["strength"], "custom")

    def test_writing_the_key_directly_switches_the_same_way(self) -> None:
        # The 1.1 window writes "strength" with _apply, not set_strength.
        controller = self.controller
        controller.set_active(True)
        controller._apply(strength="custom")
        controller.set_threshold(Button.LEFT, 70)
        controller._apply(strength="automatic")
        self.assertEqual(controller.strength, "automatic", "not taken for a hand-set window")
        self.assertEqual(controller.threshold_for(Button.LEFT), DEFAULT_THRESHOLD_MS)
        self.assertEqual(helpers.FakeFilter.instances[0].config.thresholds[Button.LEFT], DEFAULT_THRESHOLD_MS)
        controller._apply(strength="custom")
        self.assertEqual(controller.threshold_for(Button.LEFT), 70)
        stored = settings.load()
        self.assertEqual((stored["strength"], stored["thresholds"]["left"]), ("custom", 70))

    def test_buttons_written_directly_in_automatic_take_automatics_window(self) -> None:
        (self.directory / "settings.json").write_text(json.dumps(OWNER_1_0_1))
        controller = self.make_controller()
        controller._apply(buttons=["left", "right", "middle"])
        self.assertEqual(controller.threshold_for(Button.MIDDLE), DEFAULT_THRESHOLD_MS)
        self.assertEqual(controller.strength, "automatic")
        self.assertEqual(controller.thresholds[Button.BACK], 59, "a button left off keeps its old window")

    def test_a_suggestion_applied_in_automatic_stays_automatic(self) -> None:
        from app.health import Suggestion

        controller = self.controller
        controller.apply_suggestion(Suggestion(Button.LEFT, 46, 58, "crowded"))
        self.assertEqual((controller.strength, controller.automatic_window(Button.LEFT)), ("automatic", 58))
        controller._apply(strength="custom")
        controller._apply(strength="automatic")
        self.assertEqual(controller.threshold_for(Button.LEFT), 58, "the raised window is Automatic's own")

    def test_diagnostics_name_the_strength(self) -> None:
        self.assertEqual(self.controller.diagnostic_state()["strength"], "automatic")
        self.controller.set_strength("custom")
        self.assertEqual(self.controller.diagnostic_state()["strength"], "custom")
        self.assertIn("strength", self.controller.diagnostic_settings())


if __name__ == "__main__":
    unittest.main()
