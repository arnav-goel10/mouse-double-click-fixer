"""Native global mouse hooks for Windows and macOS."""

from __future__ import annotations

import platform
import threading
from typing import Callable, Optional

from .core import DoubleClickEngine


class GlobalMouseMonitor:
    """Suppress the second click in an accidental global double-click."""

    def __init__(
        self,
        on_click: Callable[[bool, Optional[float]], None],
        threshold_ms: int,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._on_click = on_click
        self._on_error = on_error or (lambda _message: None)
        self._engine = DoubleClickEngine(threshold_ms=threshold_ms, suppression_enabled=True)
        self._listener = None
        self._hook_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._suppress_button_up = False
        self._ready_event = threading.Event()
        self._startup_error: Optional[BaseException] = None
        self._thread_id: Optional[int] = None

    def start(self) -> None:
        self._engine.reset()
        self._stop_event.clear()
        self._ready_event.clear()
        self._startup_error = None
        system = platform.system()
        if system in ("Windows", "Darwin"):
            self._hook_thread = threading.Thread(target=self._run_hook, daemon=True)
        else:
            raise RuntimeError("Global fixing is supported on Windows and macOS only.")
        self._hook_thread.start()
        if not self._ready_event.wait(timeout=1):
            self.stop()
            raise RuntimeError("The global mouse hook did not start.")
        if self._startup_error is not None:
            error = self._startup_error
            self.stop()
            raise RuntimeError(str(error)) from error

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread_id is not None and platform.system() == "Windows":
            import ctypes

            ctypes.windll.user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        if self._hook_thread is not None and self._hook_thread is not threading.current_thread():
            self._hook_thread.join(timeout=1)
        self._hook_thread = None
        self._thread_id = None

    def _handle_click(self, pressed: bool) -> bool:
        if not pressed:
            should_suppress = self._suppress_button_up
            self._suppress_button_up = False
            return not should_suppress
        result = self._engine.process_click()
        self._on_click(result.is_double_click, result.interval_ms)
        self._suppress_button_up = not result.accepted
        return result.accepted

    def _run_hook(self) -> None:
        try:
            if platform.system() == "Windows":
                self._run_windows_hook()
            else:
                self._run_macos_hook()
        except BaseException as error:
            self._startup_error = error
            self._ready_event.set()
            if not self._stop_event.is_set():
                self._on_error(str(error))

    def _run_windows_hook(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        wh_mouse_ll = 14
        wm_lbuttondown = 0x0201
        wm_lbuttonup = 0x0202
        lresult = ctypes.c_ssize_t
        hook_proc = ctypes.WINFUNCTYPE(lresult, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, hook_proc, ctypes.c_void_p, wintypes.DWORD]
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.CallNextHookEx.restype = lresult
        user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), ctypes.c_void_p, wintypes.UINT, wintypes.UINT]
        user32.GetMessageW.restype = wintypes.BOOL
        user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        self._thread_id = kernel32.GetCurrentThreadId()

        @hook_proc
        def callback(code: int, message: int, _data: int) -> int:
            if code >= 0 and message in (wm_lbuttondown, wm_lbuttonup):
                if not self._handle_click(message == wm_lbuttondown):
                    return 1
            return user32.CallNextHookEx(None, code, message, _data)

        hook = user32.SetWindowsHookExW(wh_mouse_ll, callback, None, 0)
        if not hook:
            error_code = ctypes.get_last_error()
            error = ctypes.WinError(error_code)
            self._startup_error = error
            self._ready_event.set()
            return
        self._ready_event.set()
        message = wintypes.MSG()
        try:
            while not self._stop_event.is_set() and user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        finally:
            user32.UnhookWindowsHookEx(hook)

    def _run_macos_hook(self) -> None:
        try:
            import Quartz
        except ImportError as error:
            raise RuntimeError("Install macOS support with: pip install -r requirements.txt") from error

        down = Quartz.kCGEventLeftMouseDown
        up = Quartz.kCGEventLeftMouseUp

        def callback(_proxy: object, event_type: int, event: object, _refcon: object) -> object:
            if event_type in (down, up) and not self._handle_click(event_type == down):
                return None
            return event

        mask = (1 << down) | (1 << up)
        tap = Quartz.CGEventTapCreate(
            Quartz.kCGHIDEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionDefault,
            mask,
            callback,
            None,
        )
        if tap is None:
            error = RuntimeError("macOS denied the event tap. Enable Accessibility permission for this app.")
            self._startup_error = error
            self._ready_event.set()
            return
        self._ready_event.set()
        self._listener = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        run_loop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(run_loop, self._listener, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(tap, True)
        while not self._stop_event.is_set():
            Quartz.CFRunLoopRunInMode(Quartz.kCFRunLoopDefaultMode, 0.1, False)
        Quartz.CFRunLoopStop(run_loop)
