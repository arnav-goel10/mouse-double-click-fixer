"""``DoubleClickFixer --self-test``: check that a built app can run at all.

The release workflow runs this on the exact app it is about to publish. It
catches a build that would crash at launch (a module, Qt plugin or library
left out) or that its own code signing breaks (the hardened runtime refusing
a C callback that Python makes on the fly). An update to such a build could
never update itself again, so it must not ship.

It touches nothing a running copy owns: no single-instance channel, no
window, no filter, no posted events. run.py hands over to it before the app
itself is even imported. One line per check; nonzero exit if any failed.
"""

from __future__ import annotations

import os
import platform
import sys
import time
from typing import Callable, List, Tuple

#: The Qt platform the check loads unless the caller names another one (a
#: CI job with a window server can ask for "cocoa", say). Offscreen needs no
#: window server and opens no window.
DEFAULT_QT_PLATFORM = "offscreen"

#: A run loop mode of its own, so the timer check runs nothing but its timer.
RUN_LOOP_MODE = "com.doubleclickfixer.selftest"

#: The PyObjC timer check gives up after this long.
TIMER_WAIT_S = 0.5

#: The modules the app needs at launch, beyond what the checks import.
APP_MODULES = ("app.main", "app.platform", "app.updater", "app.ui.window")

#: CS_RUNTIME in the code-signing flags of a running process (cs_blobs.h).
CS_RUNTIME = 0x10000

#: Variables installer/runtime_hooks/scrub_env.py removes from a built macOS
#: app, and the ones that may be set again afterwards (by the self-test
#: itself, or by PyInstaller's own Qt hook, which points Qt at the bundle).
SCRUBBED_PREFIXES = ("OPENSSL_", "SSL_CERT_", "QT_", "QML", "PYTHON", "DYLD_")
SET_BY_SELF_TEST = ("QT_QPA_PLATFORM",)
BUNDLE_PATHS = ("QT_PLUGIN_PATH", "QML2_IMPORT_PATH")


class Skipped(Exception):
    """A check that doesn't apply here; the reason is the message."""


# -- checks -------------------------------------------------------------------------

def check_environment() -> str:
    """The runtime hook ran: nothing is left that would make OpenSSL, Qt or
    dyld load code from outside the app."""
    if sys.platform != "darwin":
        raise Skipped("macOS only")
    if not getattr(sys, "frozen", False):
        raise Skipped("not a built app")
    bundle = os.path.dirname(os.path.realpath(getattr(sys, "_MEIPASS", sys.executable)))  # Contents
    left = []
    for name, value in os.environ.items():
        if not name.startswith(SCRUBBED_PREFIXES) or name in SET_BY_SELF_TEST or name == "OPENSSL_CONF":
            continue
        if name in BUNDLE_PATHS and all(
            os.path.realpath(path).startswith(bundle + os.sep) for path in value.split(os.pathsep) if path
        ):
            continue
        left.append(name)
    if os.environ.get("OPENSSL_CONF") != os.devnull:
        left.append("OPENSSL_CONF (not pinned to an empty file)")
    if left:
        raise RuntimeError("still set: " + ", ".join(sorted(left)))
    return f"clean, OPENSSL_CONF={os.devnull}"


def check_code_signature() -> str:
    """A built macOS app runs with the hardened runtime, which is what makes
    dyld ignore DYLD_INSERT_LIBRARIES and friends."""
    if sys.platform != "darwin":
        raise Skipped("macOS only")
    if not getattr(sys, "frozen", False):
        raise Skipped("not a built app")
    flags = code_signing_flags()
    if not flags & CS_RUNTIME:
        raise RuntimeError(f"not running with the hardened runtime (flags {flags:#x})")
    return f"hardened runtime, flags {flags:#x}"


