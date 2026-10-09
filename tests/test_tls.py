"""The TLS backend the app makes Qt use (app/tls.py), and, on CI only, real
HTTPS requests through it: one that must succeed, and three whose bad
certificates must be refused."""

try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

import os
import sys
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


#: The probe's URLs: one that must answer, and three whose certificates must
#: be refused (badssl.com keeps them broken on purpose).
GOOD = "https://api.github.com/zen"
REFUSED = {
    "https://expired.badssl.com/": {"CertificateExpired"},
    "https://self-signed.badssl.com/": {"SelfSignedCertificate", "CertificateUntrusted"},
    "https://wrong.host.badssl.com/": {"HostNameMismatch"},
}
PROBE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "https_probe.py")


@unittest.skipUnless(os.environ.get("DCF_REAL_HTTPS") == "1", "reaches the internet; ci.yml runs it in a step of its own")
class RealHttpsTests(unittest.TestCase):
    """Real requests through the backend the app ships with (tests/https_probe.py,
    in a fresh process): Schannel on Windows, Qt's OpenSSL backend on macOS,
    on the very OpenSSL Python uses. One must succeed, and certificates that
    are expired, self-signed or for another host must each be refused."""

    @classmethod
    def setUpClass(cls) -> None:
        import json
        import subprocess

        result = subprocess.run([sys.executable, PROBE, GOOD, *REFUSED], capture_output=True, text=True,
                                timeout=300, check=False)
        print(f"\n{result.stdout}{result.stderr}", file=sys.stderr)
        if result.returncode != 0:
            raise AssertionError(f"the probe failed (exit {result.returncode})")
        lines = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
        cls.setup = lines[0]
        cls.replies = {line["url"]: line for line in lines if "url" in line}
        cls.images = next((line["openssl_images"] for line in lines if "openssl_images" in line), [])

    def test_the_backend_is_the_one_the_app_ships_with(self) -> None:
        expected = {"win32": "schannel", "darwin": "openssl"}.get(sys.platform, "openssl")
        self.assertEqual((self.setup["backend"], self.setup["active"]), (expected, expected), self.setup)
        self.assertGreater(self.setup["roots"], 0)
        if sys.platform == "darwin":
            self.assertTrue(self.setup["library"].startswith("OpenSSL 3."), self.setup)
            loaded = [path for path in self.images if not path.startswith(("/usr/lib/", "/System/"))]
            self.assertTrue(loaded, self.images)
            folders = {os.path.realpath(os.path.dirname(path)) for path in loaded}
            self.assertEqual(folders, {os.path.realpath(self.setup["openssl_folder"])}, self.images)
            if self.setup["openssl_source"] == "Python's":
                # The same file, so the same version: what a built app runs.
                self.assertEqual(self.setup["library"], self.setup["python_openssl"])

    def test_github_answers(self) -> None:
        reply = self.replies[GOOD]
        self.assertEqual((reply["error"], reply["status"]), ("NoError", 200), reply)
        self.assertTrue(reply["body"], reply)
        self.assertEqual(reply["ssl_errors"], [])

    def test_a_bad_certificate_is_refused(self) -> None:
        for url, expected in REFUSED.items():
            with self.subTest(url=url):
                reply = self.replies[url]
                self.assertTrue(reply["finished"], reply)
                self.assertEqual(reply["error"], "SslHandshakeFailedError", reply)
                self.assertIsNone(reply["status"], reply)
                self.assertTrue(expected & set(reply["ssl_errors"]), reply)


if __name__ == "__main__":
    unittest.main()
