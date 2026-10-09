"""The --self-test a release runs on the built app, and the macOS runtime hook
that cleans its environment."""

try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

import contextlib
import io
import os
import platform
import re
import runpy
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app import selftest

ROOT = Path(__file__).resolve().parent.parent
SCRUB_HOOK = ROOT / "installer" / "runtime_hooks" / "scrub_env.py"
IS_MAC = platform.system() == "Darwin"


def run_self_test(checks=None):
    output = io.StringIO()
    with contextlib.redirect_stdout(output), mock.patch.object(selftest, "CHECKS", checks or selftest.CHECKS):
        code = selftest.main()
    return code, output.getvalue().splitlines()


class SelfTestTests(unittest.TestCase):
    def test_every_check_passes_or_skips_here(self) -> None:
        code, lines = run_self_test()
        self.assertEqual(code, 0, "\n".join(lines))
        self.assertTrue(lines[0].startswith("Mouse Double-Click Fixer "))
        self.assertTrue(lines[-1].startswith("self-test passed"))
        for name, _check in selftest.CHECKS:
            matching = [line for line in lines if line.startswith(f"{name}: ")]
            self.assertEqual(len(matching), 1, f"one line for {name}: {lines}")
            self.assertRegex(matching[0], r": (ok \(.+\)|skipped: .+)$")

    def test_the_callback_checks_run_on_the_platform_that_needs_them(self) -> None:
        _code, lines = run_self_test()
        result = dict(line.split(": ", 1) for line in lines[1:-1])
        self.assertTrue(result["ctypes callback"].startswith("ok"))
        self.assertTrue(result["update signatures"].startswith("ok"))
        self.assertTrue(result["app modules"].startswith("ok"))
        self.assertTrue(result["Qt platform plugin"].startswith("ok"))
        self.assertTrue(result["app icons"].startswith("ok"), result["app icons"])
        self.assertTrue(result["third-party notices"].startswith("ok"))
        self.assertRegex(result["tls"], r"^ok \((schannel|openssl|securetransport), .+, TLS 1\.2.*, [1-9]\d* root certificates")
        if sys.platform == "win32":
            self.assertTrue(result["tls"].startswith("ok (schannel, "), result["tls"])
        # From source an OpenSSL older than the floor is a skip: CI also tests
        # on Pythons that ship one (python.org's 3.13 ships OpenSSL 3.0).
        self.assertRegex(result["openssl"], r"^(ok \(OpenSSL \d+\.\d+\.\d+ in Python's ssl and hashlib.*; "
                         r"\d+\.\d+ or later required\)|skipped: not a built app, and a build would fail: .+)$")
        if IS_MAC:
            self.assertTrue(result["PyObjC callback"].startswith("ok"))
            self.assertIn(result["event tap"].split(" ")[0], ("ok", "skipped:"))
        else:
            self.assertEqual(result["PyObjC callback"], "skipped: macOS only")
        # Only a built app is signed and has its environment cleaned.
        self.assertEqual(result["environment"], "skipped: " + ("not a built app" if IS_MAC else "macOS only"))
        self.assertEqual(result["code signature"], "skipped: " + ("not a built app" if IS_MAC else "macOS only"))
        self.assertEqual(result["child processes"], "skipped: " + ("not a built app" if IS_MAC else "macOS only"))

    def test_every_module_of_the_app_is_walked_and_imported(self) -> None:
        detail = selftest.check_app_modules()
        on_disk = {
            ".".join(path.relative_to(ROOT).with_suffix("").parts).removesuffix(".__init__")
            for path in (ROOT / "app").rglob("*.py")
        } - {"app"}
        self.assertEqual(detail, f"{len(on_disk)} modules imported")
        for name in on_disk:
            self.assertIn(name, sys.modules)

    def test_a_missing_core_module_fails(self) -> None:
        with mock.patch.object(selftest, "APP_MODULES", selftest.APP_MODULES + ("app.gone",)):
            with self.assertRaisesRegex(RuntimeError, "not found in the app package: app.gone"):
                selftest.check_app_modules()

    def test_the_icons_are_drawn_and_png_round_trips(self) -> None:
        detail = selftest.check_app_icons()
        self.assertRegex(detail, r"^app 64px \(\d+ px painted\), tray 18px \(\d+ px painted\), tray active 18px")
        self.assertIn("PNG round trip", detail)
        self.assertIn("png", detail.split("image formats: ")[1].split())

    def test_an_icon_that_draws_nothing_fails(self) -> None:
        from PySide6.QtGui import QIcon

        with mock.patch("app.ui.icons.tray_icon", return_value=QIcon()):
            with self.assertRaisesRegex(RuntimeError, "the tray icon drew nothing"):
                selftest.check_app_icons()

    def test_a_failed_check_fails_the_run_and_the_rest_still_run(self) -> None:
        ran = []

        def broken() -> str:
            raise ImportError("No module named 'Quartz'")

        def fine() -> str:
            ran.append(True)
            return "fine"

        code, lines = run_self_test([("broken", broken), ("fine", fine)])
        self.assertEqual(code, 1)
        self.assertIn("broken: FAILED: ImportError: No module named 'Quartz'", lines)
        self.assertIn("fine: ok (fine)", lines)
        self.assertEqual(ran, [True])
        self.assertTrue(lines[-1].startswith("self-test FAILED: 1 failed"))

    def test_a_skip_is_not_a_failure(self) -> None:
        def no_permission() -> str:
            raise selftest.Skipped("no permission")

        code, lines = run_self_test([("event tap", no_permission)])
        self.assertEqual(code, 0)
        self.assertIn("event tap: skipped: no permission", lines)

    def test_the_offscreen_platform_is_the_default_but_a_caller_may_choose(self) -> None:
        with mock.patch.dict(os.environ, {"QT_QPA_PLATFORM": ""}):
            selftest.use_default_qt_platform()
            self.assertEqual(os.environ["QT_QPA_PLATFORM"], "offscreen")
        with mock.patch.dict(os.environ, {"QT_QPA_PLATFORM": "minimal"}):
            selftest.use_default_qt_platform()
            self.assertEqual(os.environ["QT_QPA_PLATFORM"], "minimal")

    def test_a_build_without_its_notices_fails(self) -> None:
        from app import notices

        with mock.patch.object(notices, "notices_path", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "no THIRD_PARTY_NOTICES.md"):
                selftest.check_notices()

    def test_a_missing_platform_plugin_is_reported_rather_than_aborting(self) -> None:
        from PySide6.QtCore import QCoreApplication

        with mock.patch.dict(os.environ, {"QT_QPA_PLATFORM": "nosuchplatform"}), mock.patch.object(
            QCoreApplication, "instance", return_value=None
        ):
            with self.assertRaisesRegex(RuntimeError, "no 'nosuchplatform' platform plugin"):
                selftest.check_qt_platform()


