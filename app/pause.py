"""Pausing the filter for a while, and the global shortcut that does it.

The shortcut never reads the keyboard. Each system is told the one key
combination and tells the app only when that combination is pressed:

* macOS: RegisterEventHotKey (Carbon's hot keys, in HIToolbox), with a
  handler for kEventHotKeyPressed on the application's event target. The
  system matches the keys; no event tap, no Input Monitoring permission, and
  no other key ever reaches the app. The key is a key code (the P key's
  place on a US keyboard, kVK_ANSI_P), so on a layout that puts another
  letter there (Dvorak's L) that key is the one that works.
* Windows: RegisterHotKey with no window, on a thread of its own that waits
  for WM_HOTKEY. Its own thread because the filter's hook thread stops while
  the filter is paused, and the shortcut must still resume it. No keyboard
  hook (WH_KEYBOARD_LL) is ever installed. The key is the virtual key 'P',
  which follows the keyboard layout.

If the combination is taken (Windows refuses one another program has
registered; macOS one another app holds exclusively), the shortcut is simply
unavailable and diagnostics say why.

The README's promise "never keystrokes" rests on this module: see
tests/test_pause.py, which checks that nothing in the app installs a
keyboard hook or tap.
"""

from __future__ import annotations

import ctypes
import logging
import platform
import threading
from datetime import datetime
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

#: "Pause for 15 Minutes" in the menus, and what the shortcut pauses for.
DEFAULT_MINUTES = 15
#: The longest pause anything may ask for: a day.
MAX_MINUTES = 24 * 60

MAC_TEXT = "⌃⌥⌘P"
WINDOWS_TEXT = "Ctrl+Alt+Shift+P"

# macOS (HIToolbox/Events.h, CarbonEvents.h).
KVK_ANSI_P = 0x23
CMD_KEY = 1 << 8
SHIFT_KEY = 1 << 9
OPTION_KEY = 1 << 11
CONTROL_KEY = 1 << 12
MAC_MODIFIERS = CONTROL_KEY | OPTION_KEY | CMD_KEY

# Windows (winuser.h).
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
VK_P = 0x50
WINDOWS_MODIFIERS = MOD_CONTROL | MOD_ALT | MOD_SHIFT | MOD_NOREPEAT
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
ERROR_HOTKEY_ALREADY_REGISTERED = 1409

#: The id both systems hand back with the press.
HOTKEY_ID = 0xDC1


def clamp_minutes(minutes: Any) -> int:
    try:
        value = int(minutes)
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_MINUTES
    return max(1, min(MAX_MINUTES, value))


def shortcut_text(system: Optional[str] = None) -> str:
    """How the shortcut is written on this system ("" where there is none)."""
    system = system or platform.system()
    if system == "Darwin":
        return MAC_TEXT
    if system == "Windows":
        return WINDOWS_TEXT
    return ""


def until_text(until: float, now: float) -> str:
    """"3:45 PM", or "tomorrow at 3:45 PM", in the system's own clock
    format (12 or 24 hour)."""
    moment = datetime.fromtimestamp(until)
    clock = _clock(moment)
    days = (moment.date() - datetime.fromtimestamp(now).date()).days
    if days == 0:
        return clock
    if days == 1:
        return f"tomorrow at {clock}"
    return f"{moment.strftime('%A')} at {clock}"


def _clock(moment: datetime) -> str:
    try:
        from PySide6.QtCore import QLocale, QTime

        text = QLocale.system().toString(QTime(moment.hour, moment.minute), QLocale.FormatType.ShortFormat)
        if text:
            return text
    except Exception:  # noqa: BLE001 - Qt missing or unhappy: plain format below
        pass
    hour = moment.hour % 12 or 12
    return f"{hour}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"


def four_cc(text: str) -> int:
    return int.from_bytes(text.encode("ascii"), "big")


class Shortcut:
    """A global shortcut that calls `on_press` (from any thread). start()
    returns whether it is registered; stop() undoes it. Both may be called
    again. `error` says why it isn't, when it isn't."""

    text = ""

    def __init__(self, on_press: Callable[[], object]) -> None:
        self.on_press = on_press
        self.registered = False
        self.error = ""

    def start(self) -> bool:
        self.error = "No global shortcut on this system."
        return False

    def stop(self) -> None:
        self.registered = False

    def _pressed(self) -> None:
        try:
            self.on_press()
        except Exception:  # noqa: BLE001 - never raise into the system's callback
            log.warning("The pause shortcut's action failed", exc_info=True)


