"""The diagnostics log and report: written off the caller's thread, catches
what would otherwise vanish, and copies cleanly."""

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
        self.assertIn("DoubleClick Fixer 9.9.9", text)
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
        self.assertIn("DoubleClick Fixer 9.9.9", text)
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

        page = GeneralPage(AppController())
        self.addCleanup(page.deleteLater)
        page.diagnostics_button.click()
        text = application.clipboard().text()
        self.assertIn(f"DoubleClick Fixer {__version__}", text)
        self.assertIn("filter running: False", text)
        self.assertIn("Settings:", text)
        self.assertIn("Copied", page.diagnostics_row.detail.text())
        page._diagnostics_timer.timeout.emit()
        self.assertEqual(page.diagnostics_row.detail.text(), DIAGNOSTICS_DETAIL)


if __name__ == "__main__":
    unittest.main()