BUNDLE = "/Applications/Example.app/Contents"


def tls_facts(**changes):
    """What a built macOS app's tls check finds when all is well."""
    facts = dict(
        platform="darwin", frozen=True, available=["securetransport", "openssl", "cert-only"], active="openssl",
        supports_ssl=True, library="OpenSSL 3.6.5 29 Sep 2026", protocols=["TLS 1.2", "TLS 1.3"], roots=157,
        images=[
            "/usr/lib/libSystem.B.dylib",
            "/usr/lib/libcrypto.46.dylib",  # macOS's own, which system frameworks load
            "/usr/lib/libssl.48.dylib",
            "/usr/lib/libboringssl.dylib",
            "/System/Library/Frameworks/CryptoKit.framework/Versions/A/CryptoKit",
            f"{BUNDLE}/Frameworks/libcrypto.3.dylib",
            f"{BUNDLE}/Frameworks/python3.14/lib-dynload/_ssl.cpython-314-darwin.so",
            f"{BUNDLE}/Frameworks/libssl.3.dylib",
            f"{BUNDLE}/Frameworks/PySide6/Qt/plugins/tls/libqopensslbackend.dylib",
        ],
        bundle=BUNDLE, python_openssl="OpenSSL 3.6.5 29 Sep 2026",
    )
    facts.update(changes)
    return selftest.TlsFacts(**facts)


