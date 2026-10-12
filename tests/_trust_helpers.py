"""Shared by the 1.1 trust-feature tests: a controller with no hook, no real
shortcut, no process list and no game check, on settings in a throwaway
folder. Import it after _isolation."""

import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from run import _unhide_qt_plugins

_unhide_qt_plugins()

from PySide6.QtWidgets import QApplication

from app.core import Button, ClickEvent
from app.platform import HookError

logging.getLogger("app").addHandler(logging.NullHandler())
logging.getLogger("app").propagate = False


class FakeFilter:
    """Stands in for GlobalClickFilter: no hook, just the lifecycle."""

    fail_with = None
    instances: list = []

    def __init__(self, config, on_event=None, on_error=None, permission_ok=None,
                 on_permission_lost=None, on_device=None, on_wheel=None) -> None:
        self.config = config
        self.on_event = on_event
        self.started = False
        self.stopped = False
        self.updates = []
        FakeFilter.instances.append(self)

    @property
    def running(self) -> bool:
        return self.started and not self.stopped

    def start(self) -> None:
        if FakeFilter.fail_with is not None:
            raise HookError(FakeFilter.fail_with)
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def update(self, config) -> None:
        self.updates.append(config)
        self.config = config

    def seen_devices(self) -> list:
        return []


class FakeShortcut:
    """Stands in for pause.Shortcut: records start and stop, and press()
    calls the controller as the system would."""

    made: list = []
    refuse = ""

    def __init__(self, on_press) -> None:
        self.on_press = on_press
        self.registered = False
        self.error = ""
        self.starts = 0
        self.stops = 0
        FakeShortcut.made.append(self)

    def start(self) -> bool:
        self.starts += 1
        if FakeShortcut.refuse:
            self.error = FakeShortcut.refuse
            return False
        self.registered = True
        return True

    def stop(self) -> None:
        self.stops += 1
        self.registered = False

    def press(self) -> None:
        self.on_press()


class Clock:
    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def bounce(button=Button.LEFT, gap_ms=10.0) -> ClickEvent:
    return ClickEvent(button, True, False, gap_ms, None)


def press(button=Button.LEFT, gap_ms=300.0) -> ClickEvent:
    return ClickEvent(button, True, True, gap_ms, None)


class ControllerCase(unittest.TestCase):
    """A fresh AppController per test (see the module notes). `self.games`
    is what the game check answers; `self.found` the utilities running."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def make_settings_dir(self) -> Path:
        from app import settings

        directory = Path(tempfile.mkdtemp())
        for target, value in (("config_dir", mock.Mock(return_value=directory)), ("LEGACY_PATH", directory / "x")):
            patcher = mock.patch.object(settings, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        return directory

    def setUp(self) -> None:
        self.directory = self.make_settings_dir()
        FakeFilter.fail_with = None
        FakeFilter.instances = []
        FakeShortcut.made = []
        FakeShortcut.refuse = ""
        patcher = mock.patch("app.controller.GlobalClickFilter", FakeFilter)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.controller = self.make_controller()

    def make_controller(self):
        from app.controller import AppController

        controller = AppController()
        self.addCleanup(controller.shutdown)
        self.clock = Clock()
        controller._clock = self.clock
        self.game = None
        self.found = []
        controller._game_check = lambda: self.game
        controller._conflict_scan = lambda: list(self.found)
        controller._make_shortcut = FakeShortcut
        return controller

    def process_events(self) -> None:
        self.application.processEvents()
