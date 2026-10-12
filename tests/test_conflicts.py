"""Other mouse utilities running beside the app: recognising each one, and
keeping the list current."""

try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

try:
    import _trust_helpers as helpers
except ImportError:
    from tests import _trust_helpers as helpers

import os
import platform
import sys
import unittest
from unittest import mock

from PySide6.QtCore import Qt

from app import conflicts
from app.conflicts import Conflict

#: What each utility looks like running, from the sources in app/conflicts.py.
MAC_SEEN = {
    "Logi Options+": {"bundles": ["com.logi.optionsplus.driverhost"]},
    "Logitech Options": {"bundles": ["com.logitech.manager.daemon"]},
    "Logitech G HUB": {"bundles": ["com.logi.ghub.agent"]},
    "Razer Synapse": {"processes": ["RzDeviceEngine"]},
    "Mac Mouse Fix": {"bundles": ["com.nuebling.mac-mouse-fix.helper"], "processes": ["Mac Mouse Fix Helper"]},
    "SteerMouse": {"bundles": ["jp.plentycom.boa.SteerMouse"]},
    "LinearMouse": {"bundles": ["com.lujjjh.LinearMouse"]},
    "BetterMouse": {"bundles": ["com.naotanhaocan.BetterMouse"]},
}
WINDOWS_SEEN = {
    "Logi Options+": "logioptionsplus_agent.exe",
    "Logitech Options": "logioptionsmgr.exe",
    "Logitech G HUB": "lghub_agent.exe",
    "Razer Synapse": "razerappengine.exe",
    "X-Mouse Button Control": "xmousebuttoncontrol.exe",
    "DoubleClickFix": "doubleclickfix.exe",
}


class MatchTests(unittest.TestCase):
    def test_each_mac_utility_by_what_it_runs(self) -> None:
        for name, seen in MAC_SEEN.items():
            for kind, values in seen.items():
                with self.subTest(name=name, kind=kind):
                    found = conflicts.match("Darwin", **{kind: values})
                    self.assertEqual([conflict.name for conflict in found], [name])

    def test_each_windows_utility_by_its_executable(self) -> None:
        for name, exe in WINDOWS_SEEN.items():
            with self.subTest(name=name):
                found = conflicts.match("Windows", processes=[exe.upper()])
                self.assertEqual([conflict.name for conflict in found], [name])

    def test_the_brief_list_is_covered_on_its_platforms(self) -> None:
        names = {utility.name for utility in conflicts.KNOWN}
        for wanted in ("Logi Options+", "Logitech G HUB", "Razer Synapse", "Mac Mouse Fix", "SteerMouse", "LinearMouse",
                       "BetterMouse", "X-Mouse Button Control", "DoubleClickFix"):
            self.assertIn(wanted, names)
        for utility in conflicts.KNOWN:
            with self.subTest(utility=utility.name):
                self.assertTrue(utility.mac_bundles or utility.mac_processes or utility.windows_processes)
                self.assertEqual(utility.windows_processes, tuple(name.lower() for name in utility.windows_processes))
                for process in utility.mac_processes:
                    self.assertLessEqual(len(process), 32, "libproc names are cut at 32 characters")

    def test_a_platforms_names_dont_count_on_the_other(self) -> None:
        self.assertEqual(conflicts.match("Windows", bundles=["com.lujjjh.LinearMouse"]), [])
        self.assertEqual(conflicts.match("Darwin", processes=["xmousebuttoncontrol.exe"]), [])
        self.assertEqual(conflicts.match("Linux", bundles=["com.lujjjh.LinearMouse"], processes=["lghub.exe"]), [])

    def test_nothing_running(self) -> None:
        self.assertEqual(conflicts.match("Darwin", ["com.apple.finder"], ["launchd", "Finder"]), [])
        self.assertEqual(conflicts.match("Windows", processes=["explorer.exe", "python.exe"]), [])

    def test_what_each_may_take_over(self) -> None:
        found = conflicts.match("Darwin", ["com.lujjjh.LinearMouse", "com.nuebling.mac-mouse-fix.helper"])
        self.assertEqual(found, [
            Conflict("LinearMouse", ["clicks", "side buttons", "wheel"], "linearmouse"),
            Conflict("Mac Mouse Fix", ["side buttons", "middle button", "wheel"], "mac-mouse-fix"),
        ])
        self.assertEqual(found[0].takes_text(), "the clicks, the side buttons and the wheel")
        self.assertEqual(Conflict("X", ["wheel"]).takes_text(), "the wheel")
        self.assertEqual(Conflict("X", ["side buttons", "wheel"]).takes_text(), "the side buttons and the wheel")
        double = conflicts.match("Windows", processes=["DoubleClickFix.exe"])[0]
        self.assertIn("clicks", double.takes, "another bounce filter judges every click too")

    def test_one_entry_per_utility_however_many_parts_run(self) -> None:
        found = conflicts.match("Darwin", ["com.logi.optionsplus", "com.logi.optionsplus.driverhost"],
                                ["logioptionsplus_agent"])
        self.assertEqual(len(found), 1)


