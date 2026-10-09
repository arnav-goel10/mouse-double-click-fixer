"""One copy of the app per signed-in user and session (app/main.py)."""

import os
import platform
import unittest
from unittest import mock

from app import main as main_module
from app.main import LOCK_NAME, SERVER_NAME


class ChannelNameTests(unittest.TestCase):
    def test_macos_keeps_its_one_name(self) -> None:
        # Its socket and lock already sit in the user's own temporary folder.
        with mock.patch("app.main.platform.system", return_value="Darwin"):
            self.assertEqual(main_module._channel_name(), SERVER_NAME)
            self.assertEqual(main_module._channel_names(), [SERVER_NAME])

    def test_windows_names_the_session_and_the_user(self) -> None:
        sid = "S-1-5-21-111-222-333-1001"
        with mock.patch("app.main.platform.system", return_value="Windows"), \
                mock.patch.object(main_module, "_windows_session_and_user", return_value=(2, sid)):
            name = main_module._channel_name()
            self.assertEqual(name, f"{SERVER_NAME}-2-{sid}")
            # The machine-wide name copies before 1.0 listen on is still tried.
            self.assertEqual(main_module._channel_names(), [name, SERVER_NAME])

    def test_a_user_name_is_made_safe_for_a_pipe(self) -> None:
        with mock.patch("app.main.platform.system", return_value="Windows"), \
                mock.patch.object(main_module, "_windows_session_and_user", return_value=(1, "OFFICE\\Jo Ann")):
            self.assertEqual(main_module._channel_name(), f"{SERVER_NAME}-1-OFFICE_Jo_Ann")

    def test_a_failure_to_read_the_user_still_names_one(self) -> None:
        with mock.patch("app.main.platform.system", return_value="Windows"), \
                mock.patch.object(main_module, "_windows_session_and_user", side_effect=OSError("no token")), \
                mock.patch.dict(os.environ, {"USERNAME": "jo"}):
            self.assertEqual(main_module._channel_name(), f"{SERVER_NAME}-0-jo")

    def test_the_lock_follows_the_channel(self) -> None:
        with mock.patch.object(main_module, "_channel_name", return_value="dcf-test-channel"):
            lock = main_module._instance_lock()
        self.assertTrue(lock.fileName().endswith(f"{LOCK_NAME}-dcf-test-channel"))


@unittest.skipUnless(platform.system() == "Windows", "Windows only")
class WindowsSessionTests(unittest.TestCase):
    def test_the_real_session_and_user(self) -> None:
        session, user = main_module._windows_session_and_user()
        self.assertGreaterEqual(session, 0)
        self.assertTrue(user.startswith("S-1-"), user)

    def test_only_this_sessions_processes_count(self) -> None:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        session, _user = main_module._windows_session_and_user()
        self.assertTrue(main_module._same_session(kernel32, os.getpid(), session))
        self.assertFalse(main_module._same_session(kernel32, os.getpid(), session + 1))
        self.assertFalse(main_module._same_session(kernel32, 0xFFFFFFF0, session), "no such process")

    def test_a_user_only_channel_still_takes_connections(self) -> None:
        from PySide6.QtCore import QCoreApplication
        from PySide6.QtNetwork import QLocalServer, QLocalSocket

        _application = QCoreApplication.instance() or QCoreApplication([])
        name = f"dcf-test-channel-{os.getpid()}"
        server = QLocalServer()
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.assertTrue(server.listen(name), server.errorString())
        self.addCleanup(server.close)
        client = QLocalSocket()
        client.connectToServer(name)
        self.assertTrue(client.waitForConnected(2000), client.errorString())
        client.disconnectFromServer()


if __name__ == "__main__":
    unittest.main()
