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

#: Modules every build must have; the check imports these and every other
#: module of the app it finds (a module imported only later, when a menu or
#: an update needs it, fails here rather than in front of the user).
APP_MODULES = ("app.main", "app.platform", "app.updater", "app.ui.window")

#: CS_RUNTIME in the code-signing flags of a running process (cs_blobs.h).
CS_RUNTIME = 0x10000

#: Variables installer/runtime_hooks/scrub_env.py removes from a built macOS
#: app, and the ones that may be set again afterwards (by the self-test
#: itself, or by PyInstaller's own Qt hook, which points Qt at the bundle).
SCRUBBED_PREFIXES = ("OPENSSL_", "SSL_CERT_", "QT_", "QML", "PYTHON", "DYLD_", "BASH_FUNC_", "__BASH_FUNC")
SCRUBBED_NAMES = ("BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS")
SET_BY_SELF_TEST = ("QT_QPA_PLATFORM",)
BUNDLE_PATHS = ("QT_PLUGIN_PATH", "QML2_IMPORT_PATH")
#: The hook pins PATH to the system's own folders, so whatever the app starts
#: by name comes from there.
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


class Skipped(Exception):
    """A check that doesn't apply here; the reason is the message."""


# -- checks -------------------------------------------------------------------------

def check_environment() -> str:
    """The runtime hook ran: nothing is left that would make OpenSSL, Qt or
    dyld load code from outside the app, or make a program the app starts
    run something else."""
    if sys.platform != "darwin":
        raise Skipped("macOS only")
    if not getattr(sys, "frozen", False):
        raise Skipped("not a built app")
    bundle = os.path.dirname(os.path.realpath(getattr(sys, "_MEIPASS", sys.executable)))  # Contents
    left = []
    for name, value in os.environ.items():
        scrubbed = name.startswith(SCRUBBED_PREFIXES) or name in SCRUBBED_NAMES
        if not scrubbed or name in SET_BY_SELF_TEST or name == "OPENSSL_CONF":
            continue
        if name in BUNDLE_PATHS and all(
            os.path.realpath(path).startswith(bundle + os.sep) for path in value.split(os.pathsep) if path
        ):
            continue
        left.append(name)
    if os.environ.get("OPENSSL_CONF") != os.devnull:
        left.append("OPENSSL_CONF (not pinned to an empty file)")
    if os.environ.get("PATH") != SYSTEM_PATH:
        left.append(f"PATH (not pinned to {SYSTEM_PATH})")
    if left:
        raise RuntimeError("still set: " + ", ".join(sorted(left)))
    return f"clean, OPENSSL_CONF={os.devnull}, PATH={SYSTEM_PATH}"


def check_child_processes() -> str:
    """The programs the app starts run from the system, whatever the app was
    started with: pgrep, as --quit and every launch run it, and bash -p, as
    the update swap runs it, finding sleep by name. A program placed earlier
    on PATH, a BASH_ENV file or an exported function would run as the app."""
    if sys.platform != "darwin":
        raise Skipped("macOS only")
    if not getattr(sys, "frozen", False):
        raise Skipped("not a built app")
    import subprocess

    from . import main

    others = main._other_copies_running()  # /usr/bin/pgrep in a built app
    shell = subprocess.run(
        ["/bin/bash", "-p", "-c", "command -v sleep && sleep 0"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    found = shell.stdout.strip()
    if shell.returncode != 0 or found != "/bin/sleep":
        raise RuntimeError(f"bash -p found sleep at {found or 'nothing'!r}: {shell.stderr.strip()}")
    return f"pgrep ran ({'another copy is running' if others else 'no other copy'}), bash -p runs {found}"


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


def check_app_icons() -> str:
    """The app's icons draw with the Qt that shipped: the application icon,
    the menu bar (tray) icon in both states, and the PNG round trip that the
    checkbox tick and the SF Symbol glyphs go through. PNG is built into Qt
    GUI, so none of this needs the image-format plugins the build leaves out."""
    from PySide6.QtCore import QBuffer, QCoreApplication, QIODevice
    from PySide6.QtGui import QGuiApplication, QImage, QImageReader

    running = QCoreApplication.instance()
    if running is not None and not isinstance(running, QGuiApplication):
        raise Skipped("a non-GUI Qt application is running")
    application = QGuiApplication(["DoubleClickFixer"]) if running is None else None
    try:
        from .ui import icons

        drawn = []
        for name, icon, size in (
            ("app", icons.app_icon(), 64),
            ("tray", icons.tray_icon(False), 18),
            ("tray active", icons.tray_icon(True), 18),
        ):
            image = icon.pixmap(size, size).toImage()
            painted = _painted_pixels(image)
            if not painted:
                raise RuntimeError(f"the {name} icon drew nothing")
            drawn.append(f"{name} {image.width()}px ({painted} px painted)")
        png = QBuffer()
        png.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(png, "PNG"):
            raise RuntimeError("couldn't write a PNG")
        again = QImage()
        if not again.loadFromData(png.data(), "PNG") or again.size() != image.size():
            raise RuntimeError("couldn't read back the PNG it wrote")
        formats = sorted(bytes(name).decode() for name in QImageReader.supportedImageFormats())
    finally:
        if application is not None:
            application.shutdown()
            del application
    return f"{', '.join(drawn)}; PNG round trip; image formats: {' '.join(formats)}"


def _painted_pixels(image) -> int:
    return sum(
        1 for y in range(image.height()) for x in range(image.width()) if image.pixelColor(x, y).alpha()
    )


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
    """Every module of the app imports: walked from the package itself, so a
    module imported only later (by a menu, or an update) is checked too."""
    import importlib
    import pkgutil

    import app

    def unreadable(name: str) -> None:
        raise RuntimeError(f"couldn't look inside {name}")

    found = [info.name for info in pkgutil.walk_packages(app.__path__, "app.", onerror=unreadable)]
    missing = sorted(set(APP_MODULES) - set(found))
    if missing:
        raise RuntimeError("not found in the app package: " + ", ".join(missing))
    for name in found:
        importlib.import_module(name)
    return f"{len(found)} modules imported"


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
    ("app icons", check_app_icons),
    ("app modules", check_app_modules),
    ("child processes", check_child_processes),
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