class ScanTests(unittest.TestCase):
    def test_a_failed_look_finds_nothing(self) -> None:
        with mock.patch.object(conflicts, "_mac_bundles", side_effect=RuntimeError("x")), \
                mock.patch.object(conflicts, "_mac_process_names", side_effect=OSError("x")), \
                mock.patch.object(conflicts, "_windows_process_names", side_effect=OSError("x")):
            self.assertEqual(conflicts.scan(), [])

    def test_scan_uses_the_systems_lists(self) -> None:
        with mock.patch.object(conflicts.platform, "system", return_value="Darwin"), \
                mock.patch.object(conflicts, "_mac_bundles", return_value=["com.lujjjh.LinearMouse"]), \
                mock.patch.object(conflicts, "_mac_process_names", return_value=["SteerMouse Manager"]):
            self.assertEqual([c.name for c in conflicts.scan()], ["LinearMouse", "SteerMouse"])
        with mock.patch.object(conflicts.platform, "system", return_value="Windows"), \
                mock.patch.object(conflicts, "_windows_process_names", return_value=["lghub.exe"]):
            self.assertEqual([c.name for c in conflicts.scan()], ["Logitech G HUB"])

    @unittest.skipUnless(platform.system() == "Darwin", "macOS only")
    def test_the_real_process_names_include_this_one(self) -> None:
        # Names only, read with libproc: no permission, nothing changed.
        names = conflicts._mac_process_names()
        self.assertGreater(len(names), 5)
        import ctypes

        library = ctypes.CDLL("/usr/lib/libproc.dylib")
        buffer = ctypes.create_string_buffer(256)
        library.proc_name(os.getpid(), buffer, 256)
        self.assertIn(buffer.value.decode(), names)
        self.assertIsInstance(conflicts._mac_bundles(), list)

    @unittest.skipUnless(platform.system() == "Windows", "Windows only")
    def test_the_real_process_list_includes_python(self) -> None:
        names = conflicts._windows_process_names()
        self.assertIn(os.path.basename(sys.executable).lower(), names)


class ControllerConflictTests(helpers.ControllerCase):
    def test_nothing_is_looked_for_until_the_services_start(self) -> None:
        self.found = [Conflict("Mac Mouse Fix", ["side buttons"], "mac-mouse-fix")]
        self.process_events()
        self.assertEqual(self.controller.conflicts(), [])
        self.assertEqual(self.controller.diagnostic_state()["other mouse utilities"], "not checked")

    def test_found_when_the_services_start_and_said_once(self) -> None:
        changes = []
        self.controller.conflicts_changed.connect(lambda: changes.append(1))
        self.found = [Conflict("Mac Mouse Fix", ["side buttons", "wheel"], "mac-mouse-fix")]
        self.controller.start_services()
        self.process_events()
        self.assertEqual(self.controller.conflicts(), self.found)
        self.assertEqual(changes, [1])
        self.controller.refresh_conflicts()
        self.assertEqual(changes, [1], "no change, no signal")
        self.assertEqual(self.controller.diagnostic_state()["other mouse utilities"],
                         "Mac Mouse Fix (side buttons, wheel)")
        self.found = []
        self.controller.refresh_conflicts()
        self.assertEqual(changes, [1, 1])
        self.assertEqual(self.controller.diagnostic_state()["other mouse utilities"], "none")

    def test_looked_for_again_on_a_timer_and_when_the_app_comes_forward(self) -> None:
        self.controller.start_services()
        self.process_events()
        self.assertTrue(self.controller._conflict_timer.isActive())
        self.found = [Conflict("SteerMouse", ["wheel"], "steermouse")]
        self.controller._on_application_state(Qt.ApplicationState.ApplicationInactive)
        self.assertEqual(self.controller.conflicts(), [])
        self.controller._on_application_state(Qt.ApplicationState.ApplicationActive)
        self.assertEqual(self.controller.conflicts(), self.found)

    def test_a_failed_look_keeps_the_last_list(self) -> None:
        self.found = [Conflict("SteerMouse", ["wheel"], "steermouse")]
        self.controller.refresh_conflicts()

        def broken():
            raise RuntimeError("x")

        self.controller._conflict_scan = broken
        self.controller.refresh_conflicts()
        self.assertEqual([c.name for c in self.controller.conflicts()], ["SteerMouse"])

    def test_the_list_is_a_copy(self) -> None:
        self.found = [Conflict("SteerMouse", ["wheel"], "steermouse")]
        self.controller.refresh_conflicts()
        self.controller.conflicts().clear()
        self.assertEqual(len(self.controller.conflicts()), 1)


if __name__ == "__main__":
    unittest.main()
