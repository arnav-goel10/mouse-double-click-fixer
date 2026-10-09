"""The --self-test a release runs on the built app."""

import contextlib
import io
import os
import platform
import runpy
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app import selftest

ROOT = Path(__file__).resolve().parent.parent
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
        self.assertTrue(lines[0].startswith("DoubleClick Fixer "))
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
        if IS_MAC:
            self.assertTrue(result["PyObjC callback"].startswith("ok"))
            self.assertIn(result["event tap"].split(" ")[0], ("ok", "skipped:"))
        else:
            self.assertEqual(result["PyObjC callback"], "skipped: macOS only")
        # Only a built app is signed and has its environment cleaned.
        self.assertEqual(result["environment"], "skipped: " + ("not a built app" if IS_MAC else "macOS only"))
        self.assertEqual(result["code signature"], "skipped: " + ("not a built app" if IS_MAC else "macOS only"))

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

    def test_a_missing_platform_plugin_is_reported_rather_than_aborting(self) -> None:
        from PySide6.QtCore import QCoreApplication

        with mock.patch.dict(os.environ, {"QT_QPA_PLATFORM": "nosuchplatform"}), mock.patch.object(
            QCoreApplication, "instance", return_value=None
        ):
            with self.assertRaisesRegex(RuntimeError, "no 'nosuchplatform' platform plugin"):
                selftest.check_qt_platform()


@unittest.skipUnless(IS_MAC, "the built-app checks are macOS only")
class BuiltAppCheckTests(unittest.TestCase):
    """The checks that only mean something inside a built app, pretending."""

    def check_environment(self, environment):
        bundle = "/Applications/Example.app/Contents"
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

    def test_anything_left_over_fails(self) -> None:
        for name, value in [
            ("OPENSSL_MODULES", "/tmp/evil"),
            ("SSL_CERT_FILE", "/tmp/ca.pem"),
            ("QT_QPA_PLATFORM_PLUGIN_PATH", "/tmp/evil"),
            ("DYLD_INSERT_LIBRARIES", "/tmp/evil.dylib"),
            ("PYTHONPATH", "/tmp/evil"),
            ("QT_PLUGIN_PATH", "/tmp/evil"),  # outside the bundle
        ]:
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, name):
                self.check_environment({"OPENSSL_CONF": os.devnull, name: value})

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


if __name__ == "__main__":
    unittest.main()
