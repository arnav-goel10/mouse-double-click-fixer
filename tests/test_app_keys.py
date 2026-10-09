"""App keys for the exclusion list: from a chosen file, and the running apps."""

try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

import platform
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import app_keys
from app.app_keys import AppChoice


class AppKeyTests(unittest.TestCase):
    def bundle(self, info: dict | None, name: str = "Counter-Strike 2.app") -> str:
        folder = Path(tempfile.mkdtemp()) / name
        (folder / "Contents").mkdir(parents=True)
        if info is not None:
            with open(folder / "Contents" / "Info.plist", "wb") as handle:
                plistlib.dump(info, handle)
        return str(folder)

    def test_a_windows_program_is_its_lower_cased_file_name(self) -> None:
        self.assertEqual(app_keys.from_exe("C:\\Program Files\\Steam\\Games\\CS2.exe"), AppChoice("cs2.exe", "CS2"))
        self.assertEqual(app_keys.windows_key("D:/Games/Valorant.EXE"), "valorant.exe")
        self.assertIsNone(app_keys.from_exe(""))

    def test_a_mac_app_is_its_bundle_identifier(self) -> None:
        path = self.bundle({"CFBundleIdentifier": "com.valvesoftware.cs2", "CFBundleName": "CS2",
                            "CFBundleDisplayName": "Counter-Strike 2", "CFBundleExecutable": "cs2_osx"})
        self.assertEqual(app_keys.from_bundle(path), AppChoice("com.valvesoftware.cs2", "Counter-Strike 2"))

    def test_without_an_identifier_it_is_the_executables_name(self) -> None:
        path = self.bundle({"CFBundleExecutable": "game_bin", "CFBundleName": "Game"})
        self.assertEqual(app_keys.from_bundle(path), AppChoice("game_bin", "Game"))
        path = self.bundle(None, "Odd Tool.app")
        self.assertEqual(app_keys.from_bundle(path), AppChoice("Odd Tool", "Odd Tool"))

    def test_a_damaged_info_plist_falls_back_to_the_name(self) -> None:
        path = self.bundle(None, "Broken.app")
        (Path(path) / "Contents" / "Info.plist").write_bytes(b"\x00garbage")
        self.assertEqual(app_keys.from_bundle(path), AppChoice("Broken", "Broken"))

    def test_the_dialogs_answer_on_each_platform(self) -> None:
        with mock.patch.object(app_keys.platform, "system", return_value="Windows"):
            self.assertEqual(app_keys.from_path("C:\\x\\Game.exe").key, "game.exe")
        self.assertIsNone(app_keys.from_path(""), "the dialog was cancelled")

    def test_running_apps_are_sorted_by_name_once_each(self) -> None:
        found = [AppChoice("b.exe", "beta"), AppChoice("a.exe", "Alpha"), AppChoice("b.exe", "beta again")]
        system = "Windows" if platform.system() == "Windows" else "Darwin"
        target = "_running_windows" if system == "Windows" else "_running_mac"
        with mock.patch.object(app_keys.platform, "system", return_value=system), \
                mock.patch.object(app_keys, target, return_value=found):
            self.assertEqual(app_keys.running_apps(), [AppChoice("a.exe", "Alpha"), AppChoice("b.exe", "beta")])

    def test_a_failure_to_list_them_leaves_the_file_dialog(self) -> None:
        with mock.patch.object(app_keys.platform, "system", return_value="Darwin"), \
                mock.patch.object(app_keys, "_running_mac", side_effect=RuntimeError("no AppKit")), \
                self.assertLogs("app.app_keys", "WARNING"):
            self.assertEqual(app_keys.running_apps(), [])

    @unittest.skipUnless(platform.system() == "Darwin", "macOS")
    def test_the_running_apps_on_this_mac(self) -> None:
        # Reads NSWorkspace's list; opens nothing.
        apps = app_keys._running_mac()
        self.assertTrue(all(isinstance(app, AppChoice) and app.key and app.name for app in apps))

    @unittest.skipUnless(platform.system() == "Windows", "Windows")
    def test_the_running_programs_on_this_pc(self) -> None:
        # EnumWindows and a toolhelp snapshot; opens nothing.
        apps = app_keys._running_windows()
        self.assertTrue(all(app.key.endswith(".exe") and app.key == app.key.lower() for app in apps))
        self.assertNotIn("explorer.exe", {app.key for app in apps})


if __name__ == "__main__":
    unittest.main()