class MacShortcut(Shortcut):
    """Carbon's RegisterEventHotKey. The handler runs on the main thread,
    from the app's own event loop."""

    text = MAC_TEXT

    def __init__(self, on_press: Callable[[], object], carbon: Optional[Any] = None) -> None:
        super().__init__(on_press)
        self._carbon = carbon
        self._handler_ref = ctypes.c_void_p()
        self._hotkey_ref = ctypes.c_void_p()
        self._callback = None

    def _library(self):
        if self._carbon is not None:
            return self._carbon
        carbon = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
        carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
        carbon.GetApplicationEventTarget.argtypes = []
        carbon.InstallEventHandler.restype = ctypes.c_int32
        carbon.InstallEventHandler.argtypes = [
            ctypes.c_void_p, HANDLER, ctypes.c_ulong, ctypes.POINTER(EventTypeSpec), ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        carbon.RemoveEventHandler.restype = ctypes.c_int32
        carbon.RemoveEventHandler.argtypes = [ctypes.c_void_p]
        carbon.RegisterEventHotKey.restype = ctypes.c_int32
        carbon.RegisterEventHotKey.argtypes = [
            ctypes.c_uint32, ctypes.c_uint32, EventHotKeyID, ctypes.c_void_p, ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        carbon.UnregisterEventHotKey.restype = ctypes.c_int32
        carbon.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]
        carbon.GetEventParameter.restype = ctypes.c_int32
        carbon.GetEventParameter.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        self._carbon = carbon
        return carbon

    def start(self) -> bool:
        if self.registered:
            return True
        try:
            carbon = self._library()
            target = carbon.GetApplicationEventTarget()
            if not self._handler_ref.value:
                self._callback = HANDLER(self._handle)
                spec = EventTypeSpec(four_cc("keyb"), K_EVENT_HOT_KEY_PRESSED)
                status = carbon.InstallEventHandler(
                    target, self._callback, 1, ctypes.byref(spec), None, ctypes.byref(self._handler_ref)
                )
                if status != 0:
                    self.error = f"InstallEventHandler failed ({status})."
                    self._handler_ref = ctypes.c_void_p()
                    return False
            status = carbon.RegisterEventHotKey(
                KVK_ANSI_P, MAC_MODIFIERS, EventHotKeyID(four_cc(SIGNATURE), HOTKEY_ID), target, 0,
                ctypes.byref(self._hotkey_ref),
            )
            if status != 0:
                self.error = (
                    "Another app already uses this shortcut."
                    if status == EVENT_HOT_KEY_EXISTS
                    else f"RegisterEventHotKey failed ({status})."
                )
                self._hotkey_ref = ctypes.c_void_p()
                self._remove_handler()
                return False
        except Exception as error:  # noqa: BLE001 - no shortcut is not a crash
            self.error = f"{type(error).__name__}: {error}"
            log.warning("Couldn't register the pause shortcut", exc_info=True)
            return False
        self.registered = True
        self.error = ""
        return True

    def _remove_handler(self) -> None:
        if self._handler_ref.value:
            self._library().RemoveEventHandler(self._handler_ref)
            self._handler_ref = ctypes.c_void_p()

    def stop(self) -> None:
        try:
            if self._hotkey_ref.value:
                self._library().UnregisterEventHotKey(self._hotkey_ref)
                self._hotkey_ref = ctypes.c_void_p()
            self._remove_handler()
        except Exception:  # noqa: BLE001 - quitting anyway
            log.warning("Couldn't unregister the pause shortcut", exc_info=True)
        self.registered = False

    def _handle(self, _call_ref, event, _user_data) -> int:
        try:
            found = EventHotKeyID()
            status = self._library().GetEventParameter(
                event, four_cc("----"), four_cc("hkid"), None, ctypes.sizeof(found), None, ctypes.byref(found)
            )
            if status == 0 and found.signature == four_cc(SIGNATURE) and found.id == HOTKEY_ID:
                self._pressed()
                return 0
        except Exception:  # noqa: BLE001 - never raise into Carbon
            log.warning("Couldn't read a hot key event", exc_info=True)
        return EVENT_NOT_HANDLED


class WindowsHotKeyApi:
    """The few user32 calls WindowsShortcut makes, on its own thread (a
    test hands it a stand-in)."""

    def __init__(self) -> None:
        from ctypes import wintypes

        self.MSG = wintypes.MSG
        user32 = self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32")
        user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        user32.RegisterHotKey.restype = wintypes.BOOL
        user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.UnregisterHotKey.restype = wintypes.BOOL
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        user32.GetMessageW.restype = ctypes.c_int
        user32.PeekMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT
        ]
        user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    def thread_id(self) -> int:
        return int(self.kernel32.GetCurrentThreadId())

    def make_queue(self, message) -> None:
        # Any call into user32 gives a thread its message queue; peeking
        # makes sure there is one before stop() posts to it.
        self.user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)

    def register(self) -> bool:
        return bool(self.user32.RegisterHotKey(None, HOTKEY_ID, WINDOWS_MODIFIERS, VK_P))

    def unregister(self) -> None:
        self.user32.UnregisterHotKey(None, HOTKEY_ID)

    def last_error(self) -> int:
        return ctypes.get_last_error()

    def get_message(self, message) -> int:
        return int(self.user32.GetMessageW(ctypes.byref(message), None, 0, 0))

    def post_quit(self, thread_id: int) -> None:
        self.user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)


