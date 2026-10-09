"""System-wide mouse hooks for Windows and macOS.

Both backends run on a dedicated thread, feed a per-button :class:`BounceFilter`
and drop the events the filter rejects before any other application sees them.
Timestamps come from the operating system's own event records rather than from
when Python happened to wake up, so a busy machine cannot inflate a gap and let
bounce through.
"""

from __future__ import annotations

import math
import os
import platform
import threading
from time import monotonic, perf_counter
from typing import Callable, Iterable, Optional

from dataclasses import replace

from .core import BounceFilter, Button, ClickEvent, clamp_threshold

#: Set to 1 to let the filter act on synthetic clicks. Only used by the
#: automated end-to-end tests, which have no other way to produce input.
FILTER_INJECTED_ENV = "DCF_FILTER_INJECTED"

#: Marks events this app re-injects (a held release delivered late, or a
#: press re-ordered after it), so the hook passes them straight through.
INJECTED_MARK = 0x44434658  # "DCFX"

#: Marks pointer motion this app re-sends, one value per button whose queue it
#: waited in, so the motion tap knows which button's in-flight count it settles.
INJECTED_MOTION_MARKS = {INJECTED_MARK + 1 + index: button for index, button in enumerate(Button)}
MOTION_MARK_FOR = {button: mark for mark, button in INJECTED_MOTION_MARKS.items()}

#: Marks the pointer motion this app posts to put the pointer back after a
#: re-sent release pulled it to where the button came up (see _run_macos).
RESTORE_MARK = max(INJECTED_MOTION_MARKS) + 1

#: A release this close to where apps saw its press counts as a click made in
#: place (not the end of a drag). Motion that takes the pointer this far from
#: where the button came up settles it; less is a hand resting on the mouse.
STATIONARY_PX = 4.0

#: A re-sent event normally passes back through the hook within a millisecond
#: or two. If one never does (it was blocked, or lost), stop waiting for it.
IN_FLIGHT_TIMEOUT_S = 0.15

#: How far an event's own timestamp may sit from `monotonic()` and still be
#: believed. A tap sees an event within milliseconds of the driver making it.
CLOCK_TOLERANCE_S = 2.0

