"""Which app is in front, for the app exclusion list (design sections 2.3
and 2.4).

"In front" is the active app: the one that has the keyboard focus and owns
the menu bar (macOS) or the foreground window (Windows). It is not the window
under the pointer. A click on another app's window brings that app to the
front first, so the click that does so is judged by the app that was in
front before it.

An app's key is the one app_keys gives the exclusion list: on macOS its
bundle identifier, or its executable's name when it has none; on Windows its
executable's file name, lower-cased ("cs2.exe"). A Store (UWP) app's window
belongs to ApplicationFrameHost.exe, so the app inside it is looked up; when
that can't be found the key is None, which matches no exclusion.

current_app_key() asks now. watch(callback) calls back with the new key
whenever the app in front changes, until its stop(): on macOS from
NSWorkspace's activation notice, delivered on the main thread (Qt's run
loop); on Windows from SetWinEventHook(EVENT_SYSTEM_FOREGROUND) on the
thread that called watch(), which must run a message loop (the filter's hook
thread does).
"""

from __future__ import annotations

import logging
import platform
from pathlib import PureWindowsPath
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

#: Store apps' windows all belong to this program (see the module notes).
FRAME_HOST = "applicationframehost.exe"


class Watcher:
    """A running watch(); stop() ends it. Stopping twice is harmless."""

    def __init__(self, stop: Callable[[], None]) -> None:
        self._stop = stop

    def stop(self) -> None:
        stop, self._stop = self._stop, None
        if stop is not None:
            try:
                stop()
            except Exception:  # noqa: BLE001 - nothing more to do
                log.warning("Couldn't stop watching the app in front", exc_info=True)


def current_app_key() -> Optional[str]:
    try:
        system = platform.system()
        if system == "Darwin":
            import AppKit

            return mac_app_key(AppKit.NSWorkspace.sharedWorkspace().frontmostApplication())
        if system == "Windows":
            return _WindowsForeground().key_for(None)
    except Exception:  # noqa: BLE001 - unknown: nothing is excluded
        log.warning("Couldn't read the app in front", exc_info=True)
    return None


def watch(callback: Callable[[Optional[str]], object]) -> Watcher:
    system = platform.system()
    if system == "Darwin":
        return _watch_mac(callback)
    if system == "Windows":
        return _WindowsForeground().watch(callback)
    return Watcher(lambda: None)


# -- macOS ---------------------------------------------------------------------------

def mac_app_key(app: Any) -> Optional[str]:
    """An NSRunningApplication's key: its bundle identifier, or its
    executable's name."""
    if app is None:
        return None
    identifier = app.bundleIdentifier()
    if identifier:
        return str(identifier)
    executable = app.executableURL()
    name = executable.lastPathComponent() if executable is not None else None
    return str(name) if name else None


def _watch_mac(callback: Callable[[Optional[str]], object]) -> Watcher:
    import AppKit
    import Foundation

    center = AppKit.NSWorkspace.sharedWorkspace().notificationCenter()

    def activated(notification: Any) -> None:
        try:
            app = notification.userInfo()[AppKit.NSWorkspaceApplicationKey]
            callback(mac_app_key(app))
        except Exception:  # noqa: BLE001 - never raise into AppKit
            log.warning("Couldn't follow the app in front", exc_info=True)

    token = center.addObserverForName_object_queue_usingBlock_(
        AppKit.NSWorkspaceDidActivateApplicationNotification,
        None,
        Foundation.NSOperationQueue.mainQueue(),
        activated,
    )
    return Watcher(lambda: center.removeObserver_(token))


# -- Windows -------------------------------------------------------------------------

def windows_key(image_path: Optional[str]) -> Optional[str]:
    """A Windows program's key from its image path."""
    if not image_path:
        return None
    name = PureWindowsPath(str(image_path)).name.lower()
    return name or None