class WindowsShortcut(Shortcut):
    """RegisterHotKey(NULL, ...) on a thread of its own, which waits for
    WM_HOTKEY. `on_press` is called on that thread."""

    text = WINDOWS_TEXT

    def __init__(self, on_press: Callable[[], object], api: Optional[Any] = None) -> None:
        super().__init__(on_press)
        self._api = api
        self._thread: Optional[threading.Thread] = None
        self._thread_id = 0
        self._ready = threading.Event()

    def _library(self):
        if self._api is None:
            self._api = WindowsHotKeyApi()
        return self._api

    def start(self) -> bool:
        if self.registered:
            return True
        self.stop()
        self._ready.clear()
        try:
            self._library()
        except Exception as error:  # noqa: BLE001 - no shortcut is not a crash
            self.error = f"{type(error).__name__}: {error}"
            return False
        self._thread = threading.Thread(target=self._run, name="pause-shortcut", daemon=True)
        self._thread.start()
        if not self._ready.wait(2.0):
            self.error = "The shortcut's thread didn't start."
            return False
        return self.registered

    def _run(self) -> None:
        api = self._api
        ok = False
        try:
            self._thread_id = api.thread_id()
            message = api.MSG()
            api.make_queue(message)
            if not api.register():
                code = api.last_error()
                self.error = (
                    "Another program already uses this shortcut."
                    if code == ERROR_HOTKEY_ALREADY_REGISTERED
                    else f"RegisterHotKey failed (error {code})."
                )
                return
            ok = True
        except Exception as error:  # noqa: BLE001 - say why, don't die silently
            self.error = f"{type(error).__name__}: {error}"
            log.warning("Couldn't register the pause shortcut", exc_info=True)
            return
        finally:
            self.registered = ok
            if ok:
                self.error = ""
            self._ready.set()
        try:
            while api.get_message(message) > 0:  # 0 is WM_QUIT, -1 an error
                if message.message == WM_HOTKEY and int(message.wParam) == HOTKEY_ID:
                    self._pressed()
        finally:
            api.unregister()
            self.registered = False

    def stop(self) -> None:
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            if self._thread_id:
                self._api.post_quit(self._thread_id)
            thread.join(2.0)
        self.registered = False


def create(on_press: Callable[[], object], system: Optional[str] = None) -> Shortcut:
    system = system or platform.system()
    if system == "Darwin":
        return MacShortcut(on_press)
    if system == "Windows":
        return WindowsShortcut(on_press)
    return Shortcut(on_press)


# -- Carbon types ----------------------------------------------------------------

SIGNATURE = "DCFx"
K_EVENT_HOT_KEY_PRESSED = 5
EVENT_NOT_HANDLED = -9874  # eventNotHandledErr: let others see it
EVENT_HOT_KEY_EXISTS = -9878  # eventHotKeyExistsErr: another app has it


class EventHotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


class EventTypeSpec(ctypes.Structure):
    _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]


HANDLER = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
