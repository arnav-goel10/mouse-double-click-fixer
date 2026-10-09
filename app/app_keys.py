"""Which app is which, for the app exclusion list (design section 2.4).

An app's key is what the filter compares with the frontmost app's
(frontmost.current_app_key): on macOS its bundle identifier, or the name of
its executable when it has none; on Windows the lower-cased name of its
executable ("cs2.exe"). The name next to it is for people only.

The app picker offers the apps running now (with a Dock icon on macOS, a
visible window on Windows) and a file dialog for any other one.
"""

from __future__ import annotations

import logging
import os
import platform
import plistlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AppChoice:
    key: str
    name: str


def windows_key(executable: str) -> str:
    """The key of a Windows program: its file name, lower-cased."""
    return Path(executable.replace("\\", "/")).name.lower()


def from_exe(path: str) -> Optional[AppChoice]:
    """A Windows program chosen in the file dialog."""
    key = windows_key(path)
    if not key:
        return None
    return AppChoice(key, Path(path.replace("\\", "/")).stem or key)


def from_bundle(path: str) -> Optional[AppChoice]:
    """A macOS app chosen in the file dialog: its bundle identifier, or the
    name of its executable when it has none (or isn't a bundle at all)."""
    bundle = Path(path)
    info: dict = {}
    try:
        with open(bundle / "Contents" / "Info.plist", "rb") as handle:
            loaded = plistlib.load(handle)
        if isinstance(loaded, dict):
            info = loaded
    except (OSError, ValueError, plistlib.InvalidFileException):
        pass
    name = _text(info.get("CFBundleDisplayName")) or _text(info.get("CFBundleName"))
    if not name:
        name = bundle.stem if bundle.suffix == ".app" else bundle.name
    identifier = _text(info.get("CFBundleIdentifier"))
    if identifier:
        return AppChoice(identifier, name)
    executable = _text(info.get("CFBundleExecutable")) or (bundle.stem if bundle.suffix == ".app" else bundle.name)
    return AppChoice(executable, name) if executable else None


def from_path(path: str) -> Optional[AppChoice]:
    """Whatever the file dialog returned, on this platform."""
    if not path:
        return None
    if platform.system() == "Windows":
        return from_exe(path)
    return from_bundle(path)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


# -- running apps ------------------------------------------------------------------

def running_apps() -> list[AppChoice]:
    """The apps open now, by name, without this one. Empty when the system
    won't say."""
    try:
        if platform.system() == "Darwin":
            apps = _running_mac()
        elif platform.system() == "Windows":
            apps = _running_windows()
        else:
            apps = []
    except Exception:  # noqa: BLE001 - the file dialog still works
        log.warning("Couldn't list the running apps", exc_info=True)
        apps = []
    unique: dict[str, AppChoice] = {}
    for app in apps:
        unique.setdefault(app.key, app)
    return sorted(unique.values(), key=lambda app: app.name.casefold())


def _running_mac() -> list[AppChoice]:
    import AppKit

    own = os.getpid()
    apps = []
    for app in AppKit.NSWorkspace.sharedWorkspace().runningApplications():
        # Regular apps are the ones in the Dock; agents and daemons can't be
        # in front for long enough to matter.
        if app.activationPolicy() != AppKit.NSApplicationActivationPolicyRegular:
            continue
        if app.processIdentifier() == own:
            continue
        identifier = app.bundleIdentifier()
        executable = app.executableURL()
        key = str(identifier) if identifier else (str(executable.lastPathComponent()) if executable else "")
        name = str(app.localizedName() or key)
        if key:
            apps.append(AppChoice(key, name))
    return apps


def _running_windows() -> list[AppChoice]:
    """Programs with a visible top-level window, from EnumWindows and the
    toolhelp process snapshot (no psutil)."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    pids: set[int] = set()
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    gw_owner = 4

    def visit(hwnd, _param):
        if user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, gw_owner) and user32.GetWindowTextLengthW(hwnd):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            pids.add(pid.value)
        return True

    user32.EnumWindows(callback_type(visit), 0)

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    th32cs_snapprocess = 0x2
    snapshot = kernel32.CreateToolhelp32Snapshot(th32cs_snapprocess, 0)
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        return []
    own = os.getpid()
    apps = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            if entry.th32ProcessID in pids and entry.th32ProcessID != own:
                choice = from_exe(entry.szExeFile)
                if choice is not None and choice.key != "explorer.exe":
                    apps.append(choice)
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return apps
