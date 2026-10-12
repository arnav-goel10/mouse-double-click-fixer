"""Other mouse utilities running beside this app (1.1 trust features).

Several popular tools also take over mouse buttons or the wheel. Running
beside this app they can swallow side-button clicks before the filter sees
them, add their own debounce on top of this one, or smooth and re-send wheel
ticks. Nothing here changes them: scan() only says which are running, so the
app can say plainly what they may take over.

How each is recognised was checked against a primary source:

* macOS bundle identifiers from the Homebrew casks' uninstall and zap
  stanzas (formulae.brew.sh/api/cask/<name>.json, read 10 and 12 Oct 2026): Logi
  Options+ com.logi.optionsplus(.driverhost, .updater) and com.logi.cp-dev-mgr;
  G HUB com.logi.ghub(.agent); Logitech Options com.logitech.manager.daemon and
  com.logitech.Logi-Options; Mac Mouse Fix com.nuebling.mac-mouse-fix(.helper),
  also seen running on the owner's Mac as "Mac Mouse Fix Helper"; SteerMouse
  jp.plentycom.app.SteerMouse and its agent jp.plentycom.boa.SteerMouse
  ("SteerMouse Manager" in its release notes); LinearMouse
  com.lujjjh.LinearMouse; BetterMouse com.naotanhaocan.BetterMouse. Razer
  Synapse for macOS (Synapse 2, no longer updated) by its launch agents
  com.razerzone.rzdeviceengine and com.razer.rzupdater, as uninstall guides
  and Razer's forum give them (Homebrew no longer has a cask for it).
* Windows executables: logioptionsplus_agent.exe (Logi Options+ agent,
  signed by Logitech, in C:\\Program Files\\LogiOptionsPlus), lghub.exe and
  lghub_agent.exe (C:\\Program Files\\LGHUB), LogiOptionsMgr.exe (Logitech
  Options, with LogiOptions.exe), Razer Synapse 3's "Razer Synapse
  Service.exe" and "Razer Synapse 3.exe" and Synapse 4's RazerAppEngine.exe
  (Razer's own support pages and forum), XMouseButtonControl.exe, and
  DoubleClickFix.exe (named in its own uninstall.bat and Store package).

What each "takes" is what it can be set to do, not what it does on a given
machine: a note says it MAY take these over.

On macOS the running apps come from NSWorkspace (bundle identifiers, which
covers menu bar and login-item helpers) and the process names from libproc
(daemons with no bundle). On Windows from a Toolhelp process snapshot.
Neither needs a permission, and neither reads anything but names.
"""

from __future__ import annotations

import logging
import platform
from dataclasses import dataclass, field
from typing import Iterable, Optional

log = logging.getLogger(__name__)

#: The words for what a utility may take over, in the order they are named.
SIDE_BUTTONS = "side buttons"
MIDDLE_BUTTON = "middle button"
WHEEL = "wheel"
#: Its own click filtering or debounce, on top of this app's.
CLICKS = "clicks"
TAKES_ORDER = (CLICKS, SIDE_BUTTONS, MIDDLE_BUTTON, WHEEL)


@dataclass(frozen=True)
class Utility:
    key: str
    name: str
    takes: tuple
    #: macOS bundle identifiers, compared without case.
    mac_bundles: tuple = ()
    #: macOS process names (libproc's, at most 32 characters).
    mac_processes: tuple = ()
    #: Windows executable file names, lower case.
    windows_processes: tuple = ()


@dataclass(frozen=True)
class Conflict:
    """A running utility and what it may take over."""

    name: str
    takes: list = field(default_factory=list)
    key: str = ""

    def takes_text(self) -> str:
        """"the side buttons and the wheel"."""
        words = [f"the {word}" for word in self.takes]
        if len(words) <= 1:
            return "".join(words)
        return ", ".join(words[:-1]) + " and " + words[-1]