class TlsCheckTests(unittest.TestCase):
    """The tls check's verdict on what it finds, with made-up findings."""

    def test_a_built_mac_app_on_its_own_openssl_passes(self) -> None:
        self.assertEqual(
            selftest.judge_tls(tls_facts()),
            "openssl, OpenSSL 3.6.5 29 Sep 2026, TLS 1.2 and TLS 1.3, 157 root certificates; libcrypto.3.dylib, "
            "libssl.3.dylib from inside the app, as Python's ssl (macOS's own libcrypto.46.dylib, libssl.48.dylib aside)",
        )

    def test_no_root_certificates_fails_on_macos_and_windows(self) -> None:
        # Qt or macOS no longer handing over the Keychain's roots would fail
        # every update check, and the fix would have to come through it.
        for facts in (tls_facts(roots=0), tls_facts(frozen=False, bundle="", python_openssl="", roots=0)):
            with self.subTest(frozen=facts.frozen), self.assertRaisesRegex(
                    RuntimeError, "Qt's openssl backend loaded no root certificates from the Keychain"):
                selftest.judge_tls(facts)
        fallback = tls_facts(frozen=False, active="securetransport", library="Secure Transport", images=[],
                             protocols=["TLS 1.2"], roots=0)
        with self.assertRaisesRegex(RuntimeError, "securetransport backend loaded no root certificates"):
            selftest.judge_tls(fallback)
        for frozen in (True, False):
            with self.subTest(platform="win32", frozen=frozen), self.assertRaisesRegex(
                    RuntimeError, "Qt's schannel backend loaded no root certificates from Windows' certificate store"):
                selftest.judge_tls(self.windows(frozen=frozen, roots=0))
        self.assertIn(", 1 root certificates", selftest.judge_tls(self.windows(roots=1)))
        # Elsewhere (Linux, from source) Qt may load them only when it needs them.
        self.assertEqual(selftest.judge_tls(tls_facts(platform="linux", roots=0)),
                         "openssl, OpenSSL 3.6.5 29 Sep 2026, TLS 1.2 and TLS 1.3, 0 root certificates")

    def test_the_check_counts_the_root_certificates_qts_default_configuration_has(self) -> None:
        from PySide6.QtNetwork import QSslConfiguration

        class Configuration:
            def __init__(self, count: int) -> None:
                self.count = count

            def caCertificates(self):  # noqa: N802
                return [object()] * self.count

        for count in (0, 3):
            with self.subTest(count=count), \
                    mock.patch.object(QSslConfiguration, "defaultConfiguration", return_value=Configuration(count)), \
                    mock.patch.object(selftest, "judge_tls", side_effect=lambda facts: facts) as judge:
                facts = selftest.check_tls()
            judge.assert_called_once()
            self.assertEqual(facts.roots, count)

    def test_openssl_from_outside_the_app_fails(self) -> None:
        for path in (
            "/usr/local/lib/libssl.3.dylib",  # Qt's own search reaches here when the app's copy won't load
            "/opt/homebrew/opt/openssl@3/lib/libcrypto.3.dylib",
            "/Users/someone/Downloads/libcrypto.so.3",  # the first name Qt asks dyld for
            "/tmp/crypto.3.bundle",
            "/Applications/Example.app.evil/Contents/Frameworks/libssl.3.dylib",  # a neighbour, not the app
        ):
            facts = tls_facts()
            facts.images.append(path)
            with self.subTest(path=path), self.assertRaisesRegex(RuntimeError, "OpenSSL loaded from outside the app"):
                selftest.judge_tls(facts)

    def test_a_built_mac_app_must_run_qts_openssl_backend(self) -> None:
        facts = tls_facts(active="securetransport", library="Secure Transport, macOS 26", protocols=["TLS 1.2"])
        with self.assertRaisesRegex(RuntimeError, "using its securetransport backend, not OpenSSL from inside"):
            selftest.judge_tls(facts)
        # A library Qt tried and gave up on is still the finding that matters.
        facts.images.append("/Users/someone/libcrypto.so.3")
        with self.assertRaisesRegex(RuntimeError, "OpenSSL loaded from outside the app: /Users/someone/libcrypto.so.3"):
            selftest.judge_tls(facts)

    def test_the_openssl_backend_without_the_apps_openssl_fails(self) -> None:
        facts = tls_facts(images=["/usr/lib/libcrypto.46.dylib", "/usr/lib/libssl.48.dylib"])
        with self.assertRaisesRegex(RuntimeError, "no OpenSSL from inside the app is loaded"):
            selftest.judge_tls(facts)

    def test_qt_and_python_must_run_the_same_openssl(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Qt runs OpenSSL 3.6.5 .*, but Python's ssl module OpenSSL 3.5.0"):
            selftest.judge_tls(tls_facts(python_openssl="OpenSSL 3.5.0 8 Apr 2025"))

    def test_tls_older_than_1_2_alone_fails(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "can't make TLS 1.2 or later connections"):
            selftest.judge_tls(tls_facts(protocols=[]))
        with self.assertRaisesRegex(RuntimeError, "can't make TLS connections"):
            selftest.judge_tls(tls_facts(supports_ssl=False))

    def test_from_source_it_says_where_openssl_came_from(self) -> None:
        facts = tls_facts(frozen=False, bundle="", python_openssl="", images=[
            "/usr/lib/libssl.48.dylib", "/opt/homebrew/Cellar/openssl@3/3.6.5/lib/libssl.3.dylib",
            "/opt/homebrew/Cellar/openssl@3/3.6.5/lib/libcrypto.3.dylib",
        ])
        self.assertTrue(selftest.judge_tls(facts).endswith(
            "157 root certificates; libcrypto.3.dylib, libssl.3.dylib from /opt/homebrew/Cellar/openssl@3/3.6.5/lib "
            "(macOS's own libssl.48.dylib aside)"))
        fallback = tls_facts(frozen=False, active="securetransport", library="Secure Transport", images=[],
                             protocols=["TLS 1.2"])
        self.assertEqual(selftest.judge_tls(fallback),
                         "securetransport, Secure Transport, TLS 1.2, 157 root certificates; no OpenSSL from nowhere")

    def windows(self, **changes):
        facts = dict(platform="win32", frozen=True, available=["schannel", "cert-only"], active="schannel",
                     library="Secure Channel, Windows 10.0.26100", protocols=["TLS 1.2", "TLS 1.3"], roots=58,
                     images=[], bundle="", python_openssl="OpenSSL 3.0.21 1 Jul 2026")
        facts.update(changes)
        return tls_facts(**facts)

    def test_windows_uses_schannel(self) -> None:
        self.assertEqual(selftest.judge_tls(self.windows()), "schannel, Secure Channel, Windows 10.0.26100, TLS 1.2 "
                         "and TLS 1.3, 58 root certificates; no OpenSSL backend in this build")
        self.assertEqual(selftest.judge_tls(self.windows(frozen=False, available=["schannel", "openssl"])),
                         "schannel, Secure Channel, Windows 10.0.26100, TLS 1.2 and TLS 1.3, 58 root certificates")
        with self.assertRaisesRegex(RuntimeError, "using its openssl backend, not Windows' own Schannel"):
            selftest.judge_tls(self.windows(active="openssl", available=["openssl", "schannel"]))

    def test_a_windows_build_with_qts_openssl_backend_fails(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "this build has Qt's OpenSSL backend"):
            selftest.judge_tls(self.windows(available=["openssl", "schannel", "cert-only"]))

    @unittest.skipUnless(IS_MAC, "macOS only")
    def test_loaded_libraries_are_listed(self) -> None:
        images = selftest.loaded_images()
        self.assertTrue(any(path.endswith("/libSystem.B.dylib") for path in images), images[:5])
        from PySide6 import QtCore

        self.assertIn(os.path.realpath(QtCore.__file__), {os.path.realpath(path) for path in images})


def openssl_facts(**changes):
    """What a built macOS app's openssl check finds on python.org's 3.14.8."""
    facts = dict(
        frozen=True,
        running=[("Python's ssl and hashlib", (3, 5, 4)), ("Qt's TLS", (3, 5, 4))],
        files=[("Frameworks/libcrypto.3.dylib", (3, 5, 4)), ("Frameworks/libssl.3.dylib", (3, 5, 4))],
    )
    facts.update(changes)
    return selftest.OpenSSLFacts(**facts)


def libcrypto(text: bytes = b"OpenSSL 3.5.4 30 Sep 2025") -> bytes:
    """A stand-in libcrypto: binary noise around OPENSSL_VERSION_TEXT."""
    return b"\xcf\xfa\xed\xfe" + b"\x00" * 64 + text + b"\x00" + b"OpenSSL %s\x00" + b"\x00" * 64


class OpenSSLCheckTests(unittest.TestCase):
    """The openssl check: every OpenSSL a build runs or ships is at least
    OPENSSL_FLOOR."""

    def test_the_floor_is_the_lts_python_org_ships_with_3_14(self) -> None:
        # OpenSSL 3.0 reached end of life on 2026-09-07; 3.5 is the LTS.
        self.assertGreaterEqual(selftest.OPENSSL_FLOOR, (3, 5))
        self.assertIn("2030-04-08", Path(selftest.__file__).read_text())  # 3.5's end of life, beside the constant

    def test_a_built_app_on_openssl_3_5_passes(self) -> None:
        self.assertEqual(
            selftest.judge_openssl(openssl_facts()),
            "OpenSSL 3.5.4 in Python's ssl and hashlib, Qt's TLS, Frameworks/libcrypto.3.dylib, "
            "Frameworks/libssl.3.dylib; 3.5 or later required",
        )
        windows = openssl_facts(running=[("Python's ssl and hashlib", (3, 5, 4))],
                                files=[("libcrypto-3.dll", (3, 5, 4)), ("libssl-3.dll", (3, 5, 4))])
        self.assertEqual(selftest.judge_openssl(windows), "OpenSSL 3.5.4 in Python's ssl and hashlib, "
                         "libcrypto-3.dll, libssl-3.dll; 3.5 or later required")
        newer = openssl_facts(running=[("Python's ssl and hashlib", (3, 6, 5))], files=[("libcrypto-3.dll", (4, 0, 1))])
        self.assertEqual(selftest.judge_openssl(newer), "OpenSSL 3.6.5 in Python's ssl and hashlib; "
                         "OpenSSL 4.0.1 in libcrypto-3.dll; 3.5 or later required")

    def test_a_built_app_on_openssl_3_0_fails(self) -> None:
        # What setup-python's 3.13 (python.org's 3.13.15) ships.
        old = openssl_facts(running=[("Python's ssl and hashlib", (3, 0, 21))],
                            files=[("libcrypto-3.dll", (3, 0, 21)), ("libssl-3.dll", (3, 0, 21))])
        with self.assertRaises(RuntimeError) as failed:
            selftest.judge_openssl(old)
        self.assertEqual(str(failed.exception), "OpenSSL 3.0.21 in Python's ssl and hashlib, libcrypto-3.dll, "
                         "libssl-3.dll is older than 3.5, and out of support. A build must ship OpenSSL 3.5 or later "
                         "(OPENSSL_FLOOR)")

    def test_each_copy_is_held_to_the_floor(self) -> None:
        cases = {
            "Qt's TLS": openssl_facts(running=[("Python's ssl and hashlib", (3, 5, 4)), ("Qt's TLS", (3, 0, 13))]),
            "libcrypto-3-x64.dll": openssl_facts(files=[("libcrypto-3.dll", (3, 5, 4)),
                                                        ("libcrypto-3-x64.dll", (3, 0, 13))]),
            "Python's ssl and hashlib": openssl_facts(running=[("Python's ssl and hashlib", (1, 1, 1))]),
        }
        for what, facts in cases.items():
            with self.subTest(what=what), self.assertRaisesRegex(RuntimeError, rf"in {re.escape(what)} is older than 3\.5"):
                selftest.judge_openssl(facts)

    def test_a_copy_whose_version_cant_be_told_fails(self) -> None:
        facts = openssl_facts(files=[("Frameworks/libcrypto.3.dylib", None), ("Frameworks/libssl.3.dylib", None)])
        with self.assertRaisesRegex(RuntimeError, "can't tell which OpenSSL Frameworks/libcrypto.3.dylib, "
                                                  "Frameworks/libssl.3.dylib are"):
            selftest.judge_openssl(facts)
        with self.assertRaisesRegex(RuntimeError, "can't tell which OpenSSL Qt's TLS is"):
            selftest.judge_openssl(openssl_facts(running=[("Python's ssl and hashlib", (3, 5, 4)), ("Qt's TLS", None)]))

    def test_a_built_app_with_no_openssl_file_to_read_fails(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "found no OpenSSL library inside the app"):
            selftest.judge_openssl(openssl_facts(files=[]))

    def test_from_source_an_old_openssl_is_a_skip_that_says_why(self) -> None:
        facts = openssl_facts(frozen=False, running=[("Python's ssl and hashlib", (3, 0, 21))], files=[])
        with self.assertRaisesRegex(selftest.Skipped, r"^not a built app, and a build would fail: OpenSSL 3\.0\.21 in "
                                                      r"Python's ssl and hashlib is older than 3\.5"):
            selftest.judge_openssl(facts)
        fine = openssl_facts(frozen=False, files=[])
        self.assertEqual(selftest.judge_openssl(fine), "OpenSSL 3.5.4 in Python's ssl and hashlib, Qt's TLS; "
                         "3.5 or later required")

    def test_the_floor_is_one_constant(self) -> None:
        with mock.patch.object(selftest, "OPENSSL_FLOOR", (3, 6)):
            with self.assertRaisesRegex(RuntimeError, r"OpenSSL 3\.5\.4 in .* is older than 3\.6.*ship OpenSSL 3\.6"):
                selftest.judge_openssl(openssl_facts())

    def test_versions_from_python_and_qt(self) -> None:
        self.assertEqual(selftest.openssl_version_from_info((3, 5, 0, 4, 0)), (3, 5, 4))
        self.assertEqual(selftest.openssl_version_from_info((3, 0, 0, 21, 0)), (3, 0, 21))
        self.assertEqual(selftest.openssl_version_from_info((1, 1, 1, 23, 15)), (1, 1, 1))  # 1.1.1w
        self.assertEqual(selftest.openssl_version_from_number(0x30500040), (3, 5, 4))
        self.assertEqual(selftest.openssl_version_from_number(0x300000D0), (3, 0, 13))
        self.assertEqual(selftest.openssl_version_from_number(0x40000010), (4, 0, 1))
        self.assertEqual(selftest.openssl_version_from_number(0x1010117F), (1, 1, 1))
        import ssl

        # The two agree on this Python's own OpenSSL.
        self.assertEqual(selftest.openssl_version_from_info(ssl.OPENSSL_VERSION_INFO),
                         selftest.openssl_version_from_number(ssl.OPENSSL_VERSION_NUMBER))
        self.assertTrue(ssl.OPENSSL_VERSION.startswith(
            "OpenSSL " + ".".join(map(str, selftest.openssl_version_from_info(ssl.OPENSSL_VERSION_INFO)))))

    def test_library_files_are_found_and_read(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "Frameworks" / "python3.14" / "lib-dynload").mkdir(parents=True)
            (root / "Resources").mkdir()
            (root / "Frameworks" / "libcrypto.3.dylib").write_bytes(libcrypto() + libcrypto(b"OpenSSL 3.5.4 30 Sep 2025"))
            (root / "Frameworks" / "libssl.3.dylib").write_bytes(b"\xcf\xfa\xed\xfe no version of its own")
            # PyInstaller links Resources to Frameworks: one copy, counted once.
            os.symlink("../Frameworks/libcrypto.3.dylib", root / "Resources" / "libcrypto.3.dylib")
            # Not OpenSSL's libraries: Python's modules, a static archive, an unversioned link.
            (root / "Frameworks" / "python3.14" / "lib-dynload" / "_ssl.cpython-314-darwin.so").write_bytes(b"x")
            (root / "Frameworks" / "libcrypto.a").write_bytes(libcrypto(b"OpenSSL 3.0.1 14 Dec 2021"))
            os.symlink("libcrypto.3.dylib", root / "Frameworks" / "libcrypto.dylib")
            self.assertEqual(selftest.openssl_files(str(root)), [
                ("Frameworks/libcrypto.3.dylib", (3, 5, 4)), ("Frameworks/libssl.3.dylib", (3, 5, 4)),
            ])
            # A universal binary's slices disagreeing: the older counts.
            (root / "Frameworks" / "libcrypto.3.dylib").write_bytes(libcrypto() + libcrypto(b"OpenSSL 3.0.21 1 Jul 2026"))
            self.assertEqual(selftest.openssl_files(str(root))[0], ("Frameworks/libcrypto.3.dylib", (3, 0, 21)))

    def test_windows_names_and_a_libssl_without_its_libcrypto(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "libcrypto-3.dll").write_bytes(b"MZ" + libcrypto(b"OpenSSL 3.5.4 30 Sep 2025"))
            (root / "libssl-3.dll").write_bytes(b"MZ")
            (root / "libssl-3-x64.dll").write_bytes(b"MZ")  # whose libcrypto isn't there
            (root / "libcrypto.so.3").write_bytes(b"\x7fELF" + libcrypto(b"OpenSSL 3.6.0 1 Oct 2025"))
            (root / "libcrypto-3-x64.dll").write_bytes(b"MZ nothing to read")
            self.assertEqual(selftest.openssl_files(str(root)), [
                ("libcrypto-3-x64.dll", None), ("libcrypto-3.dll", (3, 5, 4)), ("libcrypto.so.3", (3, 6, 0)),
                ("libssl-3-x64.dll", None), ("libssl-3.dll", (3, 5, 4)),
            ])

    def check(self, frozen: bool, backend: str = "openssl", qt_number: int = 0x30500040, root: str = ""):
        """check_openssl with Qt's backend and number made up, and, frozen,
        `root` as the built app's files."""
        from PySide6.QtNetwork import QSslSocket

        from app import tls

        frozen_app = mock.patch.object(sys, "frozen", True, create=True) if frozen else contextlib.nullcontext()
        with frozen_app, mock.patch.object(tls, "use_preferred_backend", return_value=backend), \
                mock.patch.object(QSslSocket, "sslLibraryVersionNumber", return_value=qt_number), \
                mock.patch.object(selftest, "built_app_files", return_value=root), \
                mock.patch.object(selftest, "judge_openssl", side_effect=lambda facts: facts):
            return selftest.check_openssl()

    def test_the_check_asks_python_and_qt_and_reads_a_built_apps_files(self) -> None:
        import ssl
        import tempfile

        python = selftest.openssl_version_from_info(ssl.OPENSSL_VERSION_INFO)
        facts = self.check(frozen=False)
        self.assertEqual(facts.running, [("Python's ssl and hashlib", python), ("Qt's TLS", (3, 5, 4))])
        self.assertFalse(facts.frozen)
        self.assertEqual(facts.files, [])
        # Schannel (Windows) runs no OpenSSL; Qt's number then means nothing.
        self.assertEqual(self.check(frozen=False, backend="schannel").running, [("Python's ssl and hashlib", python)])
        self.assertEqual(self.check(frozen=False, qt_number=0).running[1], ("Qt's TLS", None))
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "libcrypto-3.dll").write_bytes(b"MZ" + libcrypto())
            facts = self.check(frozen=True, backend="schannel", root=folder)
        self.assertTrue(facts.frozen)
        self.assertEqual(facts.files, [("libcrypto-3.dll", (3, 5, 4))])

    def test_a_built_apps_files_are_its_contents_or_what_pyinstaller_unpacked(self) -> None:
        with mock.patch.object(sys, "_MEIPASS", "/Applications/Example.app/Contents/Frameworks", create=True), \
                mock.patch.object(sys, "platform", "darwin"):
            self.assertEqual(selftest.built_app_files(), os.path.realpath("/Applications/Example.app/Contents"))
        with mock.patch.object(sys, "_MEIPASS", "/tmp/_MEI12345", create=True), mock.patch.object(sys, "platform", "win32"):
            self.assertEqual(selftest.built_app_files(), os.path.realpath("/tmp/_MEI12345"))


