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

    def test_buttons_from_returns_enum_members(self) -> None:
        values = settings.save({"buttons": ["left", "middle"]})
        self.assertEqual(settings.buttons_from(values), [Button.LEFT, Button.MIDDLE])


if __name__ == "__main__":
    unittest.main()
