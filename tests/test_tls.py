"""The TLS backend the app makes Qt use (app/tls.py), and, on CI only, a real
HTTPS request through it."""

try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

import os
import sys
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app import tls


class FakeSockets:
    """QSslSocket's static backend calls, as Qt answers them."""

    def __init__(self, available, in_use=None):
        self.available = list(available)
        self.in_use = in_use  # a backend Qt already started, which it keeps
        self.active = in_use or ""
        self.asked = []

    def availableBackends(self):  # noqa: N802
        return list(self.available)

    def setActiveBackend(self, name):  # noqa: N802
        self.asked.append(name)
        if self.in_use:
            return self.in_use == name
        if name not in self.available:
            return False
        self.active = name
        return True

    def activeBackend(self):  # noqa: N802
        return self.active or (self.available[0] if self.available else "")

    def sslLibraryVersionString(self):  # noqa: N802
        return f"{self.activeBackend()} 1.0"


class ChoiceTests(unittest.TestCase):
    def test_windows_uses_schannel_and_nothing_else(self) -> None:
        self.assertEqual(tls.choose_backend(["openssl", "schannel", "cert-only"], "win32"), "schannel")
        with self.assertRaisesRegex(tls.Unavailable, "no schannel TLS backend here, only openssl, cert-only"):
            tls.choose_backend(["openssl", "cert-only"], "win32")

    def test_macos_prefers_openssl_and_falls_back_on_the_systems_own(self) -> None:
        self.assertEqual(tls.choose_backend(["securetransport", "openssl", "cert-only"], "darwin"), "openssl")
        self.assertEqual(tls.choose_backend(["securetransport", "cert-only"], "darwin"), "securetransport")
        with self.assertRaisesRegex(tls.Unavailable, "no openssl or securetransport TLS backend here, only cert-only"):
            tls.choose_backend(["cert-only"], "darwin")
        with self.assertRaisesRegex(tls.Unavailable, "only none"):
            tls.choose_backend([], "darwin")

    def test_elsewhere_openssl(self) -> None:
        self.assertEqual(tls.choose_backend(["openssl", "cert-only"], "linux"), "openssl")
        with self.assertRaises(tls.Unavailable):
            tls.choose_backend(["cert-only"], "linux")

    def test_the_choice_is_made_explicitly_rather_than_left_to_qt(self) -> None:
        # Qt would pick OpenSSL on Windows whenever it could load it.
        sockets = FakeSockets(["openssl", "schannel", "cert-only"])
        self.assertEqual(tls.select_backend(sockets, "win32"), "schannel")
        self.assertEqual(sockets.asked, ["schannel"])
        self.assertEqual(sockets.activeBackend(), "schannel")

    def test_a_fallback_is_logged(self) -> None:
        sockets = FakeSockets(["securetransport", "cert-only"])
        with self.assertLogs("app.tls", "WARNING") as logged:
            self.assertEqual(tls.select_backend(sockets, "darwin"), "securetransport")
        self.assertIn("Qt's openssl TLS backend isn't available; using securetransport", logged.output[0])

    def test_a_backend_qt_already_started_is_not_silently_kept(self) -> None:
        sockets = FakeSockets(["openssl", "schannel"], in_use="openssl")
        with self.assertRaisesRegex(tls.Unavailable, "already using its openssl TLS backend, not schannel"):
            tls.select_backend(sockets, "win32")
        same = FakeSockets(["openssl", "schannel"], in_use="schannel")
        self.assertEqual(tls.select_backend(same, "win32"), "schannel")

    def test_a_refused_choice_fails(self) -> None:
        sockets = FakeSockets(["schannel"])
        sockets.setActiveBackend = lambda name: False
        with self.assertRaises(tls.Unavailable):
            tls.select_backend(sockets, "win32")

    def test_the_choice_is_made_once_and_after_loading_pythons_openssl(self) -> None:
        calls = []
        with mock.patch.object(tls, "_chosen", None), \
                mock.patch.object(tls, "load_bundled_openssl", side_effect=lambda: calls.append("load")), \
                mock.patch.object(tls, "select_backend", side_effect=lambda sockets: calls.append("select") or "x"):
            self.assertEqual(tls.use_preferred_backend(), "x")
            self.assertEqual(tls.use_preferred_backend(), "x")
        self.assertEqual(calls, ["load", "select"])

    @unittest.skipUnless(sys.platform == "darwin", "macOS only")
    def test_pythons_openssl_is_loaded_first_on_macos(self) -> None:
        tls.load_bundled_openssl()
        self.assertIn("_ssl", sys.modules)
        self.assertIn("_hashlib", sys.modules)

    def test_this_platform_gets_a_backend_it_may_use(self) -> None:
        from PySide6.QtNetwork import QSslSocket

        name = tls.use_preferred_backend()
        self.assertIn(name, tls.preferred_backends())
        self.assertEqual(QSslSocket.activeBackend(), name)
        if sys.platform == "win32":
            self.assertEqual(name, "schannel")


@unittest.skipUnless(os.environ.get("CI") == "true", "reaches api.github.com; runs on CI only")
class RealHttpsTests(unittest.TestCase):
    def test_github_answers_over_the_apps_tls(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest, QSslSocket
        from PySide6.QtWidgets import QApplication

        from app import __version__

        application = QApplication.instance() or QApplication([])
        backend = tls.use_preferred_backend()
        manager = QNetworkAccessManager()
        request = QNetworkRequest(QUrl("https://api.github.com/zen"))
        request.setRawHeader(b"User-Agent", f"DoubleClickFixer/{__version__} (CI)".encode())
        request.setTransferTimeout(30_000)
        token = os.environ.get("GITHUB_TOKEN", "")
        if token:  # GitHub limits unauthenticated calls per address, and CI's Macs share theirs
            request.setRawHeader(b"Authorization", f"Bearer {token}".encode())
        reply = manager.get(request)
        deadline = time.time() + 60
        while not reply.isFinished() and time.time() < deadline:
            application.processEvents()
            time.sleep(0.01)
        self.assertTrue(reply.isFinished(), "no answer within 60 s")
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        body = bytes(reply.readAll()).decode("utf-8", "replace").strip()
        print(f"\nGET https://api.github.com/zen through Qt's {QSslSocket.activeBackend()} backend "
              f"({QSslSocket.sslLibraryVersionString()}): HTTP {status}, {body!r}", file=sys.stderr)
        self.assertEqual(reply.error(), QNetworkReply.NetworkError.NoError, reply.errorString())
        self.assertEqual(status, 200)
        self.assertTrue(body)
        self.assertEqual(QSslSocket.activeBackend(), backend)
        reply.deleteLater()


if __name__ == "__main__":
    unittest.main()
