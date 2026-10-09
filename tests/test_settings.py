try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import settings
from app.contracts import DEFAULT_THRESHOLD_MS, Button


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp())
        patcher = mock.patch.object(settings, "config_dir", return_value=self.directory)
        patcher.start()
        self.addCleanup(patcher.stop)
        legacy = mock.patch.object(settings, "LEGACY_PATH", self.directory / "absent.json")
        legacy.start()
        self.addCleanup(legacy.stop)

    def test_defaults_when_nothing_is_stored(self) -> None:
        values = settings.load()
        self.assertEqual(values["threshold_ms"], DEFAULT_THRESHOLD_MS)
        self.assertEqual(values["buttons"], [Button.LEFT.value])
        self.assertFalse(values["fix_enabled"])

    def test_save_round_trip(self) -> None:
        settings.save({"threshold_ms": 45, "fix_enabled": True})
        self.assertEqual(settings.load()["threshold_ms"], 45)
        self.assertTrue(settings.load()["fix_enabled"])

    def test_save_merges_rather_than_replaces(self) -> None:
        settings.save({"threshold_ms": 45})
        settings.save({"fix_enabled": True})
        values = settings.load()
        self.assertEqual(values["threshold_ms"], 45)
        self.assertTrue(values["fix_enabled"])

    def test_out_of_range_values_are_repaired(self) -> None:
        (self.directory / "settings.json").write_text(
            json.dumps({"threshold_ms": 9000, "buttons": ["left", "nonsense"], "filtered_total": -4})
        )
        values = settings.load()
        self.assertLessEqual(values["threshold_ms"], 200)
        self.assertEqual(values["buttons"], ["left"])
        self.assertEqual(values["filtered_total"], 0)

    def test_empty_button_list_falls_back_to_left(self) -> None:
        self.assertEqual(settings.save({"buttons": []})["buttons"], ["left"])

    def test_corrupt_file_does_not_crash(self) -> None:
        (self.directory / "settings.json").write_text("{not json")
        self.assertEqual(settings.load()["threshold_ms"], DEFAULT_THRESHOLD_MS)

    def test_legacy_file_is_migrated_without_its_threshold(self) -> None:
        legacy = self.directory / "legacy.json"
        legacy.write_text(json.dumps({"bounce_threshold_ms": 80, "start_at_login": True, "fix_enabled": True}))
        with mock.patch.object(settings, "LEGACY_PATH", legacy):
            values = settings.load()
        # Version 1 measured press-to-press, so its number means something else.
        self.assertEqual(values["threshold_ms"], DEFAULT_THRESHOLD_MS)
        self.assertTrue(values["start_at_login"])
        self.assertFalse(values["fix_enabled"], "the fix stays off until the user asks again")
        self.assertFalse(values["calibrated"])

    def write(self, values: dict) -> None:
        (self.directory / "settings.json").write_text(json.dumps(values))

    def test_an_old_opt_out_of_updates_stops_the_checks_too(self) -> None:
        # Before 1.0, turning "auto_update" off also stopped the checks.
        self.write({"auto_update": False, "threshold_ms": 45})
        values = settings.load()
        self.assertFalse(values["auto_check"])
        self.assertFalse(values["auto_update"])
        settings.save({"auto_update": True}, current=values)
        self.assertFalse(settings.load()["auto_check"], "kept once saved, whatever happens to installs")

    def test_update_checks_stay_on_for_everyone_else(self) -> None:
        self.assertTrue(settings.load()["auto_check"], "a new install")
        self.write({"auto_update": True})
        self.assertTrue(settings.load()["auto_check"])

    def test_a_saved_check_choice_is_its_own(self) -> None:
        self.write({"auto_update": False, "auto_check": True})
        self.assertTrue(settings.load()["auto_check"])
        self.write({"auto_update": True, "auto_check": 0})
        self.assertIs(settings.load()["auto_check"], False)

    def test_update_attempts_are_typed(self) -> None:
        self.write({"update_attempt_version": 5, "update_attempt_count": "2"})
        values = settings.load()
        self.assertEqual(values["update_attempt_version"], "")
        self.assertEqual(values["update_attempt_count"], 2)
        for bad in (-3, "many", None, float("inf")):
            self.assertEqual(settings.coerce({"update_attempt_count": bad})["update_attempt_count"], 0, bad)

    def test_buttons_from_returns_enum_members(self) -> None:
        values = settings.save({"buttons": ["left", "middle"]})
        self.assertEqual(settings.buttons_from(values), [Button.LEFT, Button.MIDDLE])