def code_signing_flags() -> int:
    """The kernel's code-signing flags for this process (macOS)."""
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    libc.csops.argtypes = [ctypes.c_int, ctypes.c_uint, ctypes.c_void_p, ctypes.c_size_t]
    flags = ctypes.c_uint32()
    if libc.csops(os.getpid(), 0, ctypes.byref(flags), ctypes.sizeof(flags)) != 0:  # CS_OPS_STATUS
        raise RuntimeError(f"csops failed (errno {ctypes.get_errno()})")
    return flags.value


def check_update_signatures() -> str:
    import hashlib

    from . import update_signature

    update_signature.self_test()
    # A broken OpenSSL setup makes hashlib fall back to its own code quietly.
    backend = "OpenSSL" if hashlib.sha256.__name__.startswith("openssl") else "built-in"
    return f"Ed25519 and minisign test vectors verify, {backend} hashlib"


def check_ctypes_callback() -> str:
    """ctypes makes a C function out of a Python one (the Windows hook and
    the macOS permission probe both need this)."""
    import ctypes

    libc = ctypes.cdll.msvcrt if sys.platform == "win32" else ctypes.CDLL(None)
    compare_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int))
    calls = [0]

    def compare(left, right) -> int:
        calls[0] += 1
        return left[0] - right[0]

    callback = compare_type(compare)
    values = (ctypes.c_int * 5)(5, 1, 4, 2, 3)
    qsort = libc.qsort
    qsort.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t, compare_type]
    qsort.restype = None
    qsort(values, len(values), ctypes.sizeof(ctypes.c_int), callback)
    if list(values) != [1, 2, 3, 4, 5] or not calls[0]:
        raise RuntimeError(f"qsort gave {list(values)} after {calls[0]} callbacks")
    return f"qsort called back {calls[0]} times"


def check_pyobjc_callback() -> str:
    """PyObjC calls a Python function from a run loop, as it does for the
    filter's event tap."""
    if sys.platform != "darwin":
        raise Skipped("macOS only")
    import Quartz

    fired: List[float] = []

    def on_timer(_timer, _info) -> None:
        fired.append(time.perf_counter())
        Quartz.CFRunLoopStop(Quartz.CFRunLoopGetCurrent())

    started = time.perf_counter()
    timer = Quartz.CFRunLoopTimerCreate(None, Quartz.CFAbsoluteTimeGetCurrent() + 0.01, 0, 0, 0, on_timer, None)
    Quartz.CFRunLoopAddTimer(Quartz.CFRunLoopGetCurrent(), timer, RUN_LOOP_MODE)
    try:
        Quartz.CFRunLoopRunInMode(RUN_LOOP_MODE, TIMER_WAIT_S, False)
    finally:
        Quartz.CFRunLoopTimerInvalidate(timer)
    if not fired:
        raise RuntimeError(f"a run loop timer's Python callback didn't run within {TIMER_WAIT_S} s")
    return f"run loop timer called back after {(fired[0] - started) * 1000:.0f} ms"


def check_event_tap() -> str:
    """Quartz hands out an event tap with a Python callback, and closes it.

    Listen-only, for tablet proximity, never enabled or added to a run loop:
    it sees nothing and changes nothing. Without the permission macOS
    refuses it, which says nothing about the build, so that is a skip.
    """
    if sys.platform != "darwin":
        raise Skipped("macOS only")
    import Quartz

    def passthrough(_proxy, _event_type, event, _refcon):
        return event

    tap = Quartz.CGEventTapCreate(
        Quartz.kCGSessionEventTap,
        Quartz.kCGTailAppendEventTap,
        Quartz.kCGEventTapOptionListenOnly,
        Quartz.CGEventMaskBit(Quartz.kCGEventTabletProximity),
        passthrough,
        None,
    )
    if tap is None:
        raise Skipped("no permission")
    Quartz.CFMachPortInvalidate(tap)
    if Quartz.CFMachPortIsValid(tap):
        raise RuntimeError("the tap was still valid after CFMachPortInvalidate")
    return "listen-only tap created and invalidated"


