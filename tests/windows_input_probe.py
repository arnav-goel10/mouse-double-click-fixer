"""The mouse and the applications for WindowsMotionSmoothnessTests, in a
process of their own (Windows only; started by the test, never on its own).

The filter under test runs in the test's process. Its hook is a Python
callback, so how soon it answers depends on that process's other Python
threads, which share one GIL; a mouse, and the applications that receive its
input, share nothing with it. So the input the filter judges is sent from
here, and what applications receive is seen here: by a low-level hook
installed before the filter's (Windows calls the newest hook first, so this
one sees only what the filter lets through, as applications do), timed on
this process's own clock.

Commands come one per line of JSON on stdin; each answer is one line of JSON
on stdout. "ready" is printed once the observer is in place.

    {"do": "stream", "rate": 1000, "count": 400, "step": [2, 1], "scenario": "click", "settle_s": 0.4}
        -> {"sent": [seconds, ...], "observed": [[message, x, y, extra, seconds], ...]}
    {"do": "quit"}

A stream sends `count` relative moves of `step` at `rate` Hz: on their own
("move"), right after a click ("click"), or from inside a drag that is let go
a quarter of the way in ("drag"). "sent" holds when each move went out.
"""

import ctypes
import json
import sys
import threading
import time
from ctypes import wintypes

WH_MOUSE_LL = 14
WM_QUIT = 0x0012
MOUSEEVENTF_MOVE, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0001, 0x0002, 0x0004
MESSAGES = {0x0200, 0x0201, 0x0202, 0x0204, 0x0205, 0x0207, 0x0208, 0x020A, 0x020B, 0x020C, 0x020E}


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("mi", MOUSEINPUT)]


def main() -> int:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    LRESULT = ctypes.c_ssize_t
    HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, wintypes.DWORD]
    user32.SetWindowsHookExW.restype = ctypes.c_void_p
    user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    user32.CallNextHookEx.restype = LRESULT
    user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), ctypes.c_void_p, wintypes.UINT, wintypes.UINT]
    user32.GetMessageW.restype = ctypes.c_int
    user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    set_context = getattr(user32, "SetThreadDpiAwarenessContext", None)
    if set_context is not None:
        set_context.argtypes = [ctypes.c_void_p]
        set_context.restype = ctypes.c_void_p
        set_context(ctypes.c_void_p(-4))  # per-monitor aware v2: the hook's physical pixels

    observed: list = []
    record = ctypes.POINTER(MSLLHOOKSTRUCT)
    ready = threading.Event()
    thread_id = [0]

    def observe() -> None:
        if set_context is not None:
            set_context(ctypes.c_void_p(-4))
        thread_id[0] = kernel32.GetCurrentThreadId()

        @HOOKPROC
        def callback(code: int, message: int, data: int) -> int:
            if code >= 0 and message in MESSAGES:
                info = ctypes.cast(data, record).contents
                observed.append((int(message), info.pt.x, info.pt.y, int(info.dwExtraInfo), time.perf_counter()))
            return user32.CallNextHookEx(None, code, message, data)

        hook = user32.SetWindowsHookExW(WH_MOUSE_LL, callback, None, 0)
        ready.set()
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            pass
        user32.UnhookWindowsHookEx(hook)

    observer = threading.Thread(target=observe, daemon=True)
    observer.start()
    ready.wait(5)
    size = ctypes.sizeof(INPUT)

    def send(flags: int, dx: int = 0, dy: int = 0) -> None:
        item = INPUT(0, MOUSEINPUT(dx, dy, 0, flags, 0, 0))
        user32.SendInput(1, ctypes.byref(item), size)

    print("ready", flush=True)
    for line in sys.stdin:
        command = json.loads(line)
        if command.get("do") != "stream":
            break
        interval = 1.0 / command["rate"]
        count, (dx, dy), scenario = command["count"], command["step"], command["scenario"]
        release_at = count // 4 if scenario == "drag" else None
        if scenario == "click":
            send(MOUSEEVENTF_LEFTDOWN)
            time.sleep(0.08)
            send(MOUSEEVENTF_LEFTUP)
            time.sleep(0.005)
        elif scenario == "drag":
            send(MOUSEEVENTF_LEFTDOWN)
            time.sleep(0.05)
        del observed[:]
        sent = []
        began = time.perf_counter() + 0.002
        for index in range(count):
            wait = began + index * interval - time.perf_counter()
            if wait > 0:
                time.sleep(wait)
            if index == release_at:
                send(MOUSEEVENTF_LEFTUP)
            sent.append(time.perf_counter())
            send(MOUSEEVENTF_MOVE, dx, dy)
        time.sleep(command.get("settle_s", 0.4))
        print(json.dumps({"sent": sent, "observed": list(observed)}), flush=True)
    user32.PostThreadMessageW(thread_id[0], WM_QUIT, 0, 0)
    observer.join(2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
