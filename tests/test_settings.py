import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import settings
from app.core import DEFAULT_THRESHOLD_MS, Button


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