#: The double-click interval is a user setting that can change at any time,
#: so it is read live, but at most this often: the tap callback must stay cheap.
CLICK_INTERVAL_CACHE_S = 2.0


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
        # Per button: whether the release being held landed where apps saw its
        # press, and where it landed. Pointer motion that leaves that spot
        # settles such a release at once (see _motion).
        self._held_stationary: dict[Button, bool] = {}
        self._held_points: dict[Button, Optional[tuple[float, float]]] = {}
        # Per button: where the last press that reached apps landed.
        self._press_points: dict[Button, Optional[tuple[float, float]]] = {}
        self.tap_resets = 0  # times macOS disabled the tap and it was re-armed
        self.hook_rearms = 0  # times the Windows hook was re-installed
        self._timers: list[threading.Timer] = []
        # Per button: events re-sent but not yet seen coming back through the
        # hook, since when, and the real events queued behind them.
        self._in_flight: dict[Button, int] = {button: 0 for button in Button}
        self._in_flight_since: dict[Button, float] = {}
        # A queued entry is (pressed, template); pressed is None for motion.
        self._queued: dict[Button, list[tuple[Optional[bool], object]]] = {button: [] for button in Button}
        # Set by the platform runner: re-posts an event the hook suppressed.
        # `pressed` is None for pointer motion.
        self._inject: Callable[[Button, Optional[bool], object], object] = lambda _b, _p, _t: None
        # Set by the platform runner: turns the pointer-motion tap on or off.
        # Motion only needs watching while a release is held or re-sent.
        self._set_motion_tap: Callable[[bool], None] = lambda _wanted: None
        self._motion_tap_lock = threading.Lock()
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
        self._held_stationary.clear()
        self._held_points.clear()
        self._press_points.clear()
        for button in Button:
            self._in_flight[button] = 0
            self._queued[button].clear()
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
            self._release_queue(button)
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
        self,
        button: Button,
        pressed: bool,
        timestamp: Optional[float],
        template: object = None,
        allow_hold: bool = True,
        location: Optional[tuple[float, float]] = None,
    ) -> ClickEvent:
        """Decide one event. `template` is a copy of it, kept in case it has to
        be re-injected later; the caller suppresses whatever is not accepted.
        `allow_hold=False` says a release could not be re-injected later.
        `location` is where the pointer was, as (x, y), if the platform knows:
        a release held where apps saw its press is a click made in place, and
        pointer motion leaving that spot delivers it rather than the timer."""
        timestamp = self._normalise_time(timestamp)
        replay = []
        overdue = []
        with self._lock:
            overdue = self._expire_in_flight(button)
            click_filter = self._filters[button]
            click_filter.enabled = button in self._active
            event = click_filter.press(timestamp) if pressed else click_filter.release(timestamp, allow_hold)
            if event.is_bounce:
                self.filtered_count += 1
            if event.held:
                self._held_templates[button] = template
                self._held_points[button] = location
                # A release that came while the contact was still closing is
                # never a click ending, however still the pointer: it waits for
                # its press to come back, and motion must not settle it.
                self._held_stationary[button] = event.hold_reason == "lift" and _near(
                    self._press_points.get(button), location
                )
                timer = threading.Timer(
                    click_filter.threshold_ms / 1000.0, self._commit_held, (button, click_filter.held_id)
                )
                timer.daemon = True
                self._timers = [t for t in self._timers if t.is_alive()] + [timer]
                timer.start()
            elif event.cancels_held:
                self._held_templates.pop(button, None)
                self._forget_held(button)
            elif event.flush_held:
                # The held release was real after all: deliver it, then this.
                replay = [(False, self._held_templates.pop(button, None)), (True, template)]
                self._forget_held(button)
                if self._queued[button]:
                    # Real events already wait behind a re-sent one, and these
                    # came after them.
                    for entry in replay:
                        self._enqueue(button, *entry)
                    replay = []
                else:
                    self._track(button, len(replay))
            if event.accepted and (self._in_flight[button] or self._queued[button]):
                # A release this app re-sent a moment ago may still be on its
                # way. Letting this one through now could overtake it (apps
                # would see down, down, up, up), so it goes out right after.
                self._enqueue(button, pressed, template)
                event = replace(event, accepted=False, deferred=True)
            if pressed and not event.is_bounce:
                # Where apps saw the button go down. A press they never see (a
                # bounce, or the contact coming back mid-drag) must not move
                # it, or a drag would look like a click made in place.
                self._press_points[button] = location
        for replay_pressed, replay_template in overdue + replay:
            self._safe_inject(button, replay_pressed, replay_template)
        self._update_motion_tap()
        try:
            self._on_event(event)
        except Exception:  # a UI callback must never break the hook
            pass
        return event

    def _enqueue(self, button: Button, pressed: Optional[bool], template: object) -> None:
        """With the lock held: queue an event behind the re-sent ones."""
        if not self._queued[button]:
            # Should the re-sent event never come back, don't keep this one
            # waiting for the next event to notice.
            timer = threading.Timer(IN_FLIGHT_TIMEOUT_S + 0.05, self._expire_check, (button,))
            timer.daemon = True
            self._timers = [t for t in self._timers if t.is_alive()] + [timer]
            timer.start()
        self._queued[button].append((pressed, template))

    def _commit_held(self, button: Button, expected: Optional[float] = None) -> None:
        """The threshold passed with no press: the held release was real.
        A timer passes the release it was started for; stop() passes none."""
        send = False
        with self._lock:
            committed = self._filters[button].commit_held(expected)
            if committed:
                template = self._held_templates.pop(button, None)
                self._forget_held(button)
                send = self._send_or_queue(button, template)
        if send:
            self._safe_inject(button, False, template)
        self._update_motion_tap()

    def _send_or_queue(self, button: Button, template: object) -> bool:
        """With the lock held: a held release is now to be delivered. Returns
        True when the caller re-sends it now; otherwise it joins the real
        events already queued behind a re-sent one, since it came after them
        (sent first, apps could see this release before its own press)."""
        if self._queued[button]:
            self._enqueue(button, False, template)
            return False
        self._track(button, 1)
        return True

    def _forget_held(self, button: Button) -> None:
        """With the lock held: the held release is settled one way or another."""
        self._held_stationary.pop(button, None)
        self._held_points.pop(button, None)

    def _motion(self, template: object, location: Optional[tuple[float, float]] = None) -> bool:
        """The pointer moved (`template` is a copy of the motion event, and
        `location` where it took the pointer, if known).

        A release held where its press landed is settled once the pointer
        leaves the spot where the button came up: a click made in place ends
        when the pointer moves off, and apps must see its release where it
        happened, before the motion. Smaller motion goes through and the hold
        stays: it is a hand resting on the mouse, or a drag only starting,
        and the contact may yet come back. Motion arriving while re-sent
        events are still on their way waits behind them, so apps never see
        the pointer leave before the click is over.

        Returns True to let the event through unchanged, False when the
        platform must drop it because it was queued to be re-sent.
        """
        flushed = []
        overdue = []
        passes = True
        with self._lock:
            for button in Button:
                overdue += [(button, entry) for entry in self._expire_in_flight(button)]
                click_filter = self._filters[button]
                if click_filter.held_id is None or not self._held_stationary.get(button):
                    continue
                if location is not None and _near(self._held_points.get(button), location):
                    continue
                if click_filter.commit_held():
                    held_template = self._held_templates.pop(button, None)
                    if self._send_or_queue(button, held_template):
                        flushed.append((button, held_template))
                self._forget_held(button)
            for button in Button:
                if self._in_flight[button] or self._queued[button]:
                    self._enqueue(button, None, template)
                    passes = False
                    break
        for button, (pressed, queued_template) in overdue:
            self._safe_inject(button, pressed, queued_template)
        for button, held_template in flushed:
            self._safe_inject(button, False, held_template)
        self._update_motion_tap()
        return passes

    def _update_motion_tap(self) -> None:
        """Watch pointer motion only while it matters: a release held in place
        (motion settles it) or re-sent events still on their way (motion
        must wait behind them). Call without the lock held."""
        with self._motion_tap_lock:
            with self._lock:
                wanted = any(
                    (self._filters[button].held_id is not None and self._held_stationary.get(button, False))
                    or self._in_flight[button] > 0
                    or bool(self._queued[button])
                    for button in Button
                )
            try:
                self._set_motion_tap(wanted)
            except Exception:  # noqa: BLE001 - never break the event stream
                pass

    # -- keeping re-sent events in order ------------------------------------
    def _track(self, button: Button, count: int) -> None:
        """Count events about to be re-sent. Call with the lock held."""
        if not self._in_flight[button]:
            self._in_flight_since[button] = monotonic()
        self._in_flight[button] += count

    def _injected_passed(self, button: Button) -> None:
        """The hook saw one of this app's re-sent events go by. Once all of
        them have, the events queued behind them go out, in order."""
        with self._lock:
            if self._in_flight[button]:
                self._in_flight[button] -= 1
            queued = [] if self._in_flight[button] else self._take_queue(button)
        for pressed, template in queued:
            self._safe_inject(button, pressed, template)
        self._update_motion_tap()

    def _expire_check(self, button: Button) -> None:
        with self._lock:
            overdue = self._expire_in_flight(button)
        for pressed, template in overdue:
            self._safe_inject(button, pressed, template)
        self._update_motion_tap()

    def _expire_in_flight(self, button: Button) -> list:
        """With the lock held: give up on re-sent events that never came back,
        returning the queue to send now."""
        if self._in_flight[button] and monotonic() - self._in_flight_since.get(button, 0) > IN_FLIGHT_TIMEOUT_S:
            self._in_flight[button] = 0
            return self._take_queue(button)
        return []

    def _take_queue(self, button: Button) -> list:
        queued = self._queued[button][:]
        self._queued[button].clear()
        if queued:
            self._track(button, len(queued))
        return queued

    def _release_queue(self, button: Button) -> None:
        with self._lock:
            self._in_flight[button] = 0
            queued = self._take_queue(button)
        for pressed, template in queued:
            self._safe_inject(button, pressed, template)
        self._update_motion_tap()

    def _safe_inject(self, button: Button, pressed: Optional[bool], template: object) -> None:
        try:
            sent = self._inject(button, pressed, template) is not False
        except Exception:  # noqa: BLE001 - never break the event stream
            sent = False
        if not sent:
            # It will never come back through the hook; don't wait for it.
            self._injected_passed(button)

    def _normalise_time(self, timestamp: Optional[float]) -> float:
        """Use the operating system's event time only if it shares our clock.

        Event records are stamped when the driver produced the event, which is
        what a gap should be measured from. Both platforms happen to count from
        boot like `monotonic()` does, but synthetic events can carry a zero or
        otherwise unusable stamp, so on Windows the first event decides once
        whether these numbers are trustworthy. Mixing two clocks would corrupt
        every gap. macOS checks each stamp on its own instead (see
        _mach_timebase) and settles this check up front.
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

        def inject(button: Button, pressed: Optional[bool], template: object) -> bool:
            if pressed is None:
                return False  # re-sending pointer motion is not supported here
            # Re-post a suppressed press or release at the current pointer
            # position, tagged so this hook lets it through. `template` is the
            # event's own tick time: apps then see when it really happened, not
            # when it was let through.
            when = int(template) & 0xFFFFFFFF if isinstance(template, int) else 0
            event = INPUT(INPUT_MOUSE, MOUSEINPUT(0, 0, 0, SEND_FLAGS[(button, pressed)], when, INJECTED_MARK))
            return user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT)) == 1

        self._inject = inject

        self._thread_id = kernel32.GetCurrentThreadId()
        filter_injected = _filter_injected()
        kernel32.GetTickCount.restype = wintypes.DWORD
        ticks = TickClock(kernel32.GetTickCount)
        targets = InjectableWindows(user32, kernel32)
        # Windows silently removes a low-level hook whose callback runs past
        # LowLevelHooksTimeout, and says nothing: the thread lives on and no
        # click is filtered again. So a slow callback re-installs the hook,
        # and so does a timer once a minute, in case a stall went unseen.
        WM_REARM = 0x8000 + 0x44  # WM_APP + n
        WM_TIMER = 0x0113
        SLOW_CALLBACK_S = 0.2
        REARM_INTERVAL_MS = 60_000
        thread_id = kernel32.GetCurrentThreadId()

        @HOOKPROC
        def callback(code: int, message: int, data: int) -> int:
            started = perf_counter()
            try:
                return decide(code, message, data)
            finally:
                if perf_counter() - started > SLOW_CALLBACK_S:
                    user32.PostThreadMessageW(thread_id, WM_REARM, 0, 0)

        def decide(code: int, message: int, data: int) -> int:
            if code >= 0:
                entry = BUTTONS.get(int(message))
                if entry is not None:
                    info = ctypes.cast(data, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                    injected = bool(info.flags & LLMHF_INJECTED)
                    ours = info.dwExtraInfo == INJECTED_MARK
                    if ours:
                        self._injected_passed(entry[0])
                    elif not injected or filter_injected:
                        button, pressed = entry
                        # A release is only held back if it can be re-sent to
                        # the window that will receive it.
                        allow_hold = pressed or targets.accepts_injection(info.pt)
                        # `time` is the tick count, in milliseconds, recorded
                        # when the driver produced the event.
                        event = self._handle(
                            button, pressed, ticks.seconds(info.time), int(info.time), allow_hold=allow_hold
                        )
                        if not event.accepted:
                            return 1
            return user32.CallNextHookEx(None, code, message, data)

        user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
        user32.SetTimer.restype = ctypes.c_size_t
        user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]

        hook = user32.SetWindowsHookExW(WH_MOUSE_LL, callback, None, 0)
        if not hook:
            self._startup_error = ctypes.WinError(ctypes.get_last_error())
            self._ready.set()
            return
        self._started = True
        self._ready.set()
        timer = user32.SetTimer(None, 0, REARM_INTERVAL_MS, None)
        message = wintypes.MSG()
        try:
            while not self._stop_event.is_set():
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result in (0, -1) or message.message == WM_QUIT:
                    break
                if message.message in (WM_REARM, WM_TIMER):
                    fresh = user32.SetWindowsHookExW(WH_MOUSE_LL, callback, None, 0)
                    if fresh:
                        user32.UnhookWindowsHookEx(hook)
                        hook = fresh
                        self.hook_rearms += 1
                    continue
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        finally:
            if timer:
                user32.KillTimer(None, timer)
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
        # Every stamp is checked on its own as it arrives (see _mach_timebase),
        # so the once-per-run check in _normalise_time must not second-guess it.
        self._use_os_time = True
        filter_injected = _filter_injected()

        DRAGGED = (
            Quartz.kCGEventLeftMouseDragged,
            Quartz.kCGEventRightMouseDragged,
            Quartz.kCGEventOtherMouseDragged,
        )

        def inject(button: Button, pressed: Optional[bool], template: object) -> bool:
            # Re-post the kept copy of a suppressed event, tagged so this app's
            # taps let it through. It keeps its own timestamp and its own
            # location, so apps see the click when and where it happened: a
            # release that moved would land off the button that was clicked.
            if template is None:
                return False
            mark = INJECTED_MARK if pressed is not None else MOTION_MARK_FOR[button]
            Quartz.CGEventSetIntegerValueField(template, Quartz.kCGEventSourceUserData, mark)
            if Quartz.CGEventGetType(template) in DRAGGED:
                # By the time queued motion goes out, the button is up.
                Quartz.CGEventSetType(template, Quartz.kCGEventMouseMoved)
            positions = pointer_and_release(template) if pressed is False else None
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, template)
            if positions is not None:
                restore_pointer(*positions)
            return True

        def pointer_and_release(template: object) -> Optional[tuple]:
            # Where the hand has the pointer now, read before the release goes
            # out, and where the button came up.
            try:
                return Quartz.CGEventGetLocation(Quartz.CGEventCreate(None)), Quartz.CGEventGetLocation(template)
            except Exception:  # noqa: BLE001 - the release must go out regardless
                return None

        def restore_pointer(pointer: object, released_at: object) -> None:
            # A release posted where the button came up takes the pointer
            # there. After a drag let go while moving, the hand has carried
            # the pointer on since, so put it back. The move is not tracked:
            # the motion tap is usually off then and would never see it come
            # back, which would hold the next click for IN_FLIGHT_TIMEOUT_S.
            try:
                if math.hypot(pointer.x - released_at.x, pointer.y - released_at.y) < STATIONARY_PX:
                    return
                move = Quartz.CGEventCreateMouseEvent(
                    None, Quartz.kCGEventMouseMoved, pointer, Quartz.kCGMouseButtonLeft
                )
                Quartz.CGEventSetIntegerValueField(move, Quartz.kCGEventSourceUserData, RESTORE_MARK)
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, move)
            except Exception:  # noqa: BLE001 - the release itself already went out
                pass

        self._inject = inject
        click_counts = ClickCountRepair()
        def callback(proxy: object, event_type: int, event: object, refcon: object) -> object:
            # An exception here would make PyObjC return nothing, which drops
            # the click. Whatever goes wrong, the event goes through untouched.
            try:
                return decide(event_type, event)
            except Exception:  # noqa: BLE001
                return event

        def decide(event_type: int, event: object) -> object:
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
                self._injected_passed(entry[0])
                return event  # re-posted by this app; already decided
            source = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGEventSourceStateID)
            if source != Quartz.kCGEventSourceStateHIDSystemState and not filter_injected:
                return event  # synthetic click from another app; leave it alone

            button, pressed = entry
            if button is Button.MIDDLE:
                number = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGMouseEventButtonNumber)
                if number != 2:
                    return event  # a side button, not the middle one

            timestamp = to_seconds(Quartz.CGEventGetTimestamp(event))
            # Repair the click count first, so the copy kept for a re-send
            # carries the corrected count too.
            state = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGMouseEventClickState)
            corrected = click_counts.correct(button, pressed, state, timestamp)
            if corrected != state:
                Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventClickState, corrected)
            location = Quartz.CGEventGetLocation(event)
            result = self._handle(
                button,
                pressed,
                timestamp,
                Quartz.CGEventCreateCopy(event),
                location=(location.x, location.y),
            )
            click_counts.record(button, result)
            return event if result.accepted else None

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

        # A second tap sees pointer motion. It is only switched on while a
        # release is held in place or re-sent events are on their way (see
        # _update_motion_tap), so ordinary motion costs nothing.
        motion_state = {"tap": None, "enabled": False}

        def motion_callback(proxy: object, event_type: int, event: object, refcon: object) -> object:
            try:
                return motion_decide(event_type, event)
            except Exception:  # noqa: BLE001 - never drop motion on a bug
                return event

        def motion_decide(event_type: int, event: object) -> object:
            if event_type in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
                if motion_state["tap"] is not None and motion_state["enabled"]:
                    Quartz.CGEventTapEnable(motion_state["tap"], True)
                return event
            mark = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGEventSourceUserData)
            if mark == RESTORE_MARK:
                return event  # puts the pointer back after a re-sent release
            if mark in INJECTED_MOTION_MARKS:
                self._injected_passed(INJECTED_MOTION_MARKS[mark])
                return event  # re-sent by this app; already decided
            location = Quartz.CGEventGetLocation(event)
            return event if self._motion(Quartz.CGEventCreateCopy(event), (location.x, location.y)) else None

        motion_mask = 0
        for event_type in (Quartz.kCGEventMouseMoved, *DRAGGED):
            motion_mask |= Quartz.CGEventMaskBit(event_type)
        motion_tap = Quartz.CGEventTapCreate(
            Quartz.kCGHIDEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionDefault,
            motion_mask,
            motion_callback,
            None,
        )
        motion_source = None
        if motion_tap is not None:
            Quartz.CGEventTapEnable(motion_tap, False)
            motion_state["tap"] = motion_tap

            def set_motion_tap(wanted: bool) -> None:
                if wanted != motion_state["enabled"]:
                    motion_state["enabled"] = wanted
                    Quartz.CGEventTapEnable(motion_tap, wanted)

            self._set_motion_tap = set_motion_tap

        self._tap = tap
        source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        self._run_loop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(self._run_loop, source, Quartz.kCFRunLoopCommonModes)
        if motion_tap is not None:
            motion_source = Quartz.CFMachPortCreateRunLoopSource(None, motion_tap, 0)
            Quartz.CFRunLoopAddSource(self._run_loop, motion_source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(tap, True)
        self._started = True
        self._ready.set()
        try:
            while not self._stop_event.is_set():
                # A bounded run keeps the stop flag responsive even when the
                # run loop is woken for reasons of its own.
                Quartz.CFRunLoopRunInMode(Quartz.kCFRunLoopDefaultMode, 0.25, False)
        finally:
            self._set_motion_tap = lambda _wanted: None
            if motion_tap is not None:
                Quartz.CGEventTapEnable(motion_tap, False)
                Quartz.CFRunLoopRemoveSource(self._run_loop, motion_source, Quartz.kCFRunLoopCommonModes)
            Quartz.CGEventTapEnable(tap, False)
            Quartz.CFRunLoopRemoveSource(self._run_loop, source, Quartz.kCFRunLoopCommonModes)
            self._tap = None


class TickClock:
    """Windows event times as seconds that never jump backwards.

    Event records carry GetTickCount(): milliseconds since boot in 32 bits,
    which wrap to zero every 49.7 days. Measured naively, the first click
    after a wrap would be a huge negative gap, read as zero, and dropped as
    bounce. Differences taken modulo 2**32 stay correct across the wrap.
    """

    def __init__(self, current_tick: Optional[Callable[[], int]] = None) -> None:
        # GetTickCount, to place the first event on this process's own clock.
        self._current_tick = current_tick
        self._last_tick: Optional[int] = None
        self._seconds = 0.0

    def seconds(self, tick: int) -> Optional[float]:
        tick = int(tick) & 0xFFFFFFFF
        if tick == 0:
            return None  # synthetic input with no timestamp
        if self._last_tick is None:
            # Start on `monotonic()`, whichever clock Python uses for it, so
            # these times and the app's own agree.
            age = 0.0
            if self._current_tick is not None:
                age = ((int(self._current_tick()) - tick) & 0xFFFFFFFF) / 1000.0
            self._seconds = monotonic() - (age if age < 10 else 0.0)
        else:
            delta = (tick - self._last_tick) & 0xFFFFFFFF
            if delta >= 0x80000000:
                delta -= 0x100000000  # a slightly older event, not a wrap
            self._seconds += delta / 1000.0
        self._last_tick = tick
        return self._seconds


class InjectableWindows:
    """Whether a re-sent click would reach the window under the pointer.

    Windows silently drops input an app sends to a window running with
    higher rights (User Interface Privilege Isolation): Task Manager, an
    administrator terminal, an installer. A release held back over such a
    window could never be delivered, and the button would look stuck, so
    there the release goes straight through instead. Answers are cached
    briefly per process; the hook must stay fast.
    """

    CACHE_SECONDS = 5.0

    def __init__(self, user32, kernel32) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._wintypes = wintypes
        self._user32 = user32
        self._kernel32 = kernel32
        self._advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        # Not WindowFromPoint: it asks the window under the pointer to hit-test
        # itself (WM_NCHITTEST), which blocks on a window that is busy, long
        # enough for Windows to drop the hook. This walks the window list only.
        user32.GetDesktopWindow.restype = wintypes.HWND
        user32.ChildWindowFromPointEx.argtypes = [wintypes.HWND, wintypes.POINT, wintypes.UINT]
        user32.ChildWindowFromPointEx.restype = wintypes.HWND
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        self._advapi32.OpenProcessToken.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)
        ]
        self._advapi32.GetTokenInformation.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)
        ]

        class GUITHREADINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND),
                ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND),
                ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND),
                ("hwndCaret", wintypes.HWND),
                ("rcCaret", wintypes.RECT),
            ]

        self._GUITHREADINFO = GUITHREADINFO
        user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]
        self._own_pid = kernel32.GetCurrentProcessId()
        # An app running as administrator may send input anywhere that matters.
        self._elevated = self._token_elevated(kernel32.GetCurrentProcess()) is True
        self._cache: dict[int, tuple[float, bool]] = {}

    def accepts_injection(self, point) -> bool:
        if self._elevated:
            return True
        try:
            CWP_SKIPINVISIBLE, CWP_SKIPDISABLED, CWP_SKIPTRANSPARENT = 0x1, 0x2, 0x4
            desktop = self._user32.GetDesktopWindow()
            under = self._user32.ChildWindowFromPointEx(
                desktop, point, CWP_SKIPINVISIBLE | CWP_SKIPDISABLED | CWP_SKIPTRANSPARENT
            )
            windows = [under if under != desktop else None]
            # A drag sends its release to the window that captured the mouse,
            # wherever the pointer is.
            info = self._GUITHREADINFO()
            info.cbSize = self._ctypes.sizeof(info)
            if self._user32.GetGUIThreadInfo(0, self._ctypes.byref(info)) and info.hwndCapture:
                windows.append(info.hwndCapture)
            return all(self._window_ok(hwnd) for hwnd in windows if hwnd)
        except Exception:  # noqa: BLE001 - unsure: deliver at once, as before holding existed
            return False

    def _window_ok(self, hwnd) -> bool:
        pid = self._wintypes.DWORD()
        self._user32.GetWindowThreadProcessId(hwnd, self._ctypes.byref(pid))
        if not pid.value or pid.value == self._own_pid:
            return True
        now = monotonic()
        cached = self._cache.get(pid.value)
        if cached is not None and now - cached[0] < self.CACHE_SECONDS:
            return cached[1]
        answer = self._process_ok(pid.value)
        if len(self._cache) > 64:
            self._cache.clear()
        self._cache[pid.value] = (now, answer)
        return answer

    def _process_ok(self, pid: int) -> bool:
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        process = self._kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not process:
            return False  # protected or another user's: assume input can't reach it
        try:
            return self._token_elevated(process) is False
        finally:
            self._kernel32.CloseHandle(process)

    def _token_elevated(self, process) -> Optional[bool]:
        """True or False, or None when the token can't be read (which, for
        another process, means it runs with higher rights than this one)."""
        ctypes, wintypes = self._ctypes, self._wintypes
        TOKEN_QUERY = 0x0008
        TOKEN_ELEVATION = 20
        token = wintypes.HANDLE()
        if not self._advapi32.OpenProcessToken(process, TOKEN_QUERY, ctypes.byref(token)):
            return None
        try:
            elevated = wintypes.DWORD()
            size = wintypes.DWORD()
            if not self._advapi32.GetTokenInformation(
                token, TOKEN_ELEVATION, ctypes.byref(elevated), ctypes.sizeof(elevated), ctypes.byref(size)
            ):
                return None
            return bool(elevated.value)
        finally:
            self._kernel32.CloseHandle(token)


class ClickCountRepair:
    """Keep macOS's click count right when presses are suppressed.

    macOS numbers each press of a chain (1 = single, 2 = double, ...) before
    the filter runs. A press continues the chain when it comes within the
    double-click interval of the press before it, and that includes a press
    the filter then suppresses: a bounce adds one to the count and restarts
    the interval, so a click the user made well apart from the last one can
    reach apps as a double-click. So the count is worked out again by Apple's
    own rule, over the presses apps actually receive: a new chain (1) when
    macOS starts one (it does when the pointer moves away, too) or when the
    interval has passed since the last press apps got; otherwise one more
    than that press. A release carries its press's count.
    """

    def __init__(self, interval: Optional[Callable[[], float]] = None) -> None:
        # Reads the user's double-click interval, in seconds.
        self._read_interval = interval or _double_click_interval
        self._interval = 0.5  # Apple's default, until a read succeeds
        self._interval_read_at: Optional[float] = None
        # Per button: (time, count) of the last press apps received, and of
        # the press being decided now.
        self._delivered: dict[Button, tuple[float, int]] = {}
        self._pending: dict[Button, tuple[float, int]] = {}

    def interval(self) -> float:
        now = monotonic()
        if self._interval_read_at is None or now - self._interval_read_at >= CLICK_INTERVAL_CACHE_S:
            self._interval_read_at = now
            try:
                self._interval = float(self._read_interval())
            except Exception:  # noqa: BLE001 - keep the last good value
                pass
        return self._interval

    def correct(self, button: Button, pressed: bool, state: int, timestamp: float) -> int:
        """The count apps should see for this event, given macOS's `state`
        and the event's time in seconds."""
        last = self._delivered.get(button)
        if pressed:
            # Strictly less, as macOS compares.
            chained = state > 1 and last is not None and timestamp - last[0] < self.interval()
            # Never more than macOS itself counted.
            count = min(last[1] + 1, state) if chained else 1
            self._pending[button] = (timestamp, count)
            return count if state >= 1 else state  # synthetic events carry none
        if state < 1 or last is None:
            return state
        return min(state, last[1])

    def record(self, button: Button, result: ClickEvent) -> None:
        # Only a press that reaches apps moves the chain on: one let through,
        # or only re-ordered (flush_held, behind a late release) or held back
        # behind a re-sent event (deferred). Not a suppressed bounce, nor the
        # contact coming back mid-drag.
        pending = self._pending.pop(button, None)
        if result.pressed and not result.is_bounce and pending is not None:
            self._delivered[button] = pending