@unittest.skipUnless(IS_MAC, "the built-app checks are macOS only")
class BuiltAppCheckTests(unittest.TestCase):
    """The checks that only mean something inside a built app, pretending."""

    def check_environment(self, environment, path=selftest.SYSTEM_PATH):
        bundle = "/Applications/Example.app/Contents"
        if path is not None:
            environment = {"PATH": path, **environment}
        with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(
            sys, "_MEIPASS", f"{bundle}/Frameworks", create=True
        ), mock.patch.dict(os.environ, environment, clear=True):
            return selftest.check_environment()

    def test_a_cleaned_environment_passes(self) -> None:
        bundle = "/Applications/Example.app/Contents"
        detail = self.check_environment({
            "HOME": "/Users/someone",
            "OPENSSL_CONF": os.devnull,
            "QT_QPA_PLATFORM": "offscreen",  # set by the self-test itself
            "QT_PLUGIN_PATH": f"{bundle}/Frameworks/PySide6/Qt/plugins",  # PyInstaller's own hook
            "QML2_IMPORT_PATH": f"{bundle}/Resources/PySide6/Qt/qml:{bundle}/Frameworks/PySide6/Qt/qml",
        })
        self.assertIn("clean", detail)
        self.assertIn("PATH=/usr/bin:/bin:/usr/sbin:/sbin", detail)

    def test_anything_left_over_fails(self) -> None:
        for name, value in [
            ("OPENSSL_MODULES", "/tmp/evil"),
            ("SSL_CERT_FILE", "/tmp/ca.pem"),
            ("QT_QPA_PLATFORM_PLUGIN_PATH", "/tmp/evil"),
            ("DYLD_INSERT_LIBRARIES", "/tmp/evil.dylib"),
            ("PYTHONPATH", "/tmp/evil"),
            ("QT_PLUGIN_PATH", "/tmp/evil"),  # outside the bundle
            ("BASH_ENV", "/tmp/evil.sh"),
            ("ENV", "/tmp/evil.sh"),
            ("BASH_FUNC_sleep%%", "() { /tmp/evil; }"),
            ("__BASH_FUNC<sleep>()", "() { /tmp/evil; }"),
            ("SHELLOPTS", "xtrace"),
        ]:
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, name):
                self.check_environment({"OPENSSL_CONF": os.devnull, name: value})

    def test_path_must_be_pinned_to_the_system_folders(self) -> None:
        for path in (None, "/tmp/evil:/usr/bin:/bin:/usr/sbin:/sbin", "/usr/bin:/bin"):
            with self.subTest(path=path), self.assertRaisesRegex(RuntimeError, "PATH"):
                self.check_environment({"OPENSSL_CONF": os.devnull}, path=path)

    def check_child_processes(self, pgrep=None, path=selftest.SYSTEM_PATH):
        import app.main

        command = mock.patch.object(app.main, "_pgrep_command", return_value=pgrep) if pgrep else contextlib.nullcontext()
        with mock.patch.object(sys, "frozen", True, create=True), mock.patch.dict(os.environ, {"PATH": path}), \
                command, mock.patch.object(app.main, "_other_copies_running", side_effect=AssertionError("not run")):
            return selftest.check_child_processes()

    def fake_pgrep(self, folder: Path, script: str) -> list:
        fake = folder / "pgrep"
        fake.write_text("#!/bin/sh\n" + script)
        fake.chmod(0o755)
        return [str(fake), "-x", "DoubleClickFixer"]

    def test_child_processes_run_from_the_system(self) -> None:
        # The real /usr/bin/pgrep, with the app's own arguments.
        import app.main

        self.assertEqual(app.main._pgrep_command(), ["/usr/bin/pgrep", "-x", os.path.basename(sys.executable)])
        self.assertRegex(
            self.check_child_processes(),
            r"^pgrep ran \((no other copy|another copy is running)\), bash -p runs /bin/sleep$",
        )

    def test_pgrep_runs_itself_and_its_answer_is_read(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            called = folder / "called"
            command = self.fake_pgrep(folder, f'echo "$@" > "{called}"; echo {os.getpid()}; echo 999999; exit 0\n')
            self.assertTrue(self.check_child_processes(command).startswith("pgrep ran (another copy is running)"))
            self.assertEqual(called.read_text().split(), ["-x", "DoubleClickFixer"])
            command = self.fake_pgrep(folder, f"echo {os.getpid()}; exit 0\n")  # only itself
            self.assertTrue(self.check_child_processes(command).startswith("pgrep ran (no other copy)"))
            command = self.fake_pgrep(folder, "exit 1\n")
            self.assertTrue(self.check_child_processes(command).startswith("pgrep ran (no other copy)"))

    def test_a_pgrep_that_cannot_look_fails_the_child_process_check(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            for code in (2, 3):
                with self.subTest(code=code), self.assertRaisesRegex(RuntimeError, f"pgrep exited {code}: no"):
                    self.check_child_processes(self.fake_pgrep(folder, f"echo no >&2; exit {code}\n"))
            with self.assertRaises(FileNotFoundError):
                self.check_child_processes([str(folder / "missing"), "-x", "DoubleClickFixer"])

    def test_a_program_earlier_on_path_fails_the_child_process_check(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            fake = Path(folder) / "sleep"
            fake.write_text("#!/bin/sh\nexit 0\n")
            fake.chmod(0o755)
            with self.assertRaisesRegex(RuntimeError, "bash -p found sleep at '.*/sleep'"):
                self.check_child_processes(path=f"{folder}:{selftest.SYSTEM_PATH}")

    def test_openssl_conf_must_point_at_an_empty_file(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "OPENSSL_CONF"):
            self.check_environment({})
        with self.assertRaisesRegex(RuntimeError, "OPENSSL_CONF"):
            self.check_environment({"OPENSSL_CONF": "/tmp/evil.cnf"})

    def test_a_built_app_must_run_with_the_hardened_runtime(self) -> None:
        self.assertIsInstance(selftest.code_signing_flags(), int)  # the real call works here
        with mock.patch.object(sys, "frozen", True, create=True):
            with mock.patch.object(selftest, "code_signing_flags", return_value=0x22000201):  # no CS_RUNTIME
                with self.assertRaisesRegex(RuntimeError, "not running with the hardened runtime"):
                    selftest.check_code_signature()
            with mock.patch.object(selftest, "code_signing_flags", return_value=0x22010201):
                self.assertIn("hardened runtime", selftest.check_code_signature())


class RunScriptTests(unittest.TestCase):
    def test_self_test_runs_before_the_app_is_even_imported(self) -> None:
        import app.main

        with mock.patch.object(sys, "argv", ["run.py", "--self-test"]), mock.patch.object(
            selftest, "main", return_value=0
        ) as self_test, mock.patch.object(app.main, "main", side_effect=AssertionError("started the app")), \
                mock.patch.object(app.main, "_instance_lock", side_effect=AssertionError("took the lock")):
            with self.assertRaises(SystemExit) as exited:
                runpy.run_path(str(ROOT / "run.py"), run_name="__main__")
        self.assertEqual(exited.exception.code, 0)
        self_test.assert_called_once_with()

    def test_self_test_from_source_in_a_fresh_process(self) -> None:
        """The whole thing, in a process with no Qt application yet."""
        environment = {key: value for key, value in os.environ.items() if key != "QT_QPA_PLATFORM"}
        finished = subprocess.run(
            [sys.executable, str(ROOT / "run.py"), "--self-test"],
            capture_output=True, text=True, timeout=120, env=environment, cwd=ROOT,
        )
        self.assertEqual(finished.returncode, 0, finished.stdout + finished.stderr)
        self.assertIn("Qt platform plugin: ok (offscreen plugin loaded and unloaded", finished.stdout)


class ScrubHookTests(unittest.TestCase):
    def run_hook(self, environment, argv=("DoubleClickFixer",)):
        with mock.patch.dict(os.environ, environment, clear=True), mock.patch.object(sys, "argv", list(argv)):
            runpy.run_path(str(SCRUB_HOOK))
            return dict(os.environ)

    def test_variables_that_load_outside_code_are_removed(self) -> None:
        left = self.run_hook({
            "HOME": "/Users/someone",
            "PATH": "/tmp/evil:/usr/local/bin:/usr/bin:/bin",
            "TMPDIR": "/var/folders/x/T/",
            "OPENSSL_CONF": "/tmp/evil.cnf",
            "OPENSSL_MODULES": "/tmp/evil",
            "OPENSSL_ENGINES": "/tmp/evil",
            "SSL_CERT_FILE": "/tmp/ca.pem",
            "SSL_CERT_DIR": "/tmp/certs",
            "QT_PLUGIN_PATH": "/tmp/evil",
            "QT_QPA_PLATFORM_PLUGIN_PATH": "/tmp/evil",
            "QT_QPA_PLATFORM": "cocoa",
            "QML2_IMPORT_PATH": "/tmp/evil",
            "PYTHONPATH": "/tmp/evil",
            "PYTHONHOME": "/tmp/evil",
            "DYLD_INSERT_LIBRARIES": "/tmp/evil.dylib",
            "DYLD_LIBRARY_PATH": "/tmp/evil",
            "_PYI_APPLICATION_HOME_DIR": "/Applications/Example.app/Contents/Frameworks",
            "PYINSTALLER_RESET_ENVIRONMENT": "1",
            "BASH_ENV": "/tmp/evil.sh",
            "ENV": "/tmp/evil.sh",
            "BASH_FUNC_sleep%%": "() { /tmp/evil; }",
            "__BASH_FUNC<sleep>()": "() { /tmp/evil; }",
            "SHELLOPTS": "xtrace",
            "BASHOPTS": "extdebug",
        })
        self.assertEqual(left, {
            "HOME": "/Users/someone",
            # Pinned: programs the app starts by name come from the system.
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "TMPDIR": "/var/folders/x/T/",
            # What PyInstaller's bootloader itself uses stays.
            "_PYI_APPLICATION_HOME_DIR": "/Applications/Example.app/Contents/Frameworks",
            "PYINSTALLER_RESET_ENVIRONMENT": "1",
            # OpenSSL's compiled-in config may sit in a user-writable folder.
            "OPENSSL_CONF": os.devnull,
        })

    def test_the_self_test_keeps_its_choice_of_qt_platform(self) -> None:
        left = self.run_hook(
            {"QT_QPA_PLATFORM": "offscreen", "QT_QPA_PLATFORM_PLUGIN_PATH": "/tmp/evil"},
            argv=("DoubleClickFixer", "--self-test"),
        )
        self.assertEqual(left, {
            "QT_QPA_PLATFORM": "offscreen", "OPENSSL_CONF": os.devnull, "PATH": selftest.SYSTEM_PATH,
        })

    def test_the_hook_and_the_self_test_agree(self) -> None:
        hook = SCRUB_HOOK.read_text(encoding="utf-8")
        self.assertIn(f'os.environ["PATH"] = "{selftest.SYSTEM_PATH}"', hook)
        for prefix in selftest.SCRUBBED_PREFIXES:
            self.assertIn(f'"{prefix}"', hook)
        for name in selftest.SCRUBBED_NAMES:
            self.assertIn(f'"{name}"', hook)

    def test_the_hook_is_wired_into_the_macos_build_only(self) -> None:
        spec = (ROOT / "doubleclick-fixer.spec").read_text(encoding="utf-8")
        self.assertIn(
            'RUNTIME_HOOKS = ["installer/runtime_hooks/scrub_env.py"] if sys.platform == "darwin" else []', spec
        )
        self.assertIn("runtime_hooks=RUNTIME_HOOKS", spec)


if __name__ == "__main__":
    unittest.main()