#: The owner's own settings.json from 0.5.3, as it was on 9 October 2026.
OWNER_0_5_3 = """{
  "version": 2,
  "threshold_ms": 59,
  "fix_enabled": true,
  "buttons": [
    "left"
  ],
  "start_at_login": true,
  "start_minimized": false,
  "calibrated": true,
  "filtered_total": 529,
  "window_geometry": "",
  "auto_update": true,
  "last_update_check": 1791560448.171617,
  "pending_update": "",
  "tray_hint_shown": false
}"""


def coerce_as_0_5_3(values: dict) -> dict:
    """What a 0.5.3 copy makes of a settings file after a downgrade: its
    coerce(), cut to the keys this cares about. It merges onto its defaults,
    keeping keys it doesn't know, and knows three buttons."""
    merged = {"threshold_ms": 60, "buttons": ["left"], "calibrated": False, **values}
    try:
        merged["threshold_ms"] = max(5, min(200, int(round(float(merged["threshold_ms"])))))
    except (TypeError, ValueError, OverflowError):
        merged["threshold_ms"] = 60
    merged["buttons"] = [name for name in merged["buttons"] if name in ("left", "right", "middle")] or ["left"]
    merged["calibrated"] = bool(merged["calibrated"])
    merged["version"] = 2
    return merged


