"""macOS permission helpers: the pane's name, the Settings button, the tap probe."""

try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

import unittest
from unittest import mock

from app import permissions


class PaneNameTests(unittest.TestCase):
    def name_on(self, product: str, darwin: str = "24.0.0") -> str:
        uname = mock.Mock(release=darwin)
        with mock.patch.object(permissions.platform, "system", return_value="Darwin"), \
                mock.patch.object(permissions.platform, "mac_ver", return_value=(product, ("", "", ""), "arm64")), \
                mock.patch.object(permissions.os, "uname", return_value=uname, create=True):
            return permissions.pane_name()

    def test_macos_27_calls_it_device_control_and_data_access(self) -> None:
        self.assertEqual(self.name_on("27.0.1", "27.0.0"), "Device Control and Data Access")
        self.assertEqual(self.name_on("28.1", "28.0.0"), "Device Control and Data Access")

    def test_earlier_macos_calls_it_accessibility(self) -> None:
        self.assertEqual(self.name_on("26.4", "25.4.0"), "Accessibility")
        self.assertEqual(self.name_on("13.6", "22.6.0"), "Accessibility")

    def test_a_disguised_version_is_read_from_the_kernel(self) -> None:
        # An app linked against an older SDK is told 16.0 (or 10.16).
        self.assertEqual(self.name_on("16.0", "27.0.0"), "Device Control and Data Access")
        self.assertEqual(self.name_on("16.0", "25.0.0"), "Accessibility")
        self.assertEqual(self.name_on("10.16", "24.1.0"), "Accessibility")

    def test_other_systems_have_no_pane(self) -> None:
        with mock.patch.object(permissions.platform, "system", return_value="Windows"):
            self.assertEqual(permissions.macos_major(), 0)
            self.assertEqual(permissions.pane_name(), "Accessibility")


class OpenSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.object(permissions, "needs_accessibility", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_an_allowed_app_still_opens_the_pane(self) -> None:
        with mock.patch.object(permissions, "has_accessibility", return_value=True), \
                mock.patch.object(permissions, "request_accessibility") as request, \
                mock.patch.object(permissions.subprocess, "Popen") as popen:
            permissions.open_accessibility_settings()
        request.assert_not_called()
        popen.assert_called_once_with(["open", permissions.ACCESSIBILITY_PANE])

    def test_an_app_not_yet_allowed_is_registered_first(self) -> None:
        calls = []
        with mock.patch.object(permissions, "has_accessibility", return_value=False), \
                mock.patch.object(permissions, "request_accessibility", side_effect=lambda: calls.append("request")), \
                mock.patch.object(permissions.subprocess, "Popen", side_effect=lambda _args: calls.append("open")):
            permissions.open_accessibility_settings()
        self.assertEqual(calls, ["request", "open"])


class TapProbeTests(unittest.TestCase):
    def fake_functions(self, port):
        graphics, foundation = mock.Mock(), mock.Mock()
        graphics.CGEventTapCreate.return_value = port
        return graphics, foundation

    def test_a_refused_tap_means_access_is_gone(self) -> None:
        graphics, foundation = self.fake_functions(None)
        with mock.patch.object(permissions, "needs_accessibility", return_value=True), \
                mock.patch.object(permissions, "_tap_functions", return_value=(graphics, foundation)):
            self.assertFalse(permissions.event_tap_allowed())
        foundation.CFMachPortInvalidate.assert_not_called()

    def test_the_probe_tap_is_closed_at_once(self) -> None:
        graphics, foundation = self.fake_functions(0x1234)
        with mock.patch.object(permissions, "needs_accessibility", return_value=True), \
                mock.patch.object(permissions, "_tap_functions", return_value=(graphics, foundation)):
            self.assertTrue(permissions.event_tap_allowed())
        location, placement, option, mask, _callback, _refcon = graphics.CGEventTapCreate.call_args.args
        self.assertEqual((location, placement, option), (1, 1, 0), "session tap, appended, filtering")
        self.assertEqual(mask, 1 << 24, "tablet proximity only")
        foundation.CFMachPortInvalidate.assert_called_once_with(0x1234)
        foundation.CFRelease.assert_called_once_with(0x1234)

    def test_nothing_to_probe_off_macos(self) -> None:
        with mock.patch.object(permissions, "needs_accessibility", return_value=False), \
                mock.patch.object(permissions, "_tap_functions") as functions:
            self.assertTrue(permissions.event_tap_allowed())
        functions.assert_not_called()


if __name__ == "__main__":
    unittest.main()
