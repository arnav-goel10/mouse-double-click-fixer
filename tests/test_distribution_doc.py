"""docs/DISTRIBUTION.md says things about the app and its CI that the code can
check: the sentence it quotes from SignPath's terms, the switches the install
test runs, and what the SignPath application and the Store's notes for
certification say the 1.0 app does on Windows. The text is pasted into forms
other people read, so it must not drift from the code."""

try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

import re
import unittest
from pathlib import Path

from app import devices_win, settings
from app.platform import WHEEL_DEFAULT_MS

ROOT = Path(__file__).resolve().parents[1]
DOC = (ROOT / "docs" / "DISTRIBUTION.md").read_text(encoding="utf-8")
E2E = (ROOT / "tools" / "windows_install_e2e.ps1").read_text(encoding="utf-8")


def source(name: str) -> str:
    return (ROOT / "app" / name).read_text(encoding="utf-8")


def between(start: str, end: str) -> str:
    first = DOC.index(start)
    return DOC[first : DOC.index(end, first)]


#: What the application and the notes name, and the file that has it.
API = {
    "SetWindowsHookEx": "platform.py",
    "WH_MOUSE_LL": "platform.py",
    "SendInput": "platform.py",
    "RegisterRawInputDevices": "devices_win.py",
    "RIDEV_INPUTSINK": "devices_win.py",
    "RIDEV_DEVNOTIFY": "devices_win.py",
    "SetWinEventHook": "frontmost.py",
    "EVENT_SYSTEM_FOREGROUND": "frontmost.py",
    "wear.json": "wear.py",
}


class DistributionDocTests(unittest.TestCase):
    def test_the_license_row_quotes_the_terms_sentence_word_for_word(self) -> None:
        # https://signpath.org/terms, "Conditions for free OSS SignPath.io
        # subscriptions", read on 2026-10-10. Note "license", not "licence".
        row = next(line for line in DOC.splitlines() if line.startswith("| **OSS license.**"))
        self.assertIn(
            'must use an OSI-approved Open Source license without commercial dual-licensing for all components."', row
        )

    def test_the_winget_switches_sentence_matches_what_the_install_test_runs(self) -> None:
        arguments = re.search(r'-ArgumentList \(@\(([^)]*)\)', E2E).group(1)
        run = " ".join(re.findall(r'"(/[A-Z]+)', arguments))
        self.assertEqual(run, "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG")
        self.assertNotIn("/SP-", E2E)
        sentence = " ".join(between("winget runs an Inno Setup installer silently", "- **`ProductCode`").split())
        self.assertIn("with its own switches, `/SP- /VERYSILENT /SUPPRESSMSGBOXES /NORESTART`", sentence)
        self.assertIn("runs `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=...`", sentence)
        self.assertIn("the same, less `/SP-`", sentence)
        self.assertNotIn("That is what CI's", sentence)

    def test_the_smartscreen_bullet_cites_microsoft(self) -> None:
        bullet = " ".join(between("- **SmartScreen.**", "- **macOS.**").split())
        self.assertIn("(https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation)", bullet)
        self.assertNotIn("not separately for each file", bullet)

    def test_the_application_and_the_store_notes_describe_1_0(self) -> None:
        application = between("> **What it does:**", "> **What we'd sign")
        notes = between("- Notes for certification", "Copies installed from the Store")
        for name, filename in API.items():
            for what, text in (("the application", application), ("the Store notes", notes)):
                self.assertIn(name, text, f"{what} doesn't name {name}")
            self.assertIn(name, source(filename), f"{name} is not in app/{filename}")

    def test_what_they_say_about_devices_the_wheel_and_the_buttons_is_what_the_app_does(self) -> None:
        # "mice, precision touchpads and touchscreens, never keyboards"
        self.assertEqual(devices_win.USAGES, ((0x01, 0x02), (0x0D, 0x05), (0x0D, 0x04)))
        self.assertEqual(devices_win.DIGITIZER_KINDS[0x05], "trackpad")
        # "off by default", "50 ms by default"
        self.assertIs(settings.DEFAULTS["wheel_fix"], False)
        self.assertEqual(WHEEL_DEFAULT_MS, 50)
        self.assertIn("(50 ms by default)", " ".join(between("> **What it does:**", "> **What we'd sign").replace("> ", "").split()))
        # "The left button is filtered by default"; side buttons are the user's choice
        self.assertEqual(settings.DEFAULTS["buttons"], ["left"])
        # The foreground app's name and the wear counts are kept in the
        # app's own files, never sent.
        self.assertEqual(settings.DEFAULTS["excluded_apps"], [])
        self.assertIn('WEAR_NAME = "wear.json"', source("wear.py"))
        self.assertIn("EVENT_SYSTEM_FOREGROUND, self.EVENT_SYSTEM_FOREGROUND", source("frontmost.py"))
        self.assertIn("RIDEV_INPUTSINK", source("devices_win.py"))
        self.assertIn("RIDEV_DEVNOTIFY", source("devices_win.py"))

    def test_what_they_say_about_wheel_ticks_and_touchpad_taps_is_what_the_app_does(self) -> None:
        # "judged only if a mouse's Raw Input report carries a wheel notch for
        # that axis": with no report to go by (Raw Input unavailable here), a
        # tick is nobody's, and the wheel fix leaves it alone.
        self.assertEqual(devices_win.WHEEL_FLAGS, (devices_win.RI_MOUSE_WHEEL, devices_win.RI_MOUSE_HWHEEL))
        nothing = devices_win.RawInputDevices(None, unavailable="test")
        for axis in (1, 2):
            self.assertIsNone(nothing.attribute(devices_win.wheel_flag(axis)))
        # "a touchpad tap within a second of a mouse's report is taken for
        # that mouse's click"
        self.assertEqual(devices_win.MOUSE_QUIET_S, 1.0)


if __name__ == "__main__":
    unittest.main()