class MigrationTests(unittest.TestCase):
    """Schema 3 (1.0): a window per button, and what becomes of older files."""

    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp())
        for target, value in (("config_dir", mock.Mock(return_value=self.directory)),
                              ("LEGACY_PATH", self.directory / "absent.json")):
            patcher = mock.patch.object(settings, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def write(self, values) -> None:
        text = values if isinstance(values, str) else json.dumps(values)
        (self.directory / "settings.json").write_text(text)

    def stored(self) -> dict:
        return json.loads((self.directory / "settings.json").read_text())

    def test_the_owners_settings_keep_their_calibrated_window(self) -> None:
        self.write(OWNER_0_5_3)
        values = settings.load()
        self.assertEqual(values["version"], settings.SCHEMA_VERSION)
        self.assertEqual(values["thresholds"]["left"], 59)
        self.assertEqual(values["calibrated_buttons"], ["left"])
        self.assertEqual(set(values["thresholds"].values()), {59}, "every button took the one window")
        self.assertEqual(values["buttons"], ["left"], "side buttons stay off")
        # Everything else is as it was.
        original = json.loads(OWNER_0_5_3)
        for key in ("fix_enabled", "start_at_login", "start_minimized", "filtered_total",
                    "window_geometry", "auto_update", "last_update_check", "pending_update", "tray_hint_shown"):
            self.assertEqual(values[key], original[key], key)
        self.assertTrue(values["auto_check"], "updates were on, so checks stay on")
        self.assertEqual((values["threshold_ms"], values["calibrated"]), (59, True), "kept for a downgrade")
        self.assertFalse(values["wheel_fix"])
        self.assertEqual((values["excluded_apps"], values["ignored_devices"]), ([], []))

    def test_the_owners_settings_survive_a_save_and_a_second_load(self) -> None:
        self.write(OWNER_0_5_3)
        first = settings.load()
        settings.save({"filtered_total": 530}, current=first)
        stored = self.stored()
        self.assertEqual(stored["thresholds"]["left"], 59)
        self.assertEqual(stored["calibrated_buttons"], ["left"])
        self.assertEqual(stored["version"], 3)
        second = settings.load()
        self.assertEqual({**first, "filtered_total": 530}, second)
        self.assertEqual(settings.migrate(second), second, "a 1.0 file is left as it is")
        self.assertEqual(settings.coerce(second), second)

    def test_a_downgraded_copy_reads_the_left_buttons_window(self) -> None:
        self.write(OWNER_0_5_3)
        values = settings.load()
        settings.save({"thresholds": {**values["thresholds"], "right": 30}, "buttons": ["left", "back"]}, current=values)
        old = coerce_as_0_5_3(self.stored())
        self.assertEqual(old["threshold_ms"], 59)
        self.assertTrue(old["calibrated"])
        self.assertEqual(old["buttons"], ["left"], "it knows nothing of back, and drops it")
        values = settings.save({"thresholds": {**settings.load()["thresholds"], "left": 72},
                                "calibrated_buttons": ["right"]}, current=settings.load())
        old = coerce_as_0_5_3(self.stored())
        self.assertEqual(old["threshold_ms"], 72, "follows the left button's window")
        self.assertFalse(old["calibrated"], "and its calibration")

    def test_upgrading_again_after_a_downgrade_keeps_both_copies_work(self) -> None:
        settings.save({"thresholds": {"left": 59, "right": 30}, "calibrated_buttons": ["right"],
                       "buttons": ["left", "right", "back"]}, current=settings.load())
        # The old copy moves its slider to 70 and saves, keeping the keys it
        # doesn't know (version 2 again).
        old = coerce_as_0_5_3(self.stored())
        old.update(threshold_ms=70, calibrated=True)
        self.write(old)
        values = settings.load()
        self.assertEqual(values["thresholds"]["left"], 70, "the old copy's window was the left button's")
        self.assertEqual(values["thresholds"]["right"], 30, "1.0's own windows are kept")
        self.assertEqual(values["calibrated_buttons"], ["left", "right"])
        self.assertEqual(values["buttons"], ["left", "right"], "back was dropped by the old copy")

    def test_an_uncalibrated_install_at_the_old_default_takes_the_new_one(self) -> None:
        self.write({"version": 2, "threshold_ms": 60, "calibrated": False, "buttons": ["left", "right"]})
        values = settings.load()
        self.assertEqual(values["thresholds"], {button.value: 46 for button in Button})
        self.assertEqual(values["calibrated_buttons"], [])
        self.assertEqual(values["buttons"], ["left", "right"])

    def test_a_window_moved_by_hand_is_kept(self) -> None:
        self.write({"version": 2, "threshold_ms": 80, "calibrated": False})
        self.assertEqual(settings.load()["thresholds"]["middle"], 80)

    def test_a_calibrated_window_of_60_is_kept(self) -> None:
        self.write({"version": 2, "threshold_ms": 60, "calibrated": True})
        values = settings.load()
        self.assertEqual(values["thresholds"]["left"], 60)
        self.assertEqual(values["calibrated_buttons"], ["left"])

    def test_a_new_install(self) -> None:
        values = settings.load()
        self.assertEqual(DEFAULT_THRESHOLD_MS, 46)
        self.assertEqual(values["thresholds"], {button.value: 46 for button in Button})
        self.assertEqual(values["buttons"], ["left"])
        self.assertEqual(values["calibrated_buttons"], [])
        self.assertFalse(values["wheel_fix"])
        from app.contracts import WHEEL_DEFAULT_MS

        self.assertEqual(values["wheel_window_ms"], WHEEL_DEFAULT_MS)

    def test_damaged_values_are_repaired(self) -> None:
        self.write({
            "version": 3,
            "thresholds": {"left": "45", "right": None, "middle": True, "back": float("inf"), "forward": 9000, "x9": 3},
            "buttons": ["forward", "back", "left", "left", "nonsense"],
            "calibrated_buttons": "left",
            "wheel_fix": 1,
            "wheel_window_ms": 2,
            "excluded_apps": [{"key": "cs2.exe", "name": "CS2"}, {"key": "cs2.exe", "name": "again"},
                              {"key": ""}, 7, "com.valvesoftware.steam", {"key": "x.exe", "name": 5}],
            "ignored_devices": "not a list",
        })  # json writes infinity as Infinity, which it reads back
        values = settings.load()
        self.assertEqual(values["thresholds"], {"left": 45, "right": 46, "middle": 46, "back": 46, "forward": 200})
        self.assertEqual(values["buttons"], ["left", "back", "forward"], "known, once, in order")
        self.assertEqual(values["calibrated_buttons"], [])
        self.assertIs(values["wheel_fix"], True)
        self.assertEqual(values["wheel_window_ms"], settings.WHEEL_MIN_MS)
        self.assertEqual(values["excluded_apps"], [
            {"key": "cs2.exe", "name": "CS2"},
            {"key": "com.valvesoftware.steam", "name": "com.valvesoftware.steam"},
            {"key": "x.exe", "name": "x.exe"},
        ])
        self.assertEqual(values["ignored_devices"], [])

    def test_writing_the_old_keys_alone_sets_the_left_button(self) -> None:
        values = settings.save({"thresholds": {"right": 30}}, current=settings.load())
        values = settings.save({"threshold_ms": 52, "calibrated": True}, current=values)
        self.assertEqual(values["thresholds"]["left"], 52)
        self.assertEqual(values["thresholds"]["right"], 30)
        self.assertEqual(values["calibrated_buttons"], ["left"])
        values = settings.save({"calibrated": False}, current=values)
        self.assertEqual(values["calibrated_buttons"], [])

    def test_buttons_and_thresholds_come_back_as_buttons(self) -> None:
        values = settings.save({"buttons": ["back", "left"], "thresholds": {"back": 20}}, current=settings.load())
        self.assertEqual(settings.buttons_from(values), [Button.LEFT, Button.BACK])
        self.assertEqual(settings.thresholds_from(values)[Button.BACK], 20)


class DurabilityTests(unittest.TestCase):
    """A save must never turn a passing read failure into reset settings."""

    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp())
        for target, value in (("config_dir", mock.Mock(return_value=self.directory)),
                              ("LEGACY_PATH", self.directory / "absent.json")):
            patcher = mock.patch.object(settings, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.good = {"threshold_ms": 120, "fix_enabled": True, "buttons": ["left", "right"],
                     "calibrated": True, "start_at_login": True}
        settings.save(self.good, current={})

    def test_save_merges_onto_what_the_app_holds_without_reading(self) -> None:
        held = settings.load()
        with mock.patch.object(Path, "read_bytes", side_effect=PermissionError(32, "locked")):
            settings.save({"filtered_total": 905}, current=held)
        values = settings.load()
        self.assertEqual(values["threshold_ms"], 120)
        self.assertTrue(values["fix_enabled"])
        self.assertEqual(values["buttons"], ["left", "right"])
        self.assertEqual(values["filtered_total"], 905)

    def test_an_unreadable_file_is_not_taken_for_an_empty_one(self) -> None:
        with mock.patch.object(Path, "read_bytes", side_effect=PermissionError(32, "locked")):
            with self.assertRaises(OSError):
                settings.save({"filtered_total": 905})
        self.assertEqual(settings.load()["threshold_ms"], 120, "nothing was overwritten")

    def test_controller_keeps_its_settings_through_a_failed_read(self) -> None:
        from app.controller import AppController

        controller = AppController()
        with mock.patch.object(Path, "read_bytes", side_effect=PermissionError(32, "locked")):
            controller._store(filtered_total=905)
        self.assertEqual(controller.settings["threshold_ms"], 120)
        self.assertTrue(controller.settings["calibrated"])
        self.assertEqual(settings.load()["threshold_ms"], 120)

    def test_the_new_file_is_flushed_to_disk_before_it_replaces_the_old(self) -> None:
        order = []
        real_fsync, real_replace = settings.os.fsync, Path.replace

        def fsync(descriptor):
            order.append("fsync")
            real_fsync(descriptor)

        def replace(path, target):
            order.append("replace")
            return real_replace(path, target)

        with mock.patch.object(settings.os, "fsync", side_effect=fsync), \
                mock.patch.object(Path, "replace", replace):
            settings.save({"threshold_ms": 45}, current=settings.load())
        self.assertEqual(order, ["fsync", "replace"])

    def test_a_damaged_file_falls_back_to_the_previous_one(self) -> None:
        settings.save({"threshold_ms": 90}, current=settings.load())  # keeps the 120 file as the spare
        (self.directory / "settings.json").write_bytes(b"\x00" * 64)  # what a power cut can leave
        values = settings.load()
        self.assertEqual(values["threshold_ms"], 120)
        self.assertTrue(values["calibrated"])

    def test_a_damaged_file_never_becomes_the_spare(self) -> None:
        settings.save({"threshold_ms": 90}, current=settings.load())
        (self.directory / "settings.json").write_text("{cut sho")
        held = settings.load()
        settings.save({"filtered_total": 3}, current=held)
        self.assertEqual(json.loads((self.directory / "settings.json.bak").read_text())["threshold_ms"], 120)

    def test_deleting_the_file_still_starts_from_defaults(self) -> None:
        settings.save({"threshold_ms": 90}, current=settings.load())
        (self.directory / "settings.json").unlink()
        self.assertEqual(settings.load()["threshold_ms"], DEFAULT_THRESHOLD_MS)
        settings.save({"filtered_total": 1}, current=settings.load())
        self.assertFalse((self.directory / "settings.json.bak").exists(), "the old copy is gone too")

    def test_a_briefly_locked_file_is_read_on_a_later_try(self) -> None:
        real = Path.read_bytes
        failures = [PermissionError(32, "locked")]

        def read_bytes(path):
            if failures and path.name == "settings.json":
                raise failures.pop()
            return real(path)

        with mock.patch.object(Path, "read_bytes", read_bytes), mock.patch.object(settings, "sleep"):
            self.assertEqual(settings.load()["threshold_ms"], 120)


if __name__ == "__main__":
    unittest.main()