def check_qt_platform() -> str:
    """The bundled Qt platform plugin loads: a QGuiApplication starts and
    goes away again, with no window."""
    from PySide6.QtCore import QCoreApplication, qVersion
    from PySide6.QtGui import QGuiApplication

    wanted = (os.environ.get("QT_QPA_PLATFORM") or DEFAULT_QT_PLATFORM).split(":")[0]
    running = QCoreApplication.instance()
    if running is not None:  # in-process, from the unit tests
        name = running.platformName() if isinstance(running, QGuiApplication) else "no GUI"
        return f"{name}, Qt {qVersion()}, in the application already running"
    # A missing plugin makes Qt abort the process; say which one instead.
    if _platform_plugin(wanted) is None:
        searched = os.pathsep.join(QCoreApplication.libraryPaths()) or "no plugin folders"
        raise RuntimeError(f"no {wanted!r} platform plugin in {searched}")
    application = QGuiApplication(["DoubleClickFixer"])
    try:
        name = application.platformName()
    finally:
        application.shutdown()
        del application
    if name != wanted:
        raise RuntimeError(f"Qt started the {name!r} platform, not {wanted!r}")
    return f"{name} plugin loaded and unloaded, Qt {qVersion()}"


def _platform_plugin(name: str):
    from PySide6.QtCore import QCoreApplication

    file = f"q{name}.dll" if sys.platform == "win32" else f"libq{name}{'.dylib' if sys.platform == 'darwin' else '.so'}"
    for folder in QCoreApplication.libraryPaths():
        path = os.path.join(folder, "platforms", file)
        if os.path.exists(path):
            return path
    return None


def check_notices() -> str:
    """The third-party notices (Qt's LGPL among them) shipped, where the
    Acknowledgements button looks for them."""
    from . import notices

    path = notices.notices_path()
    if path is None:
        raise RuntimeError(f"no {notices.NOTICES_FILE} in " + ", ".join(str(p) for p in notices.candidates()))
    return f"{path}, {path.stat().st_size:,} bytes"


def check_app_modules() -> str:
    import importlib

    for name in APP_MODULES:
        importlib.import_module(name)
    return ", ".join(APP_MODULES)


#: Name and check, in the order they run. The environment goes first, before
#: any check imports a library that reads it.
CHECKS: List[Tuple[str, Callable[[], str]]] = [
    ("environment", check_environment),
    ("code signature", check_code_signature),
    ("update signatures", check_update_signatures),
    ("ctypes callback", check_ctypes_callback),
    ("PyObjC callback", check_pyobjc_callback),
    ("event tap", check_event_tap),
    ("Qt platform plugin", check_qt_platform),
    ("app modules", check_app_modules),
    ("third-party notices", check_notices),
]


def use_default_qt_platform() -> None:
    """Load the offscreen Qt platform unless the caller chose one. Must run
    before anything imports PySide6."""
    if not os.environ.get("QT_QPA_PLATFORM"):
        os.environ["QT_QPA_PLATFORM"] = DEFAULT_QT_PLATFORM


def main() -> int:
    use_default_qt_platform()
    from . import __version__

    where = "built app" if getattr(sys, "frozen", False) else "from source"
    print(
        f"DoubleClick Fixer {__version__} self-test ({where}, {platform.system()} {platform.release()} "
        f"{platform.machine()}, Python {platform.python_version()})",
        flush=True,
    )
    failed = skipped = 0
    for name, check in CHECKS:
        try:
            detail = check()
        except Skipped as reason:
            skipped += 1
            line = f"skipped: {reason}"
        except Exception as error:  # noqa: BLE001 - report every failure, then carry on
            failed += 1
            line = f"FAILED: {type(error).__name__}: {error}"
        else:
            line = f"ok ({detail})"
        print(f"{name}: {line}", flush=True)
    passed = len(CHECKS) - failed - skipped
    if failed:
        print(f"self-test FAILED: {failed} failed, {passed} passed, {skipped} skipped", flush=True)
        return 1
    print(f"self-test passed: {passed} passed, {skipped} skipped", flush=True)
    return 0
