"""System-wide mouse hooks for Windows and macOS.

Both backends run on a dedicated thread, feed a per-button :class:`BounceFilter`
and drop the events the filter rejects before any other application sees them.
Timestamps come from the operating system's own event records rather than from
when Python happened to wake up, so a busy machine cannot inflate a gap and let
bounce through.
"""

from __future__ import annotations

import os
import platform
import threading
from time import monotonic
from typing import Callable, Iterable, Optional

from .core import BounceFilter, Button, ClickEvent, clamp_threshold

#: Set to 1 to let the filter act on synthetic clicks. Only used by the
#: automated end-to-end tests, which have no other way to produce input.
FILTER_INJECTED_ENV = "DCF_FILTER_INJECTED"

#: Marks events this app re-injects (a held release delivered late, or a
#: press re-ordered after it), so the hook passes them straight through.
INJECTED_MARK = 0x44434658  # "DCFX"


class HookError(RuntimeError):
    """The global hook could not be installed, or stopped unexpectedly."""


def is_supported() -> bool:
    return platform.system() in ("Windows", "Darwin")


def _filter_injected() -> bool:
    return os.environ.get(FILTER_INJECTED_ENV, "") == "1"


class GlobalClickFilter:
    """Install a system-wide filter that suppresses switch bounce."""

    def __init__(
        self,
        threshold_ms: int,
        buttons: Iterable[Button],
        on_event: Optional[Callable[[ClickEvent], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._on_event = on_event or (lambda _event: None)
        self._on_error = on_error or (lambda _message: None)
        self._lock = threading.Lock()
        self._filters = {
            button: BounceFilter(threshold_ms, enabled=True, button=button) for button in Button
        }
        self._active = set(buttons)
        self._threshold_ms = clamp_threshold(threshold_ms)
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._ready = threading.Event()
        self._startup_error: Optional[BaseException] = None
        self._thread_id: Optional[int] = None
        self._tap = None
        self._run_loop = None
        self._use_os_time: Optional[bool] = None
        self._started = False
        # Per button: the event kept for a release that is being held back.
        self._held_templates: dict = {}
        # Repairs a kept copy's click count before it is re-sent (macOS only).
        self._repair_template = None
        self.tap_resets = 0  # times macOS disabled the tap and it was re-armed
        self._timers: list[threading.Timer] = []
        # Set by the platform runner: re-posts an event the hook suppressed.
        self._inject: Callable[[Button, bool, object], None] = lambda _b, _p, _t: None
        self.filtered_count = 0

    # -- lifecycle ---------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        if not is_supported():
            raise HookError("System-wide filtering is available on Windows and macOS only.")
        for click_filter in self._filters.values():
            click_filter.reset()
        self._stop_event.clear()
        self._ready.clear()
        self._startup_error = None
        self._use_os_time = None
        self._started = False
        self._held_templates.clear()
        self._thread = threading.Thread(target=self._run, name="dcf-hook", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=5):
            self.stop()
            raise HookError("The system-wide mouse hook did not start in time.")
        if self._startup_error is not None:
            error = self._startup_error
            self.stop()
            raise HookError(str(error)) from error

    def stop(self) -> None:
        # A release still held back must reach applications, or they would
        # believe the button is stuck down.
        for timer in self._timers:
            timer.cancel()
        self._timers.clear()
        for button in Button:
            self._commit_held(button)
        self._stop_event.set()
        thread = self._thread
        if thread is None or thread is threading.current_thread():
            return
        # Keep asking the thread to stop until it has. Its message queue (on
        # Windows) may not exist yet the first time, or its run loop may be
        # between iterations; a single request can be lost either way.
        deadline = monotonic() + 3.0
        while thread.is_alive() and monotonic() < deadline:
            self._request_thread_stop()
            thread.join(timeout=0.05)
        if thread.is_alive():
            # Never drop the handle to a live hook: `running` stays true, so
            # nothing starts a second hook on top of it, and a later stop()
            # can still reach it.
            return
        self._thread = None
        self._thread_id = None
        self._run_loop = None

    def _request_thread_stop(self) -> None:
        if platform.system() == "Windows" and self._thread_id is not None:
            import ctypes

            ctypes.windll.user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)  # WM_QUIT
        elif platform.system() == "Darwin" and self._run_loop is not None:
            import Quartz

            Quartz.CFRunLoopStop(self._run_loop)

    def update(self, threshold_ms: Optional[int] = None, buttons: Optional[Iterable[Button]] = None) -> None:
        """Change settings while the hook keeps running."""
        with self._lock:
            if threshold_ms is not None:
                self._threshold_ms = clamp_threshold(threshold_ms)
                for click_filter in self._filters.values():
                    click_filter.threshold_ms = self._threshold_ms
            if buttons is not None:
                self._active = set(buttons)

    # -- shared event handling --------------------------------------------
    def _handle(
        self, button: Button, pressed: bool, timestamp: Optional[float], template: object = None
    ) -> ClickEvent:
        """Decide one event. `template` is a copy of it, kept in case it has to
        be re-injected later; the caller suppresses whatever is not accepted."""
        timestamp = self._normalise_time(timestamp)
        replay = []
        with self._lock:
            click_filter = self._filters[button]
            click_filter.enabled = button in self._active
            event = click_filter.press(timestamp) if pressed else click_filter.release(timestamp)
            if event.is_bounce:
                self.filtered_count += 1
            if event.suppressed_run and template is not None and self._repair_template:
                self._repair_template(template, event.suppressed_run)
            if event.held:
                self._held_templates[button] = template
                timer = threading.Timer(click_filter.threshold_ms / 1000.0, self._commit_held, (button,))
                timer.daemon = True
                self._timers = [t for t in self._timers if t.is_alive()] + [timer]
                timer.start()
            elif event.cancels_held:
                self._held_templates.pop(button, None)
            elif event.flush_held:
                # The held release was real after all: deliver it, then this.
                replay = [(False, self._held_templates.pop(button, None)), (True, template)]
        for replay_pressed, replay_template in replay:
            self._safe_inject(button, replay_pressed, replay_template)
        try:
            self._on_event(event)
        except Exception:  # a UI callback must never break the hook
            pass
        return event

    def _commit_held(self, button: Button) -> None:
        """The threshold passed with no press: the held release was real."""
        with self._lock:
            if not self._filters[button].commit_held():
                return
            template = self._held_templates.pop(button, None)
        self._safe_inject(button, False, template)

    def _safe_inject(self, button: Button, pressed: bool, template: object) -> None:
        try:
            self._inject(button, pressed, template)
        except Exception:  # noqa: BLE001 - never break the event stream
            pass

    def _normalise_time(self, timestamp: Optional[float]) -> float:
        """Use the operating system's event time only if it shares our clock.

        Event records are stamped when the driver produced the event, which is
        what a gap should be measured from. Both platforms happen to count from
        boot like `monotonic()` does, but synthetic events can carry a zero or
        otherwise unusable stamp, so the first event decides once whether these
        numbers are trustworthy. Mixing two clocks would corrupt every gap.
        """
        now = monotonic()
        if timestamp is None:
            return now
        if self._use_os_time is None:
            self._use_os_time = abs(timestamp - now) < 2.0
        return timestamp if self._use_os_time else now

    def _run(self) -> None:
        try:
            if platform.system() == "Windows":
                self._run_windows()
            else:
                self._run_macos()
        except BaseException as error:  # noqa: BLE001 - surfaced to the UI
            if not self._started:
                # start() is still waiting and reports this itself; reporting
                # it here as well would show two dialogs for one failure.
                self._startup_error = error
                self._ready.set()
            elif not self._stop_event.is_set():
                self._on_error(str(error))

    # -- Windows -----------------------------------------------------------
    def _run_windows(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        WH_MOUSE_LL = 14
        WM_QUIT = 0x0012
        LLMHF_INJECTED = 0x00000001
        BUTTONS = {
            0x0201: (Button.LEFT, True),
            0x0202: (Button.LEFT, False),
            0x0204: (Button.RIGHT, True),
            0x0205: (Button.RIGHT, False),
            0x0207: (Button.MIDDLE, True),
            0x0208: (Button.MIDDLE, False),
        }

        class MSLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("pt", wintypes.POINT),
                ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t),  # ULONG_PTR
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
            # Only the mouse member of the union is used; it is the largest.
            _fields_ = [("type", wintypes.DWORD), ("mi", MOUSEINPUT)]

        INPUT_MOUSE = 0
        SEND_FLAGS = {
            (Button.LEFT, True): 0x0002, (Button.LEFT, False): 0x0004,
            (Button.RIGHT, True): 0x0008, (Button.RIGHT, False): 0x0010,
            (Button.MIDDLE, True): 0x0020, (Button.MIDDLE, False): 0x0040,
        }

        LRESULT = ctypes.c_ssize_t
        HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, wintypes.DWORD]
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.CallNextHookEx.restype = LRESULT
        user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), ctypes.c_void_p, wintypes.UINT, wintypes.UINT]
        user32.GetMessageW.restype = ctypes.c_int
        user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
        user32.SendInput.restype = wintypes.UINT

        def inject(button: Button, pressed: bool, _template: object) -> None:
            # Re-post a suppressed press or release at the current pointer
            # position, tagged so this hook lets it through.
            event = INPUT(INPUT_MOUSE, MOUSEINPUT(0, 0, 0, SEND_FLAGS[(button, pressed)], 0, INJECTED_MARK))
            user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT))

        self._inject = inject

        self._thread_id = kernel32.GetCurrentThreadId()
        filter_injected = _filter_injected()

        @HOOKPROC
        def callback(code: int, message: int, data: int) -> int:
            if code >= 0:
                entry = BUTTONS.get(int(message))
                if entry is not None:
                    info = ctypes.cast(data, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                    injected = bool(info.flags & LLMHF_INJECTED)
                    ours = info.dwExtraInfo == INJECTED_MARK
                    if not ours and (not injected or filter_injected):
                        button, pressed = entry
                        # `time` is the tick count, in milliseconds, recorded
                        # when the driver produced the event.
                        event = self._handle(button, pressed, info.time / 1000.0)
                        if not event.accepted:
                            return 1
            return user32.CallNextHookEx(None, code, message, data)

        hook = user32.SetWindowsHookExW(WH_MOUSE_LL, callback, None, 0)
        if not hook:
            self._startup_error = ctypes.WinError(ctypes.get_last_error())
            self._ready.set()
            return
        self._started = True
        self._ready.set()
        message = wintypes.MSG()
        try:
            while not self._stop_event.is_set():
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result in (0, -1) or message.message == WM_QUIT:
                    break
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        finally:
            user32.UnhookWindowsHookEx(hook)

    # -- macOS -------------------------------------------------------------
    def _run_macos(self) -> None:
        try:
            import Quartz
        except ImportError as error:  # pragma: no cover - packaging guard
            raise HookError("Install macOS support with: pip install -r requirements.txt") from error

        BUTTONS = {
            Quartz.kCGEventLeftMouseDown: (Button.LEFT, True),
            Quartz.kCGEventLeftMouseUp: (Button.LEFT, False),
            Quartz.kCGEventRightMouseDown: (Button.RIGHT, True),
            Quartz.kCGEventRightMouseUp: (Button.RIGHT, False),
            Quartz.kCGEventOtherMouseDown: (Button.MIDDLE, True),
            Quartz.kCGEventOtherMouseUp: (Button.MIDDLE, False),
        }
        to_seconds = _mach_timebase()
        filter_injected = _filter_injected()

        def inject(_button: Button, pressed: bool, template: object) -> None:
            # Re-post the kept copy of a suppressed event, stamped now and, for
            # a release, at the pointer's current position (a drag has moved
            # on since), tagged so this tap lets it through.
            if template is None:
                return
            Quartz.CGEventSetIntegerValueField(template, Quartz.kCGEventSourceUserData, INJECTED_MARK)
            Quartz.CGEventSetTimestamp(template, _mach_now())
            if not pressed:
                Quartz.CGEventSetLocation(template, Quartz.CGEventGetLocation(Quartz.CGEventCreate(None)))
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, template)

        self._inject = inject
        self._repair_template = lambda template, run: _rewrite_click_state(Quartz, template, run)

        def callback(_proxy: object, event_type: int, event: object, _refcon: object) -> object:
            # macOS disables a tap that takes too long, or when the user
            # revokes permission. Re-arm it instead of dying silently.
            if event_type in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
                self.tap_resets += 1
                if self._tap is not None:
                    Quartz.CGEventTapEnable(self._tap, True)
                return event

            entry = BUTTONS.get(event_type)
            if entry is None:
                return event
            if Quartz.CGEventGetIntegerValueField(event, Quartz.kCGEventSourceUserData) == INJECTED_MARK:
                return event  # re-posted by this app; already decided
            source = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGEventSourceStateID)
            if source != Quartz.kCGEventSourceStateHIDSystemState and not filter_injected:
                return event  # synthetic click from another app; leave it alone

            button, pressed = entry
            if button is Button.MIDDLE:
                number = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGMouseEventButtonNumber)
                if number != 2:
                    return event  # a side button, not the middle one

            result = self._handle(
                button, pressed, to_seconds(Quartz.CGEventGetTimestamp(event)), Quartz.CGEventCreateCopy(event)
            )
            if not result.accepted:
                return None
            if result.suppressed_run:
                _rewrite_click_state(Quartz, event, result.suppressed_run)
            return event

        mask = 0
        for event_type in BUTTONS:
            mask |= Quartz.CGEventMaskBit(event_type)

        tap = Quartz.CGEventTapCreate(
            Quartz.kCGHIDEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionDefault,
            mask,
            callback,
            None,
        )
        if tap is None:
            self._startup_error = HookError(
                "macOS refused the event tap. Grant Accessibility permission to DoubleClick Fixer "
                "in System Settings > Privacy & Security > Accessibility, then try again."
            )
            self._ready.set()
            return

        self._tap = tap
        source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        self._run_loop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(self._run_loop, source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(tap, True)
        self._started = True
        self._ready.set()
        try:
            while not self._stop_event.is_set():
                # A bounded run keeps the stop flag responsive even when the
                # run loop is woken for reasons of its own.
                Quartz.CFRunLoopRunInMode(Quartz.kCFRunLoopDefaultMode, 0.25, False)
        finally:
            Quartz.CGEventTapEnable(tap, False)
            Quartz.CFRunLoopRemoveSource(self._run_loop, source, Quartz.kCFRunLoopCommonModes)
            self._tap = None


def _rewrite_click_state(Quartz, event: object, suppressed: int) -> None:
    """Undo the click count macOS added for presses that were suppressed.

    macOS tags each press with how many clicks it counts as. A suppressed
    bounce still bumped that counter, so without this a real double-click
    arrives labeled as a triple-click.
    """
    try:
        state = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGMouseEventClickState)
        if state > 1:
            Quartz.CGEventSetIntegerValueField(
                event, Quartz.kCGMouseEventClickState, max(1, state - suppressed)
            )
    except Exception:  # pragma: no cover - never break the event stream
        pass


def _mach_now() -> int:
    import ctypes

    libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    libc.mach_absolute_time.restype = ctypes.c_uint64
    return int(libc.mach_absolute_time())


def _mach_timebase() -> Callable[[int], float]:
    """Return a converter from mach absolute time to monotonic seconds."""
    try:
        import ctypes

        class MachTimebase(ctypes.Structure):
            _fields_ = [("numer", ctypes.c_uint32), ("denom", ctypes.c_uint32)]

        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        info = MachTimebase()
        libc.mach_timebase_info(ctypes.byref(info))
        scale = (info.numer / info.denom) / 1_000_000_000
    except Exception:  # pragma: no cover - fall back to plain nanoseconds
        scale = 1 / 1_000_000_000
    return lambda value: float(value) * scale
