"""Open at login: what the system says, and where the entry may point."""

try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app import startup


class FakeRegistry:
    """Just enough of winreg for startup.py, kept in memory."""

    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1
    REG_BINARY = 3

    class Key:
        def __init__(self, values: dict) -> None:
            self.values = values

        def __enter__(self):
            return self

        def __exit__(self, *_exc) -> bool:
            return False

    def __init__(self) -> None:
        self.keys = {startup.RUN_KEY: {}}

    def OpenKey(self, _root, path, _reserved=0, _access=1):  # noqa: N802
        if path not in self.keys:
            raise FileNotFoundError(path)
        return self.Key(self.keys[path])

    def QueryValueEx(self, key, name):  # noqa: N802
        if name not in key.values:
            raise FileNotFoundError(name)
        return key.values[name]

    def SetValueEx(self, key, name, _reserved, kind, value):  # noqa: N802
        key.values[name] = (value, kind)

    def DeleteValue(self, key, name):  # noqa: N802
        if name not in key.values:
            raise FileNotFoundError(name)
        del key.values[name]

    def turn_off_in_task_manager(self) -> None:
        self.keys.setdefault(startup.APPROVED_KEY, {})[startup.APP_NAME] = (b"\x03" + bytes(11), self.REG_BINARY)


class WindowsLoginItemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = FakeRegistry()
        for patch in (
            mock.patch.dict(sys.modules, {"winreg": self.registry}),
            mock.patch.object(startup.platform, "system", return_value="Windows"),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def test_off_without_a_run_value(self) -> None:
        self.assertEqual(startup.status(), startup.OFF)

    def test_on_with_a_run_value(self) -> None:
        startup.set_enabled(True)
        self.assertEqual(startup.status(), startup.ON)
        self.assertTrue(startup.is_enabled())

    def test_turned_off_in_task_manager_reads_as_blocked(self) -> None:
        startup.set_enabled(True)
        self.registry.turn_off_in_task_manager()
        self.assertEqual(startup.status(), startup.BLOCKED)
        self.assertFalse(startup.is_enabled(), "it won't start, so it isn't on")

    def test_an_enabled_flag_still_reads_as_on(self) -> None:
        startup.set_enabled(True)
        self.registry.keys[startup.APPROVED_KEY] = {startup.APP_NAME: (b"\x02" + bytes(11), 3)}
        self.assertEqual(startup.status(), startup.ON)

    def test_turning_it_on_clears_the_task_manager_choice(self) -> None:
        startup.set_enabled(True)
        self.registry.turn_off_in_task_manager()
        startup.set_enabled(True)
        self.assertEqual(startup.status(), startup.ON)
        self.assertNotIn(startup.APP_NAME, self.registry.keys[startup.APPROVED_KEY])

    def test_turning_it_off_removes_both_values(self) -> None:
        startup.set_enabled(True)
        self.registry.turn_off_in_task_manager()
        startup.set_enabled(False)
        self.assertEqual(self.registry.keys[startup.RUN_KEY], {})
        self.assertEqual(self.registry.keys[startup.APPROVED_KEY], {})


class MacLoginItemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plist = Path(tempfile.mkdtemp()) / "com.doubleclickfixer.app.plist"
        for patch in (
            mock.patch.object(startup.platform, "system", return_value="Darwin"),
            mock.patch.object(startup, "_launch_agent_path", return_value=self.plist),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def test_off_without_the_plist(self) -> None:
        self.assertEqual(startup.status(), startup.OFF)

    def test_on_when_login_items_allows_it(self) -> None:
        self.plist.write_text("x")
        for answer in (1, None, 0, 3):  # enabled, or not known yet
            with mock.patch.object(startup, "_legacy_status", return_value=answer):
                self.assertEqual(startup.status(), startup.ON, answer)

    def test_switched_off_in_login_items_reads_as_blocked(self) -> None:
        self.plist.write_text("x")
        with mock.patch.object(startup, "_legacy_status", return_value=2):
            self.assertEqual(startup.status(), startup.BLOCKED)

    def test_login_items_opens_through_service_management(self) -> None:
        service = mock.Mock()
        with mock.patch.object(startup, "_app_service", return_value=service), \
                mock.patch.object(startup.subprocess, "Popen") as popen:
            startup.open_login_items_settings()
        service.openSystemSettingsLoginItems.assert_called_once_with()
        popen.assert_not_called()

    def test_login_items_falls_back_to_the_pane_address(self) -> None:
        with mock.patch.object(startup, "_app_service", return_value=None), \
                mock.patch.object(startup.subprocess, "Popen") as popen:
            startup.open_login_items_settings()
        popen.assert_called_once_with(["/usr/bin/open", startup.LOGIN_ITEMS_PANE])


class TemporaryLocationTests(unittest.TestCase):
    def test_disk_images_and_translocated_copies_are_temporary(self) -> None:
        for path, temporary in (
            ("/Volumes/Mouse Double-Click Fixer/Mouse Double-Click Fixer.app/Contents/MacOS/DoubleClickFixer", True),
            ("/private/var/folders/x/T/AppTranslocation/1234/d/Mouse Double-Click Fixer.app/Contents/MacOS/DoubleClickFixer", True),
            ("/Applications/Mouse Double-Click Fixer.app/Contents/MacOS/DoubleClickFixer", False),
            (r"C:\Users\me\AppData\Local\Programs\Mouse Double-Click Fixer\DoubleClickFixer.exe", False),
        ):
            with mock.patch.object(startup.sys, "executable", path):
                self.assertEqual(startup.running_from_temporary_location(), temporary, path)


class ControllerLoginItemTests(unittest.TestCase):
    def setUp(self) -> None:
        from app import settings

        directory = Path(tempfile.mkdtemp())
        for patch in (
            mock.patch.object(settings, "config_dir", return_value=directory),
            mock.patch.object(settings, "LEGACY_PATH", directory / "absent.json"),
            mock.patch.object(startup, "is_supported", return_value=True),
            mock.patch.object(startup, "set_enabled"),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.set_enabled = startup.set_enabled

    def controller(self, state: str, executable: str, frozen: bool = True):
        from app.controller import AppController

        with mock.patch.object(startup, "status", return_value=state), \
                mock.patch.object(startup.sys, "executable", executable), \
                mock.patch("app.controller.sys.frozen", frozen, create=True):
            controller = AppController()
        self.addCleanup(controller.shutdown)  # before the patches of setUp are undone
        return controller

    def test_the_installed_copy_rewrites_its_login_item(self) -> None:
        controller = self.controller(startup.ON, "/Applications/Mouse Double-Click Fixer.app/Contents/MacOS/DoubleClickFixer")
        self.set_enabled.assert_called_once_with(True)
        self.assertTrue(controller.settings["start_at_login"])

    def test_a_copy_on_the_disk_image_never_rewrites_it(self) -> None:
        self.controller(startup.ON, "/Volumes/Mouse Double-Click Fixer/Mouse Double-Click Fixer.app/Contents/MacOS/DoubleClickFixer")
        self.set_enabled.assert_not_called()

    def test_a_blocked_login_item_reads_as_off_and_is_left_alone(self) -> None:
        controller = self.controller(startup.BLOCKED, "/Applications/Mouse Double-Click Fixer.app/Contents/MacOS/DoubleClickFixer")
        self.set_enabled.assert_not_called()
        self.assertFalse(controller.settings["start_at_login"])
        self.assertEqual(controller.login_item_state, startup.BLOCKED)

    def test_turning_it_on_from_the_disk_image_explains_instead(self) -> None:
        controller = self.controller(startup.OFF, "/Applications/x")
        with mock.patch.object(startup.sys, "executable", "/Volumes/Mouse Double-Click Fixer/x"):
            error = controller.set_start_at_login(True)
        self.assertIn("Applications", error)
        self.set_enabled.assert_not_called()

    def test_turning_it_on_reads_back_what_the_system_says(self) -> None:
        controller = self.controller(startup.OFF, "/Applications/x")
        with mock.patch.object(startup, "status", return_value=startup.BLOCKED):
            self.assertEqual(controller.set_start_at_login(True), "")
        self.assertFalse(controller.settings["start_at_login"], "macOS still has it switched off")
        self.assertEqual(controller.login_item_state, startup.BLOCKED)


if __name__ == "__main__":
    unittest.main()
