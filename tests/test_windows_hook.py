"""A real Windows hook test: inject clicks and see what survives the filter.

This one genuinely clicks, so it only runs when `DCF_E2E=1` is set (CI does).
A second low-level hook installed *before* the filter acts as the observer:
Windows calls the most recently installed hook first, so anything the filter
suppresses never reaches it — the same thing an application would see.
"""

import os
import platform
import threading
import time
import unittest

from app.core import Button
from app.platform import GlobalClickFilter

WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202


def _run_e2e() -> bool:
    return platform.system() == "Windows" and os.environ.get("DCF_E2E") == "1"


@unittest.skipUnless(_run_e2e(), "needs Windows and DCF_E2E=1 (it injects real clicks)")
class WindowsHookTests(unittest.TestCase):
    def setUp(self) -> None:
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.wintypes = wintypes
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        os.environ["DCF_FILTER_INJECTED"] = "1"  # the test has no real mouse
        self.observed: list[int] = []
        self._start_observer()
        self.filter = GlobalClickFilter(60, [Button.LEFT])
        self.filter.start()
        self.assertTrue(self.filter.running)

    def tearDown(self) -> None:
        self.filter.stop()
        self._stop_observer()
        os.environ.pop("DCF_FILTER_INJECTED", None)

    # -- the observer hook -------------------------------------------------
    def _start_observer(self) -> None:
        ctypes = self.ctypes
        wintypes = self.wintypes
        ready = threading.Event()
        self._observer_thread_id = None
        self._observer_stop = threading.Event()

        LRESULT = ctypes.c_ssize_t
        HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        self.user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, wintypes.DWORD]
        self.user32.SetWindowsHookExW.restype = ctypes.c_void_p
        # Without argtypes, ctypes rejects the 64-bit LPARAM it is handed.
        self.user32.CallNextHookEx.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self.user32.CallNextHookEx.restype = LRESULT
        self.user32.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            ctypes.c_void_p,
            wintypes.UINT,
            wintypes.UINT,
        ]
        self.user32.GetMessageW.restype = ctypes.c_int
        self.user32.PostThreadMessageW.argtypes = [
            wintypes.DWORD,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]

        def run() -> None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self._observer_thread_id = kernel32.GetCurrentThreadId()

            @HOOKPROC
            def callback(code: int, message: int, data: int) -> int:
                if code >= 0 and message in (WM_LBUTTONDOWN, WM_LBUTTONUP):
                    self.observed.append(int(message))
                return self.user32.CallNextHookEx(None, code, message, data)

            self._observer_callback = callback  # keep it alive
            hook = self.user32.SetWindowsHookExW(14, callback, None, 0)
            ready.set()
            message = wintypes.MSG()
            while not self._observer_stop.is_set():
                if self.user32.GetMessageW(ctypes.byref(message), None, 0, 0) <= 0:
                    break
            self.user32.UnhookWindowsHookEx(hook)

        self._observer = threading.Thread(target=run, daemon=True)
        self._observer.start()
        ready.wait(5)
        time.sleep(0.2)

    def _stop_observer(self) -> None:
        self._observer_stop.set()
        if self._observer_thread_id:
            self.user32.PostThreadMessageW(self._observer_thread_id, 0x0012, 0, 0)
        self._observer.join(timeout=2)

    # -- injection ---------------------------------------------------------
    def _click(self, down: bool) -> None:
        self.user32.mouse_event(0x0002 if down else 0x0004, 0, 0, 0, 0)
        time.sleep(0.02)

    def test_bounce_is_blocked_and_real_clicks_survive(self) -> None:
        self.observed.clear()

        self._click(True)           # a clean click
        self._click(False)
        time.sleep(0.3)

        self._click(True)           # a click that bounces
        self._click(False)
        time.sleep(0.01)
        self._click(True)           # the bounce, ~10 ms after the release
        self._click(False)
        time.sleep(0.3)

        self._click(True)           # a deliberate double-click
        self._click(False)
        time.sleep(0.15)
        self._click(True)
        self._click(False)
        time.sleep(0.4)

        presses = self.observed.count(WM_LBUTTONDOWN)
        releases = self.observed.count(WM_LBUTTONUP)
        self.assertEqual(presses, 4, f"expected the bounce to be removed, saw {self.observed}")
        self.assertEqual(releases, 4, "a suppressed press must take its release with it")
        self.assertEqual(self.filter.filtered_count, 1)


if __name__ == "__main__":
    unittest.main()
