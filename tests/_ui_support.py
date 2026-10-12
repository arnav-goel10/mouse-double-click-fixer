"""What the UI tests share: a controller over a stand-in hook in a settings
folder of its own, either platform's layout on any computer, and a way to
leave nothing running behind a test.

Import after _isolation, which keeps every test off the real machine.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import logging

# The app logs failures on purpose; keep them out of the test output.
logging.getLogger("app").addHandler(logging.NullHandler())
logging.getLogger("app").propagate = False

from run import _unhide_qt_plugins

_unhide_qt_plugins()

from PySide6.QtWidgets import QApplication

try:
    from _contract_standin import Conflict, Health, Suggestion, controller_class, standing_in
except ImportError:  # run as tests.<module> from the repository root
    from tests._contract_standin import Conflict, Health, Suggestion, controller_class, standing_in

__all__ = [
    "Conflict", "FakeFilter", "Health", "Suggestion", "as_platform", "application", "controller_class",
    "make_controller", "patch_settings", "standing_in", "stop_timers",
]


def application() -> QApplication:
    return QApplication.instance() or QApplication([])


class FakeFilter:
    """Stands in for GlobalClickFilter: the lifecycle without a hook."""

    fail_with = None

    def __init__(self, config, on_event=None, on_error=None, permission_ok=None,
                 on_permission_lost=None, on_device=None, on_wheel=None) -> None:
        self.config = config
        self.on_event = on_event
        self.on_device = on_device
        self.on_wheel = on_wheel
        self.updates = []
        self.started = self.stopped = False
        self.on_permission_lost = on_permission_lost

    @property
    def running(self) -> bool:
        return self.started and not self.stopped

    def start(self) -> None:
        if FakeFilter.fail_with:
            from app.platform import HookError

            raise HookError(FakeFilter.fail_with)
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def update(self, config) -> None:
        self.updates.append(config)

    def seen_devices(self) -> list:
        return list(getattr(self, "devices", []))


def patch_settings(test) -> Path:
    """Point the settings (and the window's own state) at a new folder for
    `test`, undone when it ends. Returns the folder."""
    from app import settings

    directory = Path(tempfile.mkdtemp())
    for patch in (
        mock.patch.object(settings, "config_dir", return_value=directory),
        mock.patch.object(settings, "LEGACY_PATH", directory / "absent.json"),
    ):
        patch.start()
        test.addCleanup(patch.stop)
    return directory


def make_controller(test, hook: bool = True):
    """The controller the UI is built on (the 1.1 contract filled in by the
    stand-in until the real one has it), over FakeFilter when `hook`, with
    the permission granted. Shut down when the test ends, before any patch
    is undone."""
    if hook:
        FakeFilter.fail_with = None
        for patch in (
            mock.patch("app.controller.GlobalClickFilter", FakeFilter),
            mock.patch("app.permissions.needs_accessibility", return_value=False),
        ):
            patch.start()
            test.addCleanup(patch.stop)
    controller = controller_class()()
    test.addCleanup(controller.shutdown)
    return controller


#: The modules that choose between the two layouts by IS_MAC.
def _platform_modules():
    from app.ui import base, menus, panes, theme, tray, tune, widgets, window

    return (base, menus, panes, tray, tune, widgets, window)


@contextlib.contextmanager
def as_platform(mac: bool):
    """Build either platform's layout, whatever this computer is."""
    from app.ui import theme

    with contextlib.ExitStack() as stack:
        for module in _platform_modules():
            stack.enter_context(mock.patch.object(module, "IS_MAC", mac))
        stack.enter_context(mock.patch.object(theme, "IS_MAC", mac))
        stack.enter_context(mock.patch.object(theme, "IS_WINDOWS", not mac))
        yield


def stop_timers(*windows) -> None:
    """Stop every timer in `windows` and hide them: a window outlives its
    test until Qt deletes it, and its timers must not act after the
    stand-in hook is gone (a real hook would start)."""
    from PySide6.QtCore import QTimer

    for window in windows:
        if window is None:
            continue
        for timer in window.findChildren(QTimer):
            timer.stop()
        window.hide()