def _double_click_interval() -> float:
    """The user's double-click speed (System Settings > Mouse), in seconds."""
    from AppKit import NSEvent

    return float(NSEvent.doubleClickInterval())


def _near(point: Optional[tuple[float, float]], location: Optional[tuple[float, float]]) -> bool:
    """Whether two pointer positions are within STATIONARY_PX of each other."""
    if point is None or location is None:
        return False
    return math.hypot(location[0] - point[0], location[1] - point[1]) < STATIONARY_PX


def _mach_timebase() -> Callable[..., float]:
    """Return a converter from a CGEvent timestamp to `monotonic()` seconds.

    CGEventGetTimestamp comes in two units. Events from real hardware, as a
    kCGHIDEventTap sees them, carry mach ticks (41.67 ns each on Apple
    silicon, 1 ns on Intel); events another program posted carry
    nanoseconds. Both count from boot, leaving out sleep, as `monotonic()`
    does. So each event is read both ways and whichever reading lands near
    the current time is used; if neither does, the event is timed as it
    arrives. Deciding once per run instead would let a single posted click
    put every later real click on the wrong scale, 41.67 times too close.
    """
    numer, denom = _timebase_ratio()
    tick_seconds = numer / denom / 1_000_000_000

    def to_seconds(value: int, now: Optional[float] = None) -> float:
        now = monotonic() if now is None else now
        best, best_error = now, CLOCK_TOLERANCE_S
        for seconds in (float(value) * tick_seconds, float(value) / 1_000_000_000):
            error = abs(seconds - now)
            if error < best_error:
                best, best_error = seconds, error
        return best

    return to_seconds


def _timebase_ratio() -> tuple[int, int]:
    """mach_timebase_info: one mach tick is numer/denom nanoseconds."""
    import ctypes

    class TimebaseInfo(ctypes.Structure):
        _fields_ = [("numer", ctypes.c_uint32), ("denom", ctypes.c_uint32)]

    info = TimebaseInfo()
    try:
        status = ctypes.CDLL("/usr/lib/libSystem.B.dylib").mach_timebase_info(ctypes.byref(info))
    except (OSError, AttributeError):
        status = -1
    if status != 0 or not info.numer or not info.denom:
        return 1, 1  # ticks read as nanoseconds; posted events still time right
    return info.numer, info.denom
