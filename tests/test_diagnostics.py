"""The diagnostics log and report: written off the caller's thread, catches
what would otherwise vanish, and copies cleanly."""

try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

import faulthandler
import logging
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from run import _unhide_qt_plugins

_unhide_qt_plugins()

from app import diagnostics


class DiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        from app import settings

        self.directory = Path(tempfile.mkdtemp())
        patcher = mock.patch.object(settings, "config_dir", return_value=self.directory)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Other test modules quiet the app's logger; this one needs it heard.
        app_logger = logging.getLogger("app")
        propagate = app_logger.propagate
        app_logger.propagate = True
        self.addCleanup(setattr, app_logger, "propagate", propagate)
        self.hooks = (sys.excepthook, threading.excepthook, sys.unraisablehook)
        diagnostics.setup("9.9.9")
        self.addCleanup(diagnostics.shutdown)

    def flush(self) -> None:
        """Let the writer thread finish what is queued, as quitting does."""
        diagnostics.shutdown()

    def log_text(self) -> str:
        return (self.directory / diagnostics.LOG_NAME).read_text(encoding="utf-8")

    def test_start_up_is_logged(self) -> None:
        self.flush()
        text = self.log_text()
        self.assertIn("Mouse Double-Click Fixer 9.9.9", text)
        self.assertIn(diagnostics.os_description(), text)

    def test_the_file_is_written_by_its_own_thread(self) -> None:
        writers = []
        real_emit = logging.handlers.RotatingFileHandler.emit

        def emit(handler, record):
            writers.append(threading.current_thread())
            real_emit(handler, record)

        with mock.patch.object(logging.handlers.RotatingFileHandler, "emit", emit):
            worker = threading.Thread(target=lambda: logging.getLogger("app.platform").warning("from the hook thread"))
            worker.start()
            worker.join()
            self.flush()
        self.assertTrue(writers)
        self.assertNotIn(worker, writers)
        self.assertNotIn(threading.main_thread(), writers)
        self.assertIn("from the hook thread", self.log_text())

    def test_uncaught_exceptions_are_logged(self) -> None:
        try:
            raise ValueError("main thread trouble")
        except ValueError:
            with mock.patch.object(sys, "stderr", None):
                sys.excepthook(*sys.exc_info())

        def fail() -> None:
            raise KeyError("worker trouble")

        worker = threading.Thread(target=fail, name="dcf-test-worker")
        with mock.patch.object(sys, "stderr", None):
            worker.start()
            worker.join()
        self.flush()
        text = self.log_text()
        self.assertIn("main thread trouble", text)
        self.assertIn("worker trouble", text)
        self.assertIn("dcf-test-worker", text)

    def test_unraisable_exceptions_are_logged(self) -> None:
        class Broken:
            def __del__(self):
                raise RuntimeError("lost in __del__")

        with mock.patch.object(sys, "stderr", None):
            Broken()  # collected at once; Python can only report it
        self.flush()
        self.assertIn("lost in __del__", self.log_text())

    def test_hard_crashes_go_to_crash_log(self) -> None:
        self.assertTrue(faulthandler.is_enabled())
        self.assertTrue((self.directory / diagnostics.CRASH_NAME).exists())

    def crash_text(self) -> str:
        return (self.directory / diagnostics.CRASH_NAME).read_text(encoding="utf-8")

    def test_each_launch_dates_crash_log(self) -> None:
        diagnostics.shutdown()
        diagnostics.setup("9.9.10")
        lines = self.crash_text().splitlines()
        self.assertEqual(len(lines), 2, "one line per launch")
        self.assertTrue(lines[0].startswith(f"{diagnostics.CRASH_HEADER} 9.9.9 started 20"))
        self.assertTrue(lines[1].startswith(f"{diagnostics.CRASH_HEADER} 9.9.10 started 20"))

    def test_crash_log_is_cut_back_at_launch(self) -> None:
        diagnostics.shutdown()
        path = self.directory / diagnostics.CRASH_NAME
        dumps = "".join(f"Windows fatal exception: code 0x8001010d, dump {number}\n" for number in range(20000))
        path.write_text(dumps, encoding="utf-8")
        self.assertGreater(path.stat().st_size, diagnostics.CRASH_MAX_BYTES)
        diagnostics.setup("9.9.9")
        self.assertLessEqual(path.stat().st_size, diagnostics.CRASH_MAX_BYTES + 200)
        lines = self.crash_text().splitlines()
        self.assertTrue(lines[0].startswith("Windows fatal exception"), "starts on a whole line")
        self.assertEqual(lines[-2], "Windows fatal exception: code 0x8001010d, dump 19999", "the newest is kept")
        self.assertTrue(lines[-1].startswith(diagnostics.CRASH_HEADER))

    def test_report_shows_crash_log_only_with_something_in_it(self) -> None:
        self.flush()
        self.assertNotIn("crash.log", diagnostics.report("9.9.9", {}, {}), "launch lines alone")
        with open(self.directory / diagnostics.CRASH_NAME, "a", encoding="utf-8") as handle:
            handle.write("Fatal Python error: Segmentation fault\n")
        text = diagnostics.report("9.9.9", {}, {})
        self.assertIn("crash.log, last lines:", text)
        self.assertIn("Segmentation fault", text)

    def test_launch_lines_from_before_1_0_still_count_as_nothing_wrong(self) -> None:
        self.flush()
        path = self.directory / diagnostics.CRASH_NAME
        old = "--- DoubleClick Fixer 0.5.3 started 2026-10-08T10:00:00+08:00 ---\n"
        path.write_text(old + path.read_text(encoding="utf-8"), encoding="utf-8")
        self.assertNotIn("crash.log", diagnostics.report("9.9.9", {}, {}))
        self.assertTrue(path.read_text(encoding="utf-8").splitlines()[-1].startswith("--- Mouse Double-Click Fixer 9.9.9"))

    def test_shutdown_puts_the_hooks_back(self) -> None:
        diagnostics.shutdown()
        self.assertEqual((sys.excepthook, threading.excepthook, sys.unraisablehook), self.hooks)
        self.assertFalse(faulthandler.is_enabled())

    def test_the_log_rotates(self) -> None:
        handler = diagnostics._state.listener.handlers[0]
        self.assertIsInstance(handler, logging.handlers.RotatingFileHandler)
        self.assertEqual(handler.maxBytes, diagnostics.MAX_BYTES)
        self.assertEqual(handler.backupCount, diagnostics.BACKUPS)

    def test_report_has_what_a_bug_report_needs(self) -> None:
        for number in range(250):
            logging.getLogger("app.test").info("line %d", number)
        logging.getLogger("app.test").info("saved under %s", Path.home() / "Library")
        self.flush()
        text = diagnostics.report(
            "9.9.9",
            {"filter running": True, "tap resets": 2},
            {"threshold_ms": 45, "window_geometry": "AdnQywADAAAAAA==", "buttons": ["left"]},
        )
        self.assertIn("Mouse Double-Click Fixer 9.9.9", text)
        self.assertIn(diagnostics.os_description(), text)
        self.assertIn("filter running: True", text)
        self.assertIn('"threshold_ms": 45', text)
        self.assertNotIn("window_geometry", text)
        self.assertIn("line 249", text)
        self.assertNotIn("line 48\n", text, "only the last lines")
        self.assertNotIn(str(Path.home()), text, "the home folder shows as ~")
        self.assertIn(str(Path("~") / "Library"), text)

    def test_report_reaches_into_the_previous_file_after_a_rotation(self) -> None:
        self.flush()
        (self.directory / f"{diagnostics.LOG_NAME}.1").write_text("old line\n", encoding="utf-8")
        (self.directory / diagnostics.LOG_NAME).write_text("new line\n", encoding="utf-8")
        self.assertEqual(diagnostics.recent_lines(), ["old line", "new line"])


class CopyDiagnosticsTests(unittest.TestCase):
    def test_the_button_copies_a_report(self) -> None:
        from PySide6.QtWidgets import QApplication

        from app import settings

        application = QApplication.instance() or QApplication([])
        directory = Path(tempfile.mkdtemp())
        for patch in (
            mock.patch.object(settings, "config_dir", return_value=directory),
            mock.patch.object(settings, "LEGACY_PATH", directory / "absent.json"),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        from app import __version__
        from app.controller import AppController
        from app.ui.window import DIAGNOSTICS_DETAIL, GeneralPage

        controller = AppController()
        self.addCleanup(controller.shutdown)  # before the patches are undone
        page = GeneralPage(controller)
        self.addCleanup(page.deleteLater)
        page.diagnostics_button.click()
        text = application.clipboard().text()
        self.assertIn(f"Mouse Double-Click Fixer {__version__}", text)
        self.assertIn("filter running: False", text)
        self.assertIn("Settings:", text)
        self.assertIn("Copied", page.diagnostics_row.detail.text())
        page._diagnostics_timer.timeout.emit()
        self.assertEqual(page.diagnostics_row.detail.text(), DIAGNOSTICS_DETAIL)


if __name__ == "__main__":
    unittest.main()