KNOWN: tuple = (
    Utility(
        "logi-options-plus", "Logi Options+", (SIDE_BUTTONS, MIDDLE_BUTTON, WHEEL),
        mac_bundles=("com.logi.optionsplus", "com.logi.optionsplus.driverhost", "com.logi.cp-dev-mgr"),
        mac_processes=("logioptionsplus_agent", "logioptionsplus"),
        windows_processes=("logioptionsplus_agent.exe", "logioptionsplus.exe"),
    ),
    Utility(
        "logitech-options", "Logitech Options", (SIDE_BUTTONS, MIDDLE_BUTTON, WHEEL),
        mac_bundles=("com.logitech.manager.daemon", "com.logitech.Logi-Options"),
        mac_processes=("LogiMgrDaemon",),
        windows_processes=("logioptions.exe", "logioptionsmgr.exe"),
    ),
    Utility(
        "g-hub", "Logitech G HUB", (SIDE_BUTTONS, MIDDLE_BUTTON, WHEEL),
        mac_bundles=("com.logi.ghub", "com.logi.ghub.agent"),
        mac_processes=("lghub_agent", "lghub"),
        windows_processes=("lghub_agent.exe", "lghub.exe"),
    ),
    Utility(
        "razer-synapse", "Razer Synapse", (SIDE_BUTTONS, MIDDLE_BUTTON, WHEEL),
        mac_bundles=("com.razerzone.rzdeviceengine",),
        mac_processes=("RzDeviceEngine",),
        windows_processes=("razer synapse service.exe", "razer synapse 3.exe", "razerappengine.exe"),
    ),
    Utility(
        "mac-mouse-fix", "Mac Mouse Fix", (SIDE_BUTTONS, MIDDLE_BUTTON, WHEEL),
        mac_bundles=("com.nuebling.mac-mouse-fix.helper", "com.nuebling.mac-mouse-fix"),
        mac_processes=("Mac Mouse Fix Helper",),
    ),
    Utility(
        "steermouse", "SteerMouse", (SIDE_BUTTONS, MIDDLE_BUTTON, WHEEL),
        mac_bundles=("jp.plentycom.boa.SteerMouse", "jp.plentycom.app.SteerMouse"),
        mac_processes=("SteerMouse Manager",),
    ),
    Utility(
        # It has a "Debounce button clicks" option of its own.
        "linearmouse", "LinearMouse", (CLICKS, SIDE_BUTTONS, WHEEL),
        mac_bundles=("com.lujjjh.LinearMouse",),
    ),
    Utility(
        "bettermouse", "BetterMouse", (SIDE_BUTTONS, MIDDLE_BUTTON, WHEEL),
        mac_bundles=("com.naotanhaocan.BetterMouse",),
    ),
    Utility(
        "x-mouse-button-control", "X-Mouse Button Control", (SIDE_BUTTONS, MIDDLE_BUTTON, WHEEL),
        windows_processes=("xmousebuttoncontrol.exe",),
    ),
    Utility(
        # Another bounce filter, with a wheel fix of its own: two filters
        # judge every click.
        "doubleclickfix", "DoubleClickFix", (CLICKS, WHEEL),
        windows_processes=("doubleclickfix.exe",),
    ),
)


def match(
    system: str,
    bundles: Iterable[str] = (),
    processes: Iterable[str] = (),
    known: Iterable[Utility] = KNOWN,
) -> list[Conflict]:
    """The known utilities among what is running: bundle identifiers and
    process names (macOS), or executable names (Windows). Sorted by name."""
    bundle_set = {str(bundle).lower() for bundle in bundles if bundle}
    process_set = {str(name).lower() for name in processes if name}
    found = []
    for utility in known:
        if system == "Darwin":
            hit = any(bundle.lower() in bundle_set for bundle in utility.mac_bundles) or any(
                name.lower() in process_set for name in utility.mac_processes
            )
        elif system == "Windows":
            hit = any(name in process_set for name in utility.windows_processes)
        else:
            hit = False
        if hit:
            takes = [word for word in TAKES_ORDER if word in utility.takes]
            found.append(Conflict(utility.name, takes, utility.key))
    return sorted(found, key=lambda conflict: conflict.name.lower())


def scan() -> list[Conflict]:
    """The known utilities running now. Never raises: a failed look finds
    nothing."""
    system = platform.system()
    try:
        if system == "Darwin":
            return match(system, _mac_bundles(), _mac_process_names())
        if system == "Windows":
            return match(system, processes=_windows_process_names())
    except Exception:  # noqa: BLE001 - a note for the user; never break the app
        log.warning("Couldn't look for other mouse utilities", exc_info=True)
    return []


# -- macOS ---------------------------------------------------------------------

def _mac_bundles() -> list[str]:
    try:
        import AppKit

        return [
            str(app.bundleIdentifier())
            for app in AppKit.NSWorkspace.sharedWorkspace().runningApplications()
            if app.bundleIdentifier()
        ]
    except Exception:  # noqa: BLE001 - the process names still count
        log.warning("Couldn't list the running apps", exc_info=True)
        return []


def _mac_process_names(libc: Optional[object] = None) -> list[str]:
    """Every process's name, from libproc (proc_listallpids, proc_name).
    Names only: nothing else about a process is read."""
    import ctypes

    lib = libc or ctypes.CDLL("/usr/lib/libproc.dylib")
    lib.proc_listallpids.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.proc_listallpids.restype = ctypes.c_int
    lib.proc_name.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
    lib.proc_name.restype = ctypes.c_int
    count = lib.proc_listallpids(None, 0)
    if count <= 0:
        return []
    capacity = count + 64  # processes start between the two calls
    pids = (ctypes.c_int * capacity)()
    count = lib.proc_listallpids(pids, ctypes.sizeof(pids))
    names = []
    buffer = ctypes.create_string_buffer(256)
    for pid in pids[: max(0, min(count, capacity))]:
        if pid <= 0:
            continue
        length = lib.proc_name(pid, buffer, ctypes.sizeof(buffer))
        if length > 0:
            names.append(buffer.value.decode("utf-8", "replace"))
    return names


# -- Windows -------------------------------------------------------------------

def _windows_process_names(kernel32: Optional[object] = None) -> list[str]:
    """Every process's executable name (lower case), from a Toolhelp
    snapshot."""
    import ctypes
    from ctypes import wintypes

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260),
        ]

    kernel32 = kernel32 or ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        return []
    names = []
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            names.append(entry.szExeFile.lower())
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return names