def foreground_key(
    pid: int, image_key: Callable[[int], Optional[str]], hosted_pids: Callable[[], list]
) -> Optional[str]:
    """The key of the app whose window, owned by process `pid`, is in front.
    A Store app's frame (ApplicationFrameHost.exe) stands for the app whose
    window it hosts, which `hosted_pids` finds; None if there is none."""
    key = image_key(pid)
    if key != FRAME_HOST:
        return key
    for hosted in hosted_pids():
        hosted_key = image_key(hosted)
        if hosted_key and hosted_key != FRAME_HOST:
            return hosted_key
    return None


class _WindowsForeground:
    EVENT_SYSTEM_FOREGROUND = 0x0003
    WINEVENT_OUTOFCONTEXT = 0x0000
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    def __init__(self, user32=None, kernel32=None) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._wintypes = wintypes
        self.user32 = user32 = user32 or ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = kernel32 = kernel32 or ctypes.WinDLL("kernel32", use_last_error=True)
        self.WINEVENTPROC = ctypes.WINFUNCTYPE(
            None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND, wintypes.LONG, wintypes.LONG,
            wintypes.DWORD, wintypes.DWORD,
        )
        self.ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.EnumChildWindows.argtypes = [wintypes.HWND, self.ENUMPROC, wintypes.LPARAM]
        user32.SetWinEventHook.argtypes = [
            wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, self.WINEVENTPROC, wintypes.DWORD,
            wintypes.DWORD, wintypes.DWORD,
        ]
        user32.SetWinEventHook.restype = wintypes.HANDLE
        user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]
        user32.UnhookWinEvent.restype = wintypes.BOOL
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
        ]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    def pid_of(self, hwnd) -> int:
        pid = self._wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, self._ctypes.byref(pid))
        return int(pid.value)

    def image_key(self, pid: int) -> Optional[str]:
        if not pid:
            return None
        process = self.kernel32.OpenProcess(self.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not process:
            return None
        try:
            size = self._wintypes.DWORD(1024)
            buffer = self._ctypes.create_unicode_buffer(size.value)
            if not self.kernel32.QueryFullProcessImageNameW(process, 0, buffer, self._ctypes.byref(size)):
                return None
            return windows_key(buffer.value)
        finally:
            self.kernel32.CloseHandle(process)

    def hosted_pids(self, hwnd, host_pid: int) -> list[int]:
        """The processes owning child windows of a frame host window, other
        than the frame host itself: the Store app inside it."""
        found: list[int] = []

        def visit(child, _param):
            pid = self.pid_of(child)
            if pid and pid != host_pid and pid not in found:
                found.append(pid)
            return True

        self.user32.EnumChildWindows(hwnd, self.ENUMPROC(visit), 0)
        return found

    def key_for(self, hwnd) -> Optional[str]:
        """The key of the app owning `hwnd`, or the foreground window's when
        None."""
        if hwnd is None:
            hwnd = self.user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = self.pid_of(hwnd)
        return foreground_key(pid, self.image_key, lambda: self.hosted_pids(hwnd, pid))

    def watch(self, callback: Callable[[Optional[str]], object]) -> Watcher:
        def changed(_hook, _event, hwnd, _object, _child, _thread, _time):
            try:
                callback(self.key_for(hwnd))
            except Exception:  # noqa: BLE001 - never raise into Windows
                log.warning("Couldn't follow the app in front", exc_info=True)

        procedure = self.WINEVENTPROC(changed)
        hook = self.user32.SetWinEventHook(
            self.EVENT_SYSTEM_FOREGROUND, self.EVENT_SYSTEM_FOREGROUND, None, procedure, 0, 0,
            self.WINEVENT_OUTOFCONTEXT,
        )
        if not hook:
            log.warning("Couldn't watch the app in front (error %d)", self._ctypes.get_last_error())
            return Watcher(lambda: None)

        def stop() -> None:
            self.user32.UnhookWinEvent(hook)
            procedure  # noqa: B018 - kept alive until the hook is gone

        return Watcher(stop)
