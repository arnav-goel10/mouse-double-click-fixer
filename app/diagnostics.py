"""A small log for bug reports, and the text "Copy Diagnostics" puts on the
clipboard.

Log calls only put a record on a queue; one background thread writes the
file. So a line logged from the mouse hook's thread never waits on the disk,
where a slow write could make Windows drop the hook or macOS disable the tap.
The log records start-up, the filter starting and stopping, failures and
updates, never clicks.
"""

from __future__ import annotations

import faulthandler
import json
import logging
import logging.handlers
import platform
import queue
import sys
import threading
from collections import deque
from pathlib import Path
from typing import Any, Optional

LOG_NAME = "DoubleClickFixer.log"
CRASH_NAME = "crash.log"
#: Two files of this size are kept, plus the one being written.
MAX_BYTES = 256 * 1024
BACKUPS = 2
#: How much of the log "Copy Diagnostics" includes.
REPORT_LINES = 200
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

log = logging.getLogger("app")


class _Recent(logging.Handler):
    """Keeps the latest lines in memory, for a report when the file can't
    be read."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: deque[str] = deque(maxlen=REPORT_LINES)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(self.format(record))
        except Exception:  # noqa: BLE001 - logging must never raise
            pass


class _State:
    listener: Optional[logging.handlers.QueueListener] = None
    queue_handler: Optional[logging.Handler] = None
    recent: Optional[_Recent] = None
    crash_file = None
    hooks: Optional[tuple] = None
    root_level = logging.WARNING


_state = _State()


def _config_dir() -> Path:
    from .settings import config_dir

    return config_dir()


def log_path() -> Path:
    return _config_dir() / LOG_NAME


def crash_path() -> Path:
    return _config_dir() / CRASH_NAME


def setup(version: str) -> None:
    """Start logging to config_dir()/DoubleClickFixer.log, route uncaught
    exceptions there, and send hard crashes (a segfault in Qt or PyObjC) to
    crash.log. Safe to call once per process; later calls do nothing."""
    if _state.listener is not None:
        return
    formatter = logging.Formatter(FORMAT)
    handlers: list[logging.Handler] = []
    try:
        _config_dir().mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_path(), maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8", delay=True
        )
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)
    except OSError:
        pass  # a read-only profile: keep the in-memory lines at least
    recent = _Recent()
    recent.setFormatter(formatter)
    handlers.append(recent)

    records: queue.SimpleQueue = queue.SimpleQueue()
    listener = logging.handlers.QueueListener(records, *handlers, respect_handler_level=False)
    listener.start()
    queue_handler = logging.handlers.QueueHandler(records)
    root = logging.getLogger()
    _state.root_level = root.level
    root.addHandler(queue_handler)
    root.setLevel(logging.INFO)
    _state.listener, _state.queue_handler, _state.recent = listener, queue_handler, recent

    _install_hooks()
    _enable_crash_log()
    log.info("DoubleClick Fixer %s on %s", version, os_description())


def shutdown() -> None:
    """Write out what is queued and put everything back as it was."""
    if _state.listener is None:
        return
    root = logging.getLogger()
    if _state.queue_handler is not None:
        root.removeHandler(_state.queue_handler)
    root.setLevel(_state.root_level)
    _state.listener.stop()
    for handler in _state.listener.handlers:
        handler.close()
    _state.listener = _state.queue_handler = None
    if _state.hooks is not None:
        sys.excepthook, threading.excepthook, sys.unraisablehook = _state.hooks
        _state.hooks = None
    if _state.crash_file is not None:
        faulthandler.disable()
        _state.crash_file.close()
        _state.crash_file = None


def _install_hooks() -> None:
    previous = (sys.excepthook, threading.excepthook, sys.unraisablehook)
    _state.hooks = previous
    except_hook, thread_hook, unraisable_hook = previous

    def on_exception(kind, value, traceback) -> None:
        log.error("Uncaught exception", exc_info=(kind, value, traceback))
        except_hook(kind, value, traceback)

    def on_thread_exception(args) -> None:
        if args.exc_type is not SystemExit:
            name = args.thread.name if args.thread is not None else "?"
            log.error(
                "Uncaught exception in thread %s", name, exc_info=(args.exc_type, args.exc_value, args.exc_traceback)
            )
        thread_hook(args)

    def on_unraisable(unraisable) -> None:
        try:
            where = repr(unraisable.object)
        except Exception:  # noqa: BLE001 - a broken __repr__ is common here
            where = "?"
        log.error(
            "%s %s",
            unraisable.err_msg or "Exception ignored in",
            where,
            exc_info=(unraisable.exc_type, unraisable.exc_value, unraisable.exc_traceback),
        )
        unraisable_hook(unraisable)

    sys.excepthook = on_exception
    threading.excepthook = on_thread_exception
    sys.unraisablehook = on_unraisable


def _enable_crash_log() -> None:
    try:
        handle = open(crash_path(), "a", encoding="utf-8")
    except OSError:
        return
    faulthandler.enable(file=handle, all_threads=True)
    _state.crash_file = handle


# -- the report ------------------------------------------------------------------

def os_description() -> str:
    system = platform.system()
    if system == "Darwin":
        return f"macOS {platform.mac_ver()[0]} ({platform.machine()})"
    if system == "Windows":
        release, version, _csd, _kind = platform.win32_ver()
        return f"Windows {release} {version} ({platform.machine()})"
    return f"{platform.platform()} ({platform.machine()})"


def _tail(path: Path, count: int) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-count:]
    except OSError:
        return []


def recent_lines(count: int = REPORT_LINES) -> list[str]:
    """The last `count` lines of the log, reaching into the previous file
    when the current one is short (it was just rotated)."""
    lines = _tail(log_path(), count)
    if len(lines) < count:
        lines = _tail(log_path().with_name(f"{LOG_NAME}.1"), count - len(lines)) + lines
    if not lines and _state.recent is not None:
        lines = list(_state.recent.lines)[-count:]
    return lines


def _private(text: str) -> str:
    """The user's home folder (which carries their name) as ~."""
    home = str(Path.home())
    return text.replace(home, "~") if home and home != "/" else text


def report(version: str, state: dict[str, Any], settings: dict[str, Any]) -> str:
    """Everything a bug report needs, as plain text: versions, the OS, the
    app's state and settings, and the end of the log. Settings hold nothing
    private; the window's saved position is left out as noise."""
    from PySide6 import __version__ as pyside_version
    from PySide6.QtCore import qVersion

    shown = {key: value for key, value in settings.items() if key != "window_geometry"}
    lines = [
        f"DoubleClick Fixer {version}",
        f"OS: {os_description()}",
        f"Qt {qVersion()}, PySide6 {pyside_version}, Python {platform.python_version()}",
        f"Running from: {_private(sys.executable)}{' (built app)' if getattr(sys, 'frozen', False) else ''}",
        "",
        "State:",
        *(f"  {key}: {value}" for key, value in state.items()),
        "",
        "Settings:",
        json.dumps(shown, indent=2, sort_keys=True),
        "",
        f"Log ({_private(str(log_path()))}), last {REPORT_LINES} lines:",
        *(_private(line) for line in recent_lines()),
    ]
    crash = _tail(crash_path(), 60)
    if crash:
        lines += ["", "crash.log, last lines:", *(_private(line) for line in crash)]
    return "\n".join(lines) + "\n"
