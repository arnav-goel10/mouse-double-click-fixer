"""System-wide mouse hooks for Windows and macOS.

Both backends run on a dedicated thread, feed a per-button :class:`BounceFilter`
and drop the events the filter rejects before any other application sees them.
Timestamps come from the operating system's own event records, or on Windows,
whose records are only as fine as its 15.6 ms tick, from a precise clock read
as the event arrives with any lateness the tick shows taken off, so a busy
machine cannot inflate a gap and let bounce through.
"""

from __future__ import annotations

import itertools
import logging
import math
import os
import platform
import threading
from collections import deque
from contextlib import contextmanager
from time import monotonic, perf_counter
from typing import Callable, Iterable, NamedTuple, Optional

from dataclasses import replace

from .core import BounceFilter, Button, ClickEvent, DeliveryDelay, PeakLateness, clamp_threshold

log = logging.getLogger(__name__)

#: Set to 1 to let the filter act on synthetic clicks. Only used by the
#: automated end-to-end tests, which have no other way to produce input.
FILTER_INJECTED_ENV = "DCF_FILTER_INJECTED"

#: Marks the button events this app re-sends (a held release delivered late,
#: and the events kept behind one so that apps see them in the order they
#: happened), so the hook passes them straight through.
INJECTED_MARK = 0x44434658  # "DCFX"

#: Marks pointer motion this app re-sends, one value per button whose queue it
#: waited in, so the hook knows which button's re-sends it settles (see
#: GlobalClickFilter._injected_passed).
INJECTED_MOTION_MARKS = {INJECTED_MARK + 1 + index: button for index, button in enumerate(Button)}
MOTION_MARK_FOR = {button: mark for mark, button in INJECTED_MOTION_MARKS.items()}

#: Marks the pointer motion this app posts to put the pointer back after a
#: re-sent release pulled it to where the button came up (see _run_macos).
RESTORE_MARK = max(INJECTED_MOTION_MARKS) + 1

#: Marks the pointer motion that takes the pointer to where a held release
#: happened, just before the release is re-sent there (Windows; see
#: WindowsHook). The move back after it carries the button's motion mark.
TELEPORT_MARK = RESTORE_MARK + 1

#: A mark travels in an event's user data (kCGEventSourceUserData on macOS,
#: dwExtraInfo on Windows). Its low 32 bits are one of the kinds above, so
#: `mark & MARK_KIND_MASK` tells this app's events apart. An event the hook
#: waits to see come back also carries, above them, its number in its
#: button's sequence of re-sends (see GlobalClickFilter._resend), so the hook
#: knows which one passed. 0 there numbers none: such an event settles
#: nothing (RESTORE_MARK and TELEPORT_MARK are never waited for).
MARK_KIND_MASK = 0xFFFFFFFF
MARK_SEQ_SHIFT = 32
#: The numbers in a mark run from 1 to this and start again: 31 bits keep a
#: mark a positive 64-bit value, which kCGEventSourceUserData is signed.
MARK_SEQ_SPAN = 0x7FFFFFFF


def make_mark(kind: int, seq: int = 0) -> int:
    """The mark for an event of `kind` that is re-send number `seq` of its
    button (any positive integer), or that numbers none (0)."""
    return (_seq_field(seq) << MARK_SEQ_SHIFT) | kind if seq else kind


def mark_kind(mark: int) -> int:
    """Which of this app's marks `mark` is, if any: INJECTED_MARK, ..."""
    return int(mark) & MARK_KIND_MASK


def mark_seq(mark: int) -> int:
    """The re-send number in `mark`, as it carries it (see _seq_field); 0
    for none."""
    return (int(mark) >> MARK_SEQ_SHIFT) & MARK_SEQ_SPAN


def _seq_field(seq: int) -> int:
    """Re-send number `seq` as a mark carries it: 1 to MARK_SEQ_SPAN."""
    return (seq - 1) % MARK_SEQ_SPAN + 1

#: Windows tags mouse messages it makes from pen and touch input with this
#: signature in dwExtraInfo (the low byte varies). They are absolute
#: positions, not hand motion, so they are never held back or re-based.
PEN_SIGNATURE_MASK = 0xFFFFFF00
PEN_SIGNATURE = 0xFF515700

#: GetTickCount, which stamps Windows input, advances in ticks of 15.6 ms. An
#: event whose stamp is this much older than the tick count when the hook
#: sees it may still have come at once.
TICK_MS = 16

#: How much after an event really happened windows_event_time can place it:
#: up to TICK_MS of lateness is left on, and the tick the event is stamped
#: with can trail it by another tick and the millisecond GetTickCount drops.
WINDOWS_STAMP_ERROR_S = (2 * TICK_MS + 1) / 1000

#: Windows silently removes a low-level hook it judges too slow, and says
#: nothing. The hook is re-installed this often in case that happened unseen.
WINDOWS_REARM_INTERVAL_MS = 15_000

#: A release this close to where apps saw its press counts as a click made in
#: place (not the end of a drag). Motion that takes the pointer this far from
#: where the button came up settles it; less is a hand resting on the mouse.
STATIONARY_PX = 4.0

#: A re-sent release takes the pointer back to where the button came up. If
#: the pointer had moved on by more than this, it is put back again.
RESTORE_MIN_PX = 0.5

#: A re-sent event passes back through the hook about as late as real events
#: reach it: a millisecond or two on an idle machine, far longer on a busy
#: one. If one never does (another app's tap swallowed it, or it was lost),
#: the events waiting behind it go out once it has been on its way twice as
#: long as any event in the last two seconds was late (see
#: core.PeakLateness), but never sooner than the first bound or later than
#: the second.
IN_FLIGHT_TIMEOUT_S = 0.15
IN_FLIGHT_MAX_TIMEOUT_S = 0.5
#: The check that gives up on such an event runs this long after it is due,
#: so that it finds it overdue.
IN_FLIGHT_CHECK_SLACK_S = 0.005

#: macOS disables an event tap that it judges too slow. If it does so this
#: many times within this many seconds, re-arming the tap would only fight
#: the system, so the filter stops and every click goes through untouched.
TAP_DISABLE_LIMIT = 3
TAP_DISABLE_WINDOW_S = 30.0
TAP_DISABLED_MESSAGE = (
    "macOS kept switching the filter off, so it has stopped and clicks now go through unfiltered. "
    "Turn it on again from the menu bar."
)

#: How far an event's own timestamp may sit from `monotonic()` and still be
#: believed. A tap sees an event within milliseconds of the driver making it.
CLOCK_TOLERANCE_S = 2.0

#: The double-click interval is a user setting that can change at any time,
#: so it is read live, but at most this often: the tap callback must stay cheap.
CLICK_INTERVAL_CACHE_S = 2.0

#: Where macOS keeps that setting (System Settings > Mouse > Double-click
#: speed), in seconds, and what it is when the user never changed it.
DOUBLE_CLICK_KEY = "com.apple.mouse.doubleClickThreshold"
DEFAULT_DOUBLE_CLICK_S = 0.5


class HookError(RuntimeError):
    """The global hook could not be installed, or stopped unexpectedly."""


def is_supported() -> bool:
    return platform.system() in ("Windows", "Darwin")


def _filter_injected() -> bool:
    return os.environ.get(FILTER_INJECTED_ENV, "") == "1"


# Places that ignored an error and have logged it. Most run on every event,
# so each logs its first error only. The app logs through a queue, so this is
# safe from the hook thread and timer threads alike.
_logged_sites: set[str] = set()
_logged_sites_lock = threading.Lock()


def _log_ignored(site: str) -> None:
    """Call from an `except` block that carries on regardless."""
    try:
        with _logged_sites_lock:
            if site in _logged_sites:
                return
            _logged_sites.add(site)
        log.warning("Ignored an error in %s (later ones there are not logged)", site, exc_info=True)
    except Exception:  # noqa: BLE001 - logging must never break the event stream
        pass


class GlobalClickFilter:
    """Install a system-wide filter that suppresses switch bounce.

    `on_error` hears, from the hook thread, that the hook stopped on its own
    after it had started. On macOS, `permission_ok` says whether the app may
    still filter input (see permissions.event_tap_allowed); it is asked each
    time macOS disables a tap, before the tap is re-armed. When it says no,
    the filter fails open (whatever it holds back goes out, then the hook
    ends) and calls `on_permission_lost` from the hook thread instead of
    `on_error`.
    """

    def __init__(
        self,
        threshold_ms: int,
        buttons: Iterable[Button],
        on_event: Optional[Callable[[ClickEvent], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        permission_ok: Optional[Callable[[], bool]] = None,
        on_permission_lost: Optional[Callable[[], None]] = None,
    ) -> None:
        self._on_event = on_event or (lambda _event: None)
        self._on_error = on_error or (lambda _message: None)
        self._permission_ok = permission_ok or (lambda: True)
        self._on_permission_lost = on_permission_lost or (lambda: None)
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
        # The Windows hook's hidden window (see SessionWindow), while it runs.
        self._hook_window = None
        self._use_os_time: Optional[bool] = None
        self._started = False
        # Why the hook last ended, for the log.
        self._end_reason = "stopped"
        # Per button: the event kept for a release that is being held back.
        self._held_templates: dict = {}
        # Per button: whether the release being held landed where apps saw its
        # press, and where it landed. Pointer motion that leaves that spot
        # settles such a release at once (see _motion).
        self._held_stationary: dict[Button, bool] = {}
        self._held_points: dict[Button, Optional[tuple[float, float]]] = {}
        # Per button: where the last press that reached apps landed.
        self._press_points: dict[Button, Optional[tuple[float, float]]] = {}
        # Whether two pointer positions count as one spot: a release there is
        # a click made in place, and motion that stays there leaves it held.
        # Windows replaces it with its own drag rectangle (see _run_windows).
        self._is_near: Callable[[Optional[tuple], Optional[tuple]], bool] = _near
        # When set to a list, the Windows hook appends (message, watched,
        # seconds) for every callback; the end-to-end test reads its cost.
        self._callback_timings: Optional[list] = None
        # When macOS disabled a tap lately (see _tap_disabled).
        self._tap_disables: list[float] = []
        self.tap_resets = 0  # times macOS disabled the tap and it was re-armed
        self.hook_rearms = 0  # times the Windows hook was re-installed
        # Per button: the timer for its held release (see _handle).
        self._held_timers: dict[Button, threading.Timer] = {}
        # How late button events reach the hook here; sets how long the
        # timer for a held release waits (see _fallback_delay).
        self._lateness = DeliveryDelay()
        # How late any event reached it lately; sets how long to wait for a
        # re-sent event to come back (see _in_flight_timeout).
        self._peak_lateness = PeakLateness()
        # How much after an event happened its timestamp can say it did:
        # nothing on macOS, up to two ticks on Windows (see _run_windows). An
        # event stamped less than this after a re-send went out may have been
        # made before it (see _give_up).
        self._stamp_error_s = 0.0
        # Per button: the events re-sent as it and not yet seen coming back
        # through the hook, oldest first, as (number, when sent). They come
        # back in the order sent, so once one does, those before it came
        # back or never will (see _injected_passed).
        self._in_flight: dict[Button, deque] = {button: deque() for button in Button}
        # Per button: the releases among them, as (number, when the release
        # happened): another button's later events must not overtake them
        # (see _follow_earlier_releases).
        self._releases_in_flight: dict[Button, deque] = {button: deque() for button in Button}
        # Per button: the number its last re-send was given (see _resend).
        self._last_seq: dict[Button, int] = {button: 0 for button in Button}
        # Per button: real events waiting to be re-sent until its re-sent
        # events have come back. An entry is (order, pressed, template, sent
        # as, stamp): order counts entries across every queue, pressed is
        # None for motion, "sent as" is the button the event is re-sent and
        # counted in flight for, its own, and stamp is when it happened.
        # Another button's event waits in this queue behind a release that
        # came before it, and that button's later events then follow it here
        # (see _handle and _queue_for).
        self._queued: dict[Button, list[tuple]] = {button: [] for button in Button}
        self._queue_order = itertools.count()
        # Per button: the other button's queue its events wait in, if they do.
        self._parked: dict[Button, Button] = {}
        # Per button: when the first of its releases waiting in a queue happened.
        self._queued_release_at: dict[Button, float] = {}
        # Per button: the one check that gives up on its re-sent events
        # should they never come back, as (timer, token) (see _arm_check).
        self._checks: dict[Button, tuple] = {}
        # Every event decided to be re-sent, as (button sent as, pressed,
        # template, number), in the order decided, on whatever thread. One
        # thread at a time sends them, in that order (see _send_outbox).
        self._outbox: deque = deque()
        self._send_lock = threading.Lock()
        # Set by the platform runner: re-posts an event the hook suppressed,
        # marked with its number (see make_mark). `pressed` is None for
        # pointer motion. False says it could not be sent.
        self._inject: Callable[[Button, Optional[bool], object, int], object] = lambda _b, _p, _t, _n: None
        # Whether pointer motion needs judging (see _update_motion_tap): read
        # on every move by the macOS tap, without a lock. Raised with the lock
        # held the moment something becomes pending, so every move decided
        # after that is judged.
        self._motion_wanted = False
        # Set by the Windows runner: turns its hook's motion watch on or off
        # (WindowsHook.set_watch). Motion only needs watching while a release
        # is held or re-sent.
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
        self._end_reason = "stopped"
        self._held_templates.clear()
        self._held_stationary.clear()
        self._held_points.clear()
        self._press_points.clear()
        self._tap_disables.clear()
        for button in Button:
            self._in_flight[button].clear()
            self._releases_in_flight[button].clear()
            self._queued[button].clear()
        self._parked.clear()
        self._queued_release_at.clear()
        self._outbox.clear()
        self._motion_wanted = False
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
        self._let_everything_go()
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
        # The hook went on deciding until it ended: what it held back or
        # queued since the flush above would be left to timers. Send it now,
        # and leave no timer behind.
        self._let_everything_go()

    def tap_alive(self) -> bool:
        """Whether the filter is still in the event stream. On macOS that is
        whether the main tap is enabled: the system can disable it, or drop
        it with the Accessibility grant, while the hook thread lives on."""
        if not self.running:
            return False
        if platform.system() != "Darwin":
            return True
        tap = self._tap
        if tap is None:
            return False
        try:
            import Quartz

            return bool(Quartz.CGEventTapIsEnabled(tap))
        except Exception:  # noqa: BLE001 - unsure: don't make a health check churn the filter
            _log_ignored("the tap health check")
            return True

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
        pointer motion leaving that spot delivers it rather than the timer.

        First, every held release this event's timestamp shows was real is
        settled, its own button's among them (see _settle_due); this event,
        if it is to reach apps, then goes out behind them, and behind its own
        button's earlier events and other buttons' earlier releases that
        have not reached apps yet."""
        arrived = monotonic()
        timestamp = self._normalise_time(timestamp)
        with self._lock:
            self._lateness.add(arrived - timestamp)
            self._peak_lateness.add(arrived - timestamp, arrived)
            self._give_up(button, timestamp)
            settled, behind = self._settle_due(timestamp)
            click_filter = self._filters[button]
            click_filter.enabled = button in self._active
            held_at = click_filter.held_at
            event = click_filter.press(timestamp) if pressed else click_filter.release(timestamp, allow_hold)
            if event.is_bounce:
                self.filtered_count += 1
            if event.held:
                self._held_templates[button] = template
                self._held_points[button] = location
                # A release that came while the contact was still closing is
                # never a click ending, however still the pointer: it waits for
                # its press to come back, and motion must not settle it.
                self._held_stationary[button] = event.hold_reason == "lift" and self._is_near(
                    self._press_points.get(button), location
                )
                # Motion is judged while a release is held at a known place:
                # motion stamped past the window settles it (see _motion).
                if location is not None:
                    self._motion_wanted = True
                self._start_held_timer(
                    button, self._fallback_delay(click_filter.threshold_ms, timestamp, arrived), click_filter.held_id
                )
            elif event.cancels_held:
                self._held_templates.pop(button, None)
                self._forget_held(button)
            elif event.flush_held:
                # The held release was real after all (its button is no longer
                # filtered): deliver it, behind whatever came before it, then
                # this. (A press past the window has already settled it,
                # above, and comes back deferred.)
                queue = self._send_or_queue(button, self._held_templates.pop(button, None), held_at, behind)
                self._forget_held(button)
                if queue is None:
                    self._resend(button, True, template)
                else:
                    self._enqueue(queue, True, template, button, timestamp)
            if event.accepted:
                # It must reach apps after the events of its button still
                # waiting to be re-sent, after the releases it settled, which
                # happened first, and after another button's release that
                # happened first and has not reached apps yet: it waits where
                # they wait, or is re-sent right behind them.
                queue = self._queue_for(button)
                resend = False
                if queue is None and allow_hold:
                    earlier, on_way = self._follow_earlier_releases(button, timestamp)
                    queue = behind if behind is not None else earlier
                    resend = bool(settled) or (on_way and not self._in_flight[button])
                if queue is None and not resend and self._in_flight[button]:
                    # Events of this button re-sent a moment ago may still be
                    # on their way. Letting this one through now could overtake
                    # them (apps would see down, down, up, up), so it goes out
                    # after them, and so after any release of another button
                    # it follows that is on its way too.
                    queue = button
                if queue is not None:
                    self._enqueue(queue, pressed, template, button, timestamp)
                    event = replace(event, accepted=False, deferred=True)
                elif resend:
                    self._resend(button, pressed, template, timestamp)
                    event = replace(event, accepted=False, deferred=True)
            if pressed and not event.is_bounce:
                # Where apps saw the button go down. A press they never see (a
                # bounce, or the contact coming back mid-drag) must not move
                # it, or a drag would look like a click made in place.
                self._press_points[button] = location
        self._send_outbox()
        self._update_motion_tap()
        try:
            self._on_event(event)
        except Exception:  # noqa: BLE001 - a UI callback must never break the hook
            _log_ignored("the click event callback")
        return event

    def _start_held_timer(self, button: Button, seconds: float, held_id: Optional[int]) -> None:
        """With the lock held: settle `button`'s release held now after
        `seconds`, if nothing has by then (see _commit_held)."""
        previous = self._held_timers.get(button)
        if previous is not None:
            previous.cancel()  # its release is settled already
        timer = threading.Timer(seconds, self._commit_held, (button, held_id))
        timer.daemon = True
        self._held_timers[button] = timer
        timer.start()

    def _settle_due(self, timestamp: float) -> tuple[list, Optional[Button]]:
        """With the lock held: settle every held release that an event stamped
        `timestamp` shows was real (see BounceFilter.due). See _settle for
        what it returns."""
        return self._settle([button for button, click_filter in self._filters.items() if click_filter.due(timestamp)])

    def _settle(self, buttons: list) -> tuple[list, Optional[Button]]:
        """With the lock held: these buttons' held releases were real. They go
        out in the order they happened, the oldest first: each is re-sent now,
        unless it has to wait in a queue (see _send_or_queue), and once one
        has, those after it follow it there. Returns the buttons settled, and
        the queue the last of them joined, or None: an event that comes after
        them waits there, or else is re-sent right behind them."""
        behind = None
        for button in sorted(buttons, key=lambda each: self._filters[each].held_at):
            released_at = self._filters[button].held_at
            self._filters[button].commit_held()
            template = self._held_templates.pop(button, None)
            self._forget_held(button)
            behind = self._send_or_queue(button, template, released_at, behind)
        return buttons, behind

    def _fallback_delay(self, threshold_ms: float, released_at: float, arrived: float) -> float:
        """With the lock held: how long the timer for a release held just now
        waits before settling it.

        The timer is for silence, a release with nothing after it (a click
        with the hand kept still): any later event settles it sooner, and
        exactly (see _settle_due). It must not fire while a press made inside
        the window may still be on its way, so it waits out the window from
        when the release happened plus how late this machine delivers events
        (see DeliveryDelay), and never less than the window from when the
        release arrived: a release that came out of a backlog has its
        comeback press right behind it in that backlog."""
        window = threshold_ms / 1000.0
        allowance = self._lateness.allowance_ms() / 1000.0
        deadline = max(released_at + window + allowance, arrived + window)
        return max(0.0, deadline - monotonic())

    def _commit_held(self, button: Button, expected: Optional[float] = None) -> None:
        """The threshold passed with no press: the held release was real.
        A timer passes the release it was started for."""
        with self._lock:
            click_filter = self._filters[button]
            released_at = click_filter.held_at
            if click_filter.commit_held(expected):
                template = self._held_templates.pop(button, None)
                self._forget_held(button)
                self._send_or_queue(button, template, released_at)
        self._send_outbox()
        self._update_motion_tap()

    def _send_or_queue(
        self, button: Button, template: object, released_at: float, behind: Optional[Button] = None
    ) -> Optional[Button]:
        """With the lock held: a held release, made at `released_at`, is now
        to be delivered. It joins the events of its button already waiting to
        be re-sent (see _queue_for), since it came after them (sent first,
        apps could see this release before its own press), or else `behind`,
        where an older release settled with it waits, or else the queue where
        another button's earlier release waits (see
        _follow_earlier_releases). Returns the queue it joined, or None when
        nothing had to wait and it is re-sent now."""
        queue = self._queue_for(button)
        if queue is None:
            queue = behind
        if queue is None:
            queue, _on_way = self._follow_earlier_releases(button, released_at)
        if queue is None:
            self._resend(button, False, template, released_at)
        else:
            self._enqueue(queue, False, template, button, released_at)
        return queue

    def _follow_earlier_releases(self, button: Button, stamp: float) -> tuple[Optional[Button], bool]:
        """With the lock held: whether an event of `button` that happened at
        `stamp` has to follow another button's release that happened before
        it and has not reached apps yet; going first, it would show apps the
        two buttons down together when they never were. Returns the queue to
        wait in, when that button has events waiting in one (the release
        among them, or ahead of them), and whether such a release is on its
        way: then the event is to be re-sent, which puts it right behind."""
        on_way = False
        for other in Button:
            if other is button:
                continue
            releases = self._releases_in_flight[other]
            queued_at = self._queued_release_at.get(other)
            if not ((releases and releases[0][1] < stamp) or (queued_at is not None and queued_at < stamp)):
                continue
            queue = self._queue_for(other)
            if queue is not None:
                return queue, False
            on_way = True
        return None, on_way

    def _forget_held(self, button: Button) -> None:
        """With the lock held: the held release is settled one way or another."""
        self._held_stationary.pop(button, None)
        self._held_points.pop(button, None)

    def _tap_disabled(self, now: Optional[float] = None) -> bool:
        """macOS disabled the main tap. Returns True when that has now
        happened TAP_DISABLE_LIMIT times within TAP_DISABLE_WINDOW_S, and the
        filter should stop rather than re-arm it again."""
        now = monotonic() if now is None else now
        with self._lock:
            recent = [moment for moment in self._tap_disables if now - moment < TAP_DISABLE_WINDOW_S]
            self._tap_disables = recent + [now]
            return len(self._tap_disables) >= TAP_DISABLE_LIMIT

    def _motion(
        self,
        template: object,
        location: Optional[tuple[float, float]] = None,
        timestamp: Optional[float] = None,
    ) -> bool:
        """The pointer moved (`template` is a copy of the motion event,
        `location` where it took the pointer, and `timestamp` when it
        happened, if known).

        Motion settles a held release in two cases. Motion stamped past the
        release's window shows the release was real, as any event does (see
        _settle_due), but only for a release held at a known place. Where the
        pointer's place means nothing (Windows: a hidden pointer, pen and
        touch, a remote session), motion leaves the release held, so as not
        to wait behind it: a re-sent move is absolute, and in a game's
        mouse-look it would jump the view. The timer and button events settle
        such a release. And a click made in place (a release held where its
        press landed) is settled as soon as the pointer leaves the spot where
        the button came up, inside the window or not: the click ends when the
        pointer moves off, and apps must see its release there, before the
        motion. Smaller motion passes and the hold stays: it is a hand
        resting on the mouse, or a drag only starting, and the contact may
        yet come back.

        Motion then goes out behind the releases it settled, waiting where
        the last of them waits if any had to wait in a queue (see _settle).
        Otherwise it waits behind events still waiting to be re-sent, or else
        behind events re-sent a moment ago that may still be on their way, so
        apps never see the pointer leave before a click is over.

        Returns True to let the event through unchanged, False when the
        platform must drop it because it was queued to be re-sent.
        """
        arrived = monotonic()
        if timestamp is not None:
            timestamp = self._normalise_time(timestamp)
        with self._lock:
            if timestamp is not None:
                self._peak_lateness.add(arrived - timestamp, arrived)
                for button in Button:
                    self._give_up(button, timestamp)
            settle = []
            for button, click_filter in self._filters.items():
                if click_filter.held_id is None:
                    continue
                place = self._held_points.get(button)
                if place is not None and timestamp is not None and click_filter.due(timestamp):
                    settle.append(button)
                elif self._held_stationary.get(button) and (location is None or not self._is_near(place, location)):
                    settle.append(button)
            _settled, queue = self._settle(settle)
            if queue is None:
                # None of the releases it settled had to wait in a queue. It
                # waits behind events that do, which go out only when their
                # queue does, or else behind events re-sent a moment ago.
                waiting = [button for button in Button if self._queued[button]]
                waiting = waiting or [button for button in Button if self._in_flight[button]]
                queue = waiting[0] if waiting else None
            if queue is not None:
                self._enqueue(queue, None, template)
        self._send_outbox()
        self._update_motion_tap()
        return queue is None

    def _update_motion_tap(self) -> None:
        """Judge pointer motion only while it matters: a release is held at a
        known place (motion stamped past its window settles it, and motion
        leaving a click made in place settles that sooner) or re-sent events
        are still on their way (motion must wait behind them). Otherwise the
        hook lets every move straight through after one look at
        _motion_wanted. The flag is raised as soon as something becomes
        pending (with the lock held); this lowers it once nothing is. Call
        without the lock held."""
        with self._motion_tap_lock:
            with self._lock:
                wanted = any(
                    (self._filters[button].held_id is not None and self._held_points.get(button) is not None)
                    or bool(self._in_flight[button])
                    or bool(self._queued[button])
                    for button in Button
                )
                self._motion_wanted = wanted
            try:
                self._set_motion_tap(wanted)
            except Exception:  # noqa: BLE001 - never break the event stream
                _log_ignored("switching the motion tap")

    # -- keeping re-sent events in order ------------------------------------
    def _queue_for(self, button: Button) -> Optional[Button]:
        """With the lock held: the queue an event of `button` joins to reach
        apps after everything of that button still waiting to be re-sent, or
        None when nothing waits. That is its own queue, unless its events
        wait in another button's queue behind a release that came before
        them (see _handle): its later events follow them there, or they would
        overtake them."""
        parked = self._parked.get(button)
        if parked is not None:
            return parked
        return button if self._queued[button] else None

    def _enqueue(
        self,
        button: Button,
        pressed: Optional[bool],
        template: object,
        send_as: Optional[Button] = None,
        stamp: Optional[float] = None,
    ) -> None:
        """With the lock held: queue an event behind `button`'s re-sent ones.
        `send_as` is the event's own button, if not `button`, and `stamp` when
        it happened."""
        send_as = button if send_as is None else send_as
        self._motion_wanted = True
        self._queued[button].append((next(self._queue_order), pressed, template, send_as, stamp))
        if send_as is not button:
            self._parked[send_as] = button
        if pressed is False and stamp is not None:
            self._queued_release_at.setdefault(send_as, stamp)
        self._arm_check(button)

    def _resend(self, button: Button, pressed: Optional[bool], template: object, stamp: Optional[float] = None) -> None:
        """With the lock held: re-send an event as `button`, in turn. It gets
        the next number in that button's sequence, which its mark carries, and
        counts in flight until the hook sees it come back. `stamp` is when it
        happened, for a release."""
        self._last_seq[button] += 1
        seq = self._last_seq[button]
        self._in_flight[button].append((seq, monotonic()))
        if pressed is False and stamp is not None:
            self._releases_in_flight[button].append((seq, stamp))
        self._outbox.append((button, pressed, template, seq))
        self._motion_wanted = True

    def _send_outbox(self) -> None:
        """Send every event decided to be re-sent, in the order the decisions
        were made under the lock, whichever thread made them. Call without
        the lock held, after deciding.

        One thread sends at a time. If another is sending, it sends this
        thread's events too, so the hook's callback never waits for a timer's
        send; it looks again after letting go, so none is left behind."""
        if not self._outbox:
            return  # nothing to send, or another thread is sending it
        while self._send_lock.acquire(blocking=False):
            try:
                while True:
                    with self._lock:
                        if not self._outbox:
                            break
                        button, pressed, template, seq = self._outbox.popleft()
                    self._safe_inject(button, pressed, template, seq)
            finally:
                self._send_lock.release()
            with self._lock:
                if not self._outbox:
                    return

    def _safe_inject(self, button: Button, pressed: Optional[bool], template: object, seq: int) -> None:
        try:
            sent = self._inject(button, pressed, template, seq) is not False
        except Exception:  # noqa: BLE001 - never break the event stream
            _log_ignored("re-sending an event")
            sent = False
        if not sent:
            self._resend_lost(button, seq)

    def _injected_passed(self, button: Button, seq: int) -> None:
        """The hook saw one of this app's re-sent events go by, numbered `seq`
        in its mark (see make_mark; 0 numbers none and settles nothing). The
        ones re-sent as this button before it are settled too: they come back
        in the order sent, so each came back before it or never will. Once
        none is left, the events queued behind them go out, in order."""
        with self._lock:
            in_flight = self._in_flight[button]
            if seq and in_flight:
                # The mark carries the number modulo MARK_SEQ_SPAN: it is the
                # first one in flight that it fits, unless that is none of
                # them (one given up on already).
                first = in_flight[0][0]
                number = first + (seq - _seq_field(first)) % MARK_SEQ_SPAN
                if number <= in_flight[-1][0]:
                    self._settle_in_flight(button, number)
        self._send_outbox()
        self._update_motion_tap()

    def _resend_lost(self, button: Button, seq: int) -> None:
        """Re-send `seq` of `button` never went out: it will never come back
        through the hook, so it is not waited for. Only it: those sent before
        it may still be on their way."""
        with self._lock:
            in_flight = self._in_flight[button]
            for index in range(len(in_flight) - 1, -1, -1):  # it is one of the latest
                if in_flight[index][0] == seq:
                    del in_flight[index]
                    break
            releases = self._releases_in_flight[button]
            for index in range(len(releases) - 1, -1, -1):
                if releases[index][0] == seq:
                    del releases[index]
                    break
            if not in_flight:
                self._take_queue(button)
        self._send_outbox()
        self._update_motion_tap()

    def _settle_in_flight(self, button: Button, number: int) -> None:
        """With the lock held: `button`'s re-sends up to `number` are done
        with. Once none is left in flight, its queue goes out."""
        in_flight = self._in_flight[button]
        while in_flight and in_flight[0][0] <= number:
            in_flight.popleft()
        releases = self._releases_in_flight[button]
        while releases and releases[0][0] <= number:
            releases.popleft()
        if not in_flight:
            self._take_queue(button)

    def _take_queue(self, button: Button) -> None:
        """With the lock held: re-send `button`'s queue, in order."""
        queued, self._queued[button] = self._queued[button], []
        self._resend_queued(queued)

    def _resend_queued(self, queued: list) -> None:
        """With the lock held: re-send these queue entries, in order."""
        for _order, pressed, template, send_as, stamp in queued:
            if pressed is not None:
                # A button's waiting events all wait in one queue: this one.
                self._parked.pop(send_as, None)
                self._queued_release_at.pop(send_as, None)
            self._resend(send_as, pressed, template, stamp)

    # -- giving up on re-sent events ------------------------------------------
    def _in_flight_timeout(self) -> float:
        """With the lock held: how long to wait for a re-sent event to come
        back before giving up on it (see IN_FLIGHT_TIMEOUT_S)."""
        follows = 2 * self._peak_lateness.worst_ms(monotonic()) / 1000
        return max(IN_FLIGHT_TIMEOUT_S, min(IN_FLIGHT_MAX_TIMEOUT_S, follows))

    def _give_up(self, button: Button, stamp: Optional[float] = None) -> None:
        """With the lock held: give up on `button`'s re-sent events that are
        overdue (see _in_flight_timeout), as never coming back: another app's
        tap swallowed them, or they were lost. When an event stamped `stamp`
        is the reason to look, only those re-sent before it happened count:
        one re-sent after it was behind it on the way, so the event's arrival
        says nothing of it. Since a stamp can say an event happened up to
        _stamp_error_s after it did, only those re-sent that much before it
        count. Once none is left, the queue goes out."""
        in_flight = self._in_flight[button]
        if not in_flight:
            return
        due = monotonic() - self._in_flight_timeout()
        number = None
        for seq, sent_at in in_flight:
            if sent_at >= due or (stamp is not None and sent_at >= stamp - self._stamp_error_s):
                break
            number = seq
        if number is not None:
            self._settle_in_flight(button, number)

    def _arm_check(self, button: Button) -> None:
        """With the lock held: events wait behind `button`'s re-sent ones.
        Should those never come back, a check gives up on them once they are
        overdue, if events still wait then, rather than waiting for a later
        event to show it (see _expire_check). One check per button is ever
        pending, set for when the oldest re-send in flight is due, or sooner,
        for when the late event that lengthens the wait stops counting: the
        wait is shorter from then on, and the check looks again."""
        if button in self._checks or not self._in_flight[button]:
            return
        now = monotonic()
        sent_at = self._in_flight[button][0][1]
        due = sent_at + self._in_flight_timeout()
        due = min(due, max(sent_at + IN_FLIGHT_TIMEOUT_S, self._peak_lateness.counts_until(now)))
        seconds = max(0.0, due - now) + IN_FLIGHT_CHECK_SLACK_S
        token = object()
        timer = threading.Timer(seconds, self._expire_check, (button, token))
        timer.daemon = True
        self._checks[button] = (timer, token)
        timer.start()

    def _expire_check(self, button: Button, token: object) -> None:
        """The check for `button`'s re-sent events is due (see _arm_check)."""
        with self._lock:
            check = self._checks.get(button)
            if check is None or check[1] is not token:
                return  # cancelled (see _give_up_all)
            del self._checks[button]
            if not self._queued[button]:
                # What it was set for came back, and what waited went out.
                # Giving up on re-sends still on their way would only let a
                # real event made before they went out, so ahead of them on
                # the way, pass them.
                return
            # What waits goes out behind those given up on, so it reaches
            # apps after them even if they were only slow.
            self._give_up(button)
            if self._queued[button]:
                # Events still wait: for the same re-send, not overdue yet (an
                # event came late since, or a less late one still lengthens
                # the wait), or for one sent after it, which is due later.
                # The check moves on to when that is due.
                self._arm_check(button)
        self._send_outbox()
        self._update_motion_tap()

    def _give_up_all(self) -> None:
        """Give up on every re-sent event in flight, and send every event
        waiting behind them in the order they came: when the filter stops or
        fails open, and when macOS re-enables a tap it had disabled (events
        posted meanwhile went past it, and never come back)."""
        with self._lock:
            waiting = sorted((entry for button in Button for entry in self._queued[button]), key=lambda entry: entry[0])
            for button in Button:
                self._in_flight[button].clear()
                self._releases_in_flight[button].clear()
                self._queued[button] = []
            for timer, _token in self._checks.values():
                timer.cancel()  # nothing left to check
            self._checks.clear()
            self._resend_queued(waiting)
        self._send_outbox()
        self._update_motion_tap()

    def _let_everything_go(self) -> None:
        """The filter is stopping, or failing open: what it holds back must
        reach applications, or they would believe a button is stuck down.
        Held releases are settled, oldest first, and everything waiting goes
        out in order. Timers set before this do nothing after it."""
        with self._lock:
            for timer in self._held_timers.values():
                timer.cancel()
            self._held_timers.clear()
            self._settle([button for button, click_filter in self._filters.items() if click_filter.held_id is not None])
        self._give_up_all()

    def _normalise_time(self, timestamp: Optional[float]) -> float:
        """Use the operating system's event time only if it shares our clock.

        Event records are stamped when the driver produced the event, which is
        what a gap should be measured from, but a stamp can be zero or
        otherwise unusable, so the first one decides once whether these
        numbers are trustworthy. Mixing two clocks would corrupt every gap.
        Both platform hooks settle this up front instead: macOS checks each
        stamp on its own (see _mach_timebase), and Windows times events on
        the precise clock as they arrive (see windows_event_time).
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
            else:
                if self._end_reason == "stopped":
                    self._end_reason = f"failed: {error}"
                if not self._stop_event.is_set():
                    self._on_error(str(error))
        finally:
            if self._started:
                log.info(
                    "Hook ended, %s (tap resets %d, hook re-arms %d)",
                    self._end_reason,
                    self.tap_resets,
                    self.hook_rearms,
                )

    # -- Windows -----------------------------------------------------------
    def _run_windows(self) -> None:
        import ctypes
        from ctypes import wintypes

        api = WindowsApi()
        user32, kernel32 = api.user32, api.kernel32
        # This thread works in the same physical pixels as the hook's event
        # records (the app's own threads are per-monitor aware through Qt).
        api.use_physical_pixels()

        WH_MOUSE_LL = 14
        WM_QUIT = 0x0012
        WM_MOUSEMOVE = 0x0200
        BUTTONS = {
            0x0201: (Button.LEFT, True),
            0x0202: (Button.LEFT, False),
            0x0204: (Button.RIGHT, True),
            0x0205: (Button.RIGHT, False),
            0x0207: (Button.MIDDLE, True),
            0x0208: (Button.MIDDLE, False),
        }
        PMSLLHOOKSTRUCT = ctypes.POINTER(api.MSLLHOOKSTRUCT)
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
        user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
        user32.SetTimer.restype = ctypes.c_size_t
        user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        kernel32.GetTickCount.restype = wintypes.DWORD
        get_tick_count = kernel32.GetTickCount
        call_next = user32.CallNextHookEx

        targets = InjectableWindows(user32, kernel32)
        sender = InputSender(api, self._resend_lost)
        hook_logic = WindowsHook(
            self,
            api,
            accepts_injection=lambda x, y: targets.accepts_injection(wintypes.POINT(x, y)),
            filter_injected=_filter_injected(),
            send=sender.submit,
        )
        watch = hook_logic.watch
        self._inject = hook_logic.inject
        self._is_near = api.within_drag_rect
        with self._motion_tap_lock:
            self._set_motion_tap = hook_logic.set_watch
        # Every stamp is on monotonic()'s clock (see WindowsHook.button), so
        # the once-per-run check in _normalise_time must not second-guess it.
        self._use_os_time = True
        # But one can say an event happened up to two ticks after it did.
        self._stamp_error_s = WINDOWS_STAMP_ERROR_S
        self._thread_id = kernel32.GetCurrentThreadId()

        # Windows silently removes a low-level hook whose callback runs past
        # LowLevelHooksTimeout, and says nothing: the thread lives on and no
        # click is filtered again. So a slow callback re-installs the hook, so
        # does a timer every 15 s in case a stall went unseen, and so do
        # unlocking the session, reconnecting to it and waking from sleep,
        # when hooks are most often lost.
        WM_REARM = 0x8000 + 0x44  # WM_APP + n
        REARM_SLOW, REARM_SESSION = 1, 2
        WM_TIMER = 0x0113
        SLOW_CALLBACK_S = 0.2
        thread_id = kernel32.GetCurrentThreadId()
        rearm_interval_ms = WINDOWS_REARM_INTERVAL_MS
        # Arrival times come from the precise clock, moved onto monotonic()'s.
        clock_offset = monotonic() - perf_counter()

        @HOOKPROC
        def callback(code: int, message: int, data: int) -> int:
            arrival = perf_counter()
            timings = self._callback_timings
            watched = watch[0]
            try:
                return decide(code, message, data, arrival)
            except Exception:  # noqa: BLE001 - never drop input on a bug
                _log_ignored("the mouse hook")
                return call_next(None, code, message, data)
            finally:
                elapsed = perf_counter() - arrival
                if elapsed > SLOW_CALLBACK_S:
                    user32.PostThreadMessageW(thread_id, WM_REARM, REARM_SLOW, 0)
                if timings is not None:
                    timings.append((int(message), watched, elapsed))

        def decide(code: int, message: int, data: int, arrival: float) -> int:
            if code >= 0:
                if message == WM_MOUSEMOVE:
                    # The hot path: every move comes here, and is only looked
                    # at while it matters (see WindowsHook.set_watch).
                    if watch[0]:
                        info = ctypes.cast(data, PMSLLHOOKSTRUCT).contents
                        if hook_logic.motion(
                            info.pt.x, info.pt.y, info.flags, info.dwExtraInfo,
                            info.time, arrival + clock_offset, get_tick_count(),
                        ):
                            return 1
                else:
                    entry = BUTTONS.get(int(message))
                    if entry is not None:
                        info = ctypes.cast(data, PMSLLHOOKSTRUCT).contents
                        if hook_logic.button(
                            entry[0], entry[1], info.pt.x, info.pt.y, info.flags, info.time,
                            info.dwExtraInfo, arrival + clock_offset, get_tick_count(),
                        ):
                            return 1
            return call_next(None, code, message, data)

        hook = user32.SetWindowsHookExW(WH_MOUSE_LL, callback, None, 0)
        if not hook:
            sender.close()
            self._startup_error = ctypes.WinError(ctypes.get_last_error())
            self._ready.set()
            return
        try:
            window = SessionWindow(api, lambda: user32.PostThreadMessageW(thread_id, WM_REARM, REARM_SESSION, 0))
        except Exception:  # noqa: BLE001 - the periodic re-arm still covers it
            _log_ignored("creating the session window")
            window = None
        self._hook_window = window.hwnd if window is not None else None
        self._started = True
        self._ready.set()
        timer = user32.SetTimer(None, 0, rearm_interval_ms, None)
        message = wintypes.MSG()
        try:
            while not self._stop_event.is_set():
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result in (0, -1) or message.message == WM_QUIT:
                    break
                if message.message in (WM_REARM, WM_TIMER) and not message.hWnd:
                    fresh = user32.SetWindowsHookExW(WH_MOUSE_LL, callback, None, 0)
                    if fresh:
                        user32.UnhookWindowsHookEx(hook)
                        hook = fresh
                        self.hook_rearms += 1
                        if message.message == WM_REARM:
                            log.info(
                                "Re-installed the mouse hook: %s",
                                "it ran slowly" if message.wParam == REARM_SLOW
                                else "the session was unlocked or reconnected, or the computer woke",
                            )
                    continue
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        finally:
            if timer:
                user32.KillTimer(None, timer)
            if window is not None:
                window.close()
            self._hook_window = None
            user32.UnhookWindowsHookEx(hook)
            with self._motion_tap_lock:
                self._set_motion_tap = lambda _wanted: None
                watch[0] = False
            # What stop() handed over (held releases, queued events) goes out
            # now, with no hook of ours left for SendInput to wait on. A timer
            # that fires later sends directly.
            sender.close()

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
        MOTION = frozenset((Quartz.kCGEventMouseMoved, *DRAGGED))
        DISABLED = (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput)

        def inject(button: Button, pressed: Optional[bool], template: object, seq: int) -> bool:
            # Re-post the kept copy of a suppressed event, marked so this app's
            # tap lets it through and knows which re-send it is. It keeps its
            # own timestamp and its own location, so apps see the click when
            # and where it happened: a release that moved would land off the
            # button that was clicked.
            if template is None:
                return False
            kind = INJECTED_MARK if pressed is not None else MOTION_MARK_FOR[button]
            Quartz.CGEventSetIntegerValueField(template, Quartz.kCGEventSourceUserData, make_mark(kind, seq))
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
                _log_ignored("reading the pointer before a re-sent release")
                return None

        def restore_pointer(pointer: object, released_at: object) -> None:
            # A release posted where the button came up takes the pointer
            # there. The hand may have carried the pointer on since: far, after
            # a drag let go while moving, or a little, after a click whose
            # small motion passed while its release was held. So put it back.
            # The move is not tracked: the tap lets it through by its mark,
            # and nothing waits behind it.
            try:
                if math.hypot(pointer.x - released_at.x, pointer.y - released_at.y) <= RESTORE_MIN_PX:
                    return
                move = Quartz.CGEventCreateMouseEvent(
                    None, Quartz.kCGEventMouseMoved, pointer, Quartz.kCGMouseButtonLeft
                )
                Quartz.CGEventSetIntegerValueField(move, Quartz.kCGEventSourceUserData, RESTORE_MARK)
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, move)
            except Exception:  # noqa: BLE001 - the release itself already went out
                _log_ignored("putting the pointer back after a re-sent release")

        self._inject = inject
        click_counts = ClickCountRepair()
        # The first read of the double-click interval is the slowest (it asks
        # the preferences daemon); do it now, not inside the tap callback,
        # where a slow call gets the tap disabled.
        click_counts.interval()
        # Why the hook ends on its own, once it decides to: the run loop then
        # stops and the filter fails open (see below).
        PERMISSION_GONE, KEPT_DISABLED = "permission gone", "macOS kept disabling the tap"
        ending: list[str] = []

        def end_hook(reason: str) -> None:
            ending.append(reason)
            Quartz.CFRunLoopStop(self._run_loop)

        def permission_still_ok() -> bool:
            try:
                return bool(self._permission_ok())
            except Exception:  # noqa: BLE001 - unsure: re-arm, as before the check existed
                _log_ignored("the permission check")
                return True

        def rearm_main() -> None:
            # macOS disables a tap that takes too long, on some user input, or
            # once the app has lost its permission. Re-arm it instead of dying
            # silently, unless the permission is gone (a filtering tap left on
            # a dead grant can stall input system-wide) or it keeps happening.
            if not permission_still_ok():
                end_hook(PERMISSION_GONE)
                return
            if self._tap_disabled():
                end_hook(KEPT_DISABLED)
                return
            self.tap_resets += 1
            Quartz.CGEventTapEnable(tap, True)
            # Whatever this app posted while the tap was off went past it and
            # never comes back: stop waiting for it, and send what waits
            # behind it, now that the tap sees it come back.
            self._give_up_all()

        def callback(proxy: object, event_type: int, event: object, refcon: object) -> object:
            # An exception here would make PyObjC return nothing, which drops
            # the event. Whatever goes wrong, the event goes through untouched.
            try:
                # The hot path: every pointer move comes here. While nothing
                # is pending it goes straight back after this one check.
                if event_type in MOTION and not self._motion_wanted:
                    return event
                return decide(event_type, event)
            except Exception:  # noqa: BLE001
                _log_ignored("the event tap")
                return event

        def decide(event_type: int, event: object) -> object:
            if event_type in DISABLED:
                rearm_main()
                return event
            if event_type in MOTION:
                return decide_motion(event)

            entry = BUTTONS.get(event_type)
            if entry is None:
                return event
            mark = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGEventSourceUserData)
            if mark_kind(mark) == INJECTED_MARK:
                self._injected_passed(entry[0], mark_seq(mark))
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

        def decide_motion(event: object) -> object:
            # Motion while a release is held or re-sent events are on their
            # way (see _update_motion_tap).
            mark = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGEventSourceUserData)
            kind = mark_kind(mark)
            if kind == RESTORE_MARK:
                return event  # puts the pointer back after a re-sent release
            if kind in INJECTED_MOTION_MARKS:
                self._injected_passed(INJECTED_MOTION_MARKS[kind], mark_seq(mark))
                return event  # re-sent by this app; already decided
            location = Quartz.CGEventGetLocation(event)
            # Only the hardware's own stream comes in the order it happened.
            # Another app's motion is stamped when it was posted and can
            # overtake hardware events still on their way, a comeback press
            # among them, so its time settles nothing.
            source = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGEventSourceStateID)
            hardware = filter_injected or source == Quartz.kCGEventSourceStateHIDSystemState
            timestamp = to_seconds(Quartz.CGEventGetTimestamp(event)) if hardware else None
            copy = Quartz.CGEventCreateCopy(event)
            return event if self._motion(copy, (location.x, location.y), timestamp) else None

        # One tap sees clicks and pointer motion alike. WindowServer delivers
        # one tap's events to it strictly in order, and holds each until the
        # callback answers, so a move can never reach apps ahead of a release
        # decided before it. A second tap for motion, switched on only while
        # a release is held, let the first moves after a click overtake the
        # held release: switching a tap on takes effect tens of ms later, and
        # two taps' ports are not serviced in any set order.
        mask = 0
        for event_type in (*BUTTONS, *MOTION):
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
            from . import DISPLAY_NAME, permissions

            self._startup_error = HookError(
                f"macOS refused the event tap. Allow {DISPLAY_NAME} in System Settings \u203a "
                f"Privacy & Security \u203a {permissions.pane_name()}, then try again."
            )
            self._ready.set()
            return

        def close_tap() -> None:
            # Out of the event stream: from here on clicks pass untouched.
            Quartz.CGEventTapEnable(tap, False)

        self._tap = tap
        source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        self._run_loop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(self._run_loop, source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(tap, True)
        self._started = True
        self._ready.set()
        failed_open = None
        try:
            while not self._stop_event.is_set() and not ending:
                # A bounded run keeps the stop flag responsive even when the
                # run loop is woken for reasons of its own.
                Quartz.CFRunLoopRunInMode(Quartz.kCFRunLoopDefaultMode, 0.25, False)
            if ending and not self._stop_event.is_set():
                # Fail open. The tap goes first: nothing answers it once the
                # run loop has stopped, so left enabled it would stall every
                # event, the releases about to be re-sent included. Then what
                # is held back goes out, and every later click passes untouched.
                close_tap()
                self._let_everything_go()
                failed_open = ending[0]
        finally:
            close_tap()
            # Disabling a tap is not enough: macOS keeps it registered, for
            # the life of the process, until its port is invalidated.
            Quartz.CFRunLoopRemoveSource(self._run_loop, source, Quartz.kCFRunLoopCommonModes)
            Quartz.CFMachPortInvalidate(tap)
            self._tap = None
        if failed_open is not None:
            self._end_reason = failed_open
        if failed_open == PERMISSION_GONE:
            # Not an error to report: the app asks for the permission again.
            try:
                self._on_permission_lost()
            except Exception:  # noqa: BLE001 - the hook has ended either way
                _log_ignored("the permission-lost callback")
        elif failed_open == KEPT_DISABLED:
            raise HookError(TAP_DISABLED_MESSAGE)


class WindowsTemplate(NamedTuple):
    """What the Windows hook keeps of a press or release it may re-send: the
    tick it was stamped with (re-sent with it, so apps see when it happened),
    where it happened, and whether it may be re-sent there (see
    WindowsHook.button)."""

    tick: int
    x: int
    y: int
    relocate: bool = True


def windows_event_time(arrival: float, tick_now: int, event_tick: int) -> float:
    """When a Windows input event happened, in seconds on `arrival`'s clock.

    `arrival` is when the hook saw the event, on a precise clock; `event_tick`
    the GetTickCount value Windows stamped it with, and `tick_now` the same
    clock read in the hook. Ticks come in 15.6 ms steps, so a stamp up to one
    tick old says nothing; anything older is how late the hook ran (its thread
    was busy), and is taken off the arrival time. The difference is taken
    modulo 2**32, so the tick count wrapping every 49.7 days changes nothing,
    and no state is kept between events, so neither does a long idle.
    """
    late_ms = (int(tick_now) - int(event_tick)) & 0xFFFFFFFF
    if late_ms >= 0x80000000:
        late_ms = 0  # stamped after the hook's own reading: not late at all
    late_s = max(0, late_ms - TICK_MS) / 1000.0
    return arrival - min(late_s, CLOCK_TOLERANCE_S)


def normalized_absolute(x: int, y: int, left: int, top: int, width: int, height: int) -> tuple[int, int]:
    """SendInput's absolute coordinates (0-65535 across the virtual desktop)
    that put the pointer on pixel (x, y) exactly.

    Windows maps a coordinate back to the pixel floor(value * width / 65536),
    so this takes the smallest value that maps to the pixel: the ceiling of
    pixel * 65536 / width, never 0 (an absolute 0 stopped working in Windows
    10 1709). Positions are first kept on the desktop, whose top-left corner
    (left, top) can be negative with monitors left of or above the main one.
    """
    width, height = max(1, int(width)), max(1, int(height))
    x = min(max(int(x), left), left + width - 1)
    y = min(max(int(y), top), top + height - 1)
    return max(1, -(-(x - left) * 65536 // width)), max(1, -(-(y - top) * 65536 // height))


class WindowsHook:
    """What the Windows hook decides, apart from its ctypes plumbing (see
    _run_windows), so it can be tested against a stand-in for Windows.

    Windows has no location on a button event: an event goes wherever the
    pointer is when Windows processes it. So a release held while the hand
    moves on would land off the click, and apps would see the pointer leave
    with the button still down and start a drag. The hook therefore watches
    motion while a release is held in place: the first move that leaves the
    spot is held back, the release goes out where the pointer still is, and
    the move follows it. A release that can't be delivered that way (the
    hand moved on before the timer delivered it, as when a drag ends while
    moving) is re-sent in one batch with motion that takes the pointer to
    where the button came up and back again; while the pointer is still
    inside the drag rectangle there, it goes out where the pointer is.

    A move held back leaves the pointer where it was, so Windows works out
    the next move from that stale spot. While motion is watched, `basis` is
    where the last move that went through left the pointer (this app's own
    included), and `virtual` where the hand has really taken it: the target
    of the last move held back. Each real move keeps its own step, already
    shaped by pointer acceleration, on top of `virtual`. Moves held back are
    re-sent as the exact pixel they were headed for.
    """

    LLMHF_INJECTED = 0x00000001

    def __init__(
        self,
        owner: "GlobalClickFilter",
        api: "WindowsApi",
        accepts_injection: Callable[[int, int], bool],
        filter_injected: bool = False,
        send: Optional[Callable[[list], object]] = None,
    ) -> None:
        self._owner = owner
        self._api = api
        self._accepts_injection = accepts_injection
        self._filter_injected = filter_injected
        # Sends a batch of (input, button it counts for or None, its number),
        # in order. The hook runs with an InputSender (see there); by
        # default, at once.
        self._send = send or (lambda batch: send_batch(api, batch, owner._resend_lost))
        # Whether motion is being watched: read on every move, without a lock.
        self.watch = [False]
        self.basis = (0, 0)
        self.virtual = (0, 0)
        # False when Windows couldn't say where the pointer was as watching
        # began (another desktop had the input); the next real move sets both.
        self.known = False

    # -- watching motion ----------------------------------------------------
    def set_watch(self, wanted: bool) -> None:
        """The filter's motion switch (GlobalClickFilter._set_motion_tap),
        called with its _motion_tap_lock held. As watching begins, the
        pointer is where Windows says it is."""
        if wanted and not self.watch[0]:
            here = self._api.cursor_pos()
            self.known = here is not None
            if here is not None:
                self.basis = self.virtual = here
        self.watch[0] = wanted

    def _watch_now(self) -> Optional[tuple[int, int]]:
        """Watch motion before anything is sent: a real move processed while
        SendInput runs (other hooks make it interleave) must queue behind
        what is being sent, not overtake it. Returns where the pointer will
        be once everything sent before has arrived, if known."""
        with self._owner._motion_tap_lock:
            self.set_watch(True)
            return self.virtual if self.known else None

    def _where(self, x: int, y: int) -> tuple[int, int]:
        """Where an event at (x, y) really happened: while motion is held
        back the pointer lags behind the hand by the moves held."""
        if not self.watch[0] or not self.known:
            return (x, y)
        return (self.virtual[0] + x - self.basis[0], self.virtual[1] + y - self.basis[1])

    # -- events the hook sees -------------------------------------------------
    def button(
        self,
        button: Button,
        pressed: bool,
        x: int,
        y: int,
        flags: int,
        tick: int,
        extra: int,
        arrival: float,
        tick_now: int,
    ) -> bool:
        """A press or release at (x, y), stamped `tick` by Windows, seen by
        the hook at `arrival` (on monotonic()'s clock) while GetTickCount
        read `tick_now`. Returns True when the hook must drop it."""
        owner = self._owner
        if mark_kind(extra) == INJECTED_MARK:
            owner._injected_passed(button, mark_seq(extra))  # re-sent by this app; already decided
            return False
        if flags & self.LLMHF_INJECTED and not self._filter_injected:
            return False  # synthetic input from another app; leave it alone
        # The tick is far coarser than the 12 ms and 30 ms rules and the
        # thresholds near them, so the event is timed by its arrival on the
        # precise clock, less any lateness the tick shows (a stall here).
        stamp = windows_event_time(arrival, tick_now, tick)
        # A release is only held back if it can be re-sent to the window that
        # will receive it.
        allow_hold = pressed or self._accepts_injection(x, y)
        # A release is re-sent where it happened, and motion that leaves the
        # spot delivers it, unless the pointer's place means nothing: pen and
        # touch (absolute positions), a hidden pointer (a game's mouse-look)
        # or a remote session.
        relocate = (extra & PEN_SIGNATURE_MASK) != PEN_SIGNATURE and (
            pressed or self._api.relocation_allowed()
        )
        at = self._where(x, y)
        event = owner._handle(
            button,
            pressed,
            stamp,
            WindowsTemplate(int(tick), at[0], at[1], relocate),
            allow_hold=allow_hold,
            location=at if relocate else None,
        )
        return not event.accepted

    def motion(
        self,
        x: int,
        y: int,
        flags: int,
        extra: int,
        tick: Optional[int] = None,
        arrival: Optional[float] = None,
        tick_now: Optional[int] = None,
    ) -> bool:
        """A move to (x, y), seen while motion is watched, timed as `button`
        times a press when `tick`, `arrival` and `tick_now` are given.
        Returns True when the hook must drop it (it was queued to be re-sent)."""
        kind = mark_kind(extra)
        if kind in INJECTED_MOTION_MARKS:
            self.basis = (x, y)
            self._owner._injected_passed(INJECTED_MOTION_MARKS[kind], mark_seq(extra))
            return False
        if kind == TELEPORT_MARK:
            self.basis = (x, y)
            return False
        if (flags & self.LLMHF_INJECTED and not self._filter_injected) or (
            extra & PEN_SIGNATURE_MASK
        ) == PEN_SIGNATURE:
            # Another program's motion, or pen and touch: left alone. It puts
            # the pointer somewhere new, so the hand's next step is taken from
            # there; and with none of the hand's moves held back, that is
            # where the pointer stays, so a release still held goes back to
            # where it happened rather than landing here.
            if not self.known or self.virtual == self.basis:
                self.virtual = (x, y)
            self.basis = (x, y)
            self.known = True
            return False
        if not self.known:
            self.basis = self.virtual = (x, y)
            self.known = True
        target = (self.virtual[0] + x - self.basis[0], self.virtual[1] + y - self.basis[1])
        stamp = None
        if tick is not None and arrival is not None and tick_now is not None:
            stamp = windows_event_time(arrival, tick_now, tick)
        if not self._owner._motion(target, target, stamp):
            self.virtual = target
            return True
        self.basis = self.virtual = (x, y)
        return False

    # -- sending ----------------------------------------------------------------
    def inject(self, button: Button, pressed: Optional[bool], template: object, seq: int) -> bool:
        """The filter's re-send (GlobalClickFilter._inject) of re-send `seq`
        of `button`. `template` is a WindowsTemplate, or for motion the (x, y)
        it was headed for. What to send is decided here, at once; the sending
        itself is handed on."""
        api = self._api
        with api.physical_pixels():
            batch = self._batch(api, button, pressed, template, seq)
        self._send(batch)
        return True  # anything that doesn't go in is settled by send_batch

    def _batch(self, api: "WindowsApi", button: Button, pressed: Optional[bool], template: object, seq: int) -> list:
        if pressed is None:
            x, y = template
            self._watch_now()
            return [(api.move_input(x, y, make_mark(MOTION_MARK_FOR[button], seq)), button, seq)]
        tick, x, y, relocate = template
        flags = api.button_flags(button, pressed)
        here = self._watch_now()
        moved = here is not None and here != (x, y)
        # Message times must not run backwards: once later motion has
        # reached apps, a release goes out stamped now.
        when = 0 if moved and not pressed else int(tick) & 0xFFFFFFFF
        if (
            pressed
            or not moved
            or not relocate
            or not api.relocation_allowed()
            or self._owner._is_near((x, y), here)
        ):
            # A press goes where the pointer is: it is re-sent right after the
            # events that came before it, motion included. So does a release
            # where the pointer still is, or still inside the drag rectangle
            # around where the button came up (apps take that as the same
            # spot, so the pointer needn't jump), one made where the
            # pointer's place means nothing (see button), and one whose way
            # back isn't known.
            return [(api.button_input(flags, when, make_mark(INJECTED_MARK, seq)), button, seq)]
        # The pointer has moved on: take it to where the button came up,
        # release it there and take it back, in one batch so the three arrive
        # in order. The release is waited for until the way back has come
        # back too: that carries the release's number, and the release
        # itself none. So a real move that comes between the release and the
        # way back queues behind it rather than being undone by it.
        return [
            (api.move_input(x, y, TELEPORT_MARK), None, 0),
            (api.button_input(flags, when, INJECTED_MARK), None, 0),
            (api.move_input(here[0], here[1], make_mark(MOTION_MARK_FOR[button], seq)), button, seq),
        ]


def send_batch(api, batch: list, lost: Callable[[Button, int], object]) -> int:
    """SendInput one batch of (input, button it counts for or None, its
    number). An input that didn't go in never comes back through the hook,
    so it is settled with `lost` rather than waited for. Returns how many
    went in."""
    try:
        with api.physical_pixels():
            sent = api.send([item for item, _button, _seq in batch])
    except Exception:  # noqa: BLE001 - never break the event stream
        _log_ignored("sending input")
        sent = 0
    for _item, button, seq in batch[sent:]:
        if button is not None:
            lost(button, seq)
    return sent


class InputSender:
    """Sends the Windows hook's input, in order, from a thread of its own.

    While a low-level hook is installed, SendInput waits until every hook has
    seen the input. On the hook's own thread, inside its callback, that means
    the callback runs again within SendInput, and a send from there waits for
    ever: all input on the machine stops. So the hook decides what to send
    and hands it here, and timers and stop() do the same. Batches arrive in
    the order the filter decided them (see GlobalClickFilter._send_outbox),
    and one queue keeps them in it.
    """

    def __init__(self, api: "WindowsApi", lost: Callable[[Button, int], object]) -> None:
        import queue

        self._api = api
        self._lost = lost
        self._queue: "queue.SimpleQueue" = queue.SimpleQueue()
        self._closed = False
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="dcf-send", daemon=True)
        self._thread.start()

    def submit(self, batch: list) -> None:
        with self._lock:
            if not self._closed:
                self._queue.put(batch)
                return
        send_batch(self._api, batch, self._lost)  # the hook is gone: nothing to wait on

    def close(self, timeout: float = 2.0) -> None:
        """Send what is queued, then stop; later batches are sent directly."""
        with self._lock:
            self._closed = True
            self._queue.put(None)
        self._thread.join(timeout)

    def _run(self) -> None:
        while True:
            batch = self._queue.get()
            if batch is None:
                return
            send_batch(self._api, batch, self._lost)


class WindowsApi:
    """The Win32 calls the Windows hook makes, set up once per hook thread.

    Coordinates are physical pixels, as in the hook's event records: callers
    that may run on another thread (re-sends from timers, or stop() from the
    UI) wrap their work in `physical_pixels()`.
    """

    SM_SWAPBUTTON = 23
    SM_CXDRAG, SM_CYDRAG = 68, 69
    SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79
    SM_REMOTESESSION = 0x1000
    CURSOR_SHOWING = 0x1
    MONITOR_DEFAULTTONEAREST = 2
    #: DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2, as Qt makes the app.
    PER_MONITOR_AWARE_V2 = -4
    INPUT_MOUSE = 0
    MOVE_ABSOLUTE = 0x0001 | 0x8000 | 0x4000  # MOVE | ABSOLUTE | VIRTUALDESK
    BUTTON_FLAGS = {
        (Button.LEFT, True): 0x0002, (Button.LEFT, False): 0x0004,
        (Button.RIGHT, True): 0x0008, (Button.RIGHT, False): 0x0010,
        (Button.MIDDLE, True): 0x0020, (Button.MIDDLE, False): 0x0040,
    }

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._wintypes = wintypes
        self.user32 = user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

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

        class CURSORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("hCursor", wintypes.HANDLE),
                ("ptScreenPos", wintypes.POINT),
            ]

        self.MSLLHOOKSTRUCT, self.MOUSEINPUT, self.INPUT, self.CURSORINFO = (
            MSLLHOOKSTRUCT, MOUSEINPUT, INPUT, CURSORINFO
        )
        user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
        user32.SendInput.restype = wintypes.UINT
        user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        user32.GetSystemMetrics.restype = ctypes.c_int
        user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        user32.GetCursorPos.restype = wintypes.BOOL
        user32.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
        user32.GetCursorInfo.restype = wintypes.BOOL
        user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        user32.MonitorFromPoint.restype = wintypes.HANDLE
        # Windows 10 1607 and later; without them the system-wide values do.
        self._metric_for_dpi = getattr(user32, "GetSystemMetricsForDpi", None)
        if self._metric_for_dpi is not None:
            self._metric_for_dpi.argtypes = [ctypes.c_int, wintypes.UINT]
            self._metric_for_dpi.restype = ctypes.c_int
        self._set_dpi_context = getattr(user32, "SetThreadDpiAwarenessContext", None)
        if self._set_dpi_context is not None:
            self._set_dpi_context.argtypes = [ctypes.c_void_p]
            self._set_dpi_context.restype = ctypes.c_void_p
        try:
            self._dpi_for_monitor = ctypes.WinDLL("shcore").GetDpiForMonitor
            self._dpi_for_monitor.argtypes = [
                wintypes.HANDLE, ctypes.c_int, ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT)
            ]
            self._dpi_for_monitor.restype = ctypes.c_long
        except (OSError, AttributeError):
            self._dpi_for_monitor = None

    # -- DPI ---------------------------------------------------------------
    def use_physical_pixels(self) -> None:
        """Make this thread per-monitor aware for good (the hook's own thread)."""
        if self._set_dpi_context is not None:
            self._set_dpi_context(self.PER_MONITOR_AWARE_V2)

    @contextmanager
    def physical_pixels(self):
        """Work in physical pixels on this thread for a moment. The app's
        threads already do (Qt makes the process per-monitor aware), but a
        process without Qt, such as the tests, would get scaled coordinates."""
        previous = None
        if self._set_dpi_context is not None:
            previous = self._set_dpi_context(self.PER_MONITOR_AWARE_V2)
        try:
            yield
        finally:
            if previous:
                self._set_dpi_context(previous)

    # -- the pointer -------------------------------------------------------
    def metric(self, index: int) -> int:
        return int(self.user32.GetSystemMetrics(index))

    def cursor_pos(self) -> Optional[tuple[int, int]]:
        point = self._wintypes.POINT()
        with self.physical_pixels():
            if not self.user32.GetCursorPos(self._ctypes.byref(point)):
                return None  # another desktop has the input (a UAC prompt, the lock screen)
        return (point.x, point.y)

    def cursor_showing(self) -> bool:
        info = self.CURSORINFO()
        info.cbSize = self._ctypes.sizeof(info)
        if not self.user32.GetCursorInfo(self._ctypes.byref(info)):
            return True  # unsure: treat it as an ordinary pointer
        return bool(info.flags & self.CURSOR_SHOWING)

    def remote_session(self) -> bool:
        return bool(self.metric(self.SM_REMOTESESSION))

    def relocation_allowed(self) -> bool:
        """Whether a release may be held for the pointer leaving its spot,
        and re-sent where it happened. Not while the pointer is hidden (a
        game's mouse-look, where absolute moves would show up as camera
        jumps) or in a remote session (the client positions the pointer)."""
        return self.cursor_showing() and not self.remote_session()

    def drag_size(self, point: tuple) -> tuple[int, int]:
        """How far the pointer may move from `point` before Windows calls it a
        drag, in pixels, at the DPI of the monitor there (SM_CXDRAG/SM_CYDRAG)."""
        dpi = self.dpi_at(point)
        if dpi and self._metric_for_dpi is not None:
            return (
                max(1, self._metric_for_dpi(self.SM_CXDRAG, dpi)),
                max(1, self._metric_for_dpi(self.SM_CYDRAG, dpi)),
            )
        return max(1, self.metric(self.SM_CXDRAG)), max(1, self.metric(self.SM_CYDRAG))

    def dpi_at(self, point: tuple) -> int:
        if self._dpi_for_monitor is None:
            return 0
        wintypes = self._wintypes
        monitor = self.user32.MonitorFromPoint(
            wintypes.POINT(int(point[0]), int(point[1])), self.MONITOR_DEFAULTTONEAREST
        )
        x_dpi, y_dpi = wintypes.UINT(), wintypes.UINT()
        if not monitor or self._dpi_for_monitor(monitor, 0, self._ctypes.byref(x_dpi), self._ctypes.byref(y_dpi)):
            return 0  # MDT_EFFECTIVE_DPI = 0; any non-zero HRESULT is a failure
        return int(x_dpi.value)

    def within_drag_rect(self, point: Optional[tuple], location: Optional[tuple]) -> bool:
        """Whether `location` is still on the spot `point`: inside the
        rectangle Windows itself uses to tell a click from a drag (DragDetect
        starts a drag once the pointer leaves it). Runs on the hook thread,
        or inside physical_pixels() (a re-send deciding where to go)."""
        if point is None or location is None:
            return False
        try:
            width, height = self.drag_size(point)
        except Exception:  # noqa: BLE001 - fall back to the shared test
            _log_ignored("reading the drag rectangle")
            return _near(point, location)
        return abs(location[0] - point[0]) < width and abs(location[1] - point[1]) < height

    # -- sending input -------------------------------------------------------
    def buttons_swapped(self) -> bool:
        return bool(self.metric(self.SM_SWAPBUTTON))

    def button_flags(self, button: Button, pressed: bool) -> int:
        """SendInput's flags for this app's button. Those name the physical
        button, which Windows then swaps for a left-handed user, while the
        hook sees the swapped (logical) one."""
        if button is not Button.MIDDLE and self.buttons_swapped():
            button = Button.RIGHT if button is Button.LEFT else Button.LEFT
        return self.BUTTON_FLAGS[(button, pressed)]

    def button_input(self, flags: int, when: int, mark: int) -> object:
        return self.INPUT(self.INPUT_MOUSE, self.MOUSEINPUT(0, 0, 0, flags, when, mark))

    def virtual_screen(self) -> tuple[int, int, int, int]:
        """The rectangle around every monitor: left, top, width, height. Read
        on every send, so a display change needs no notification."""
        return (
            self.metric(self.SM_XVIRTUALSCREEN),
            self.metric(self.SM_YVIRTUALSCREEN),
            self.metric(self.SM_CXVIRTUALSCREEN),
            self.metric(self.SM_CYVIRTUALSCREEN),
        )

    def move_input(self, x: int, y: int, mark: int) -> object:
        """Pointer motion to pixel (x, y) exactly. Absolute, so pointer
        acceleration (already in the hand's motion) isn't applied twice."""
        dx, dy = normalized_absolute(x, y, *self.virtual_screen())
        return self.INPUT(self.INPUT_MOUSE, self.MOUSEINPUT(dx, dy, 0, self.MOVE_ABSOLUTE, 0, mark))

    def send(self, inputs: list) -> int:
        """SendInput, in one batch; returns how many went in."""
        batch = (self.INPUT * len(inputs))(*inputs)
        return int(self.user32.SendInput(len(inputs), batch, self._ctypes.sizeof(self.INPUT)))


class SessionWindow:
    """A hidden window on the hook's thread that hears the session being
    unlocked or reconnected and the computer waking, the moments a low-level
    hook is most often lost, and calls `on_change` (on that thread).

    A top-level window, not a message-only one: those miss broadcasts such as
    WM_POWERBROADCAST. Nothing here is essential; the periodic re-arm covers
    whatever fails to register.
    """

    WM_CLOSE = 0x0010
    WM_WTSSESSION_CHANGE = 0x02B1
    WM_POWERBROADCAST = 0x0218
    #: WTS_CONSOLE_CONNECT, WTS_REMOTE_CONNECT, WTS_SESSION_UNLOCK.
    SESSION_EVENTS = (0x1, 0x3, 0x8)
    #: PBT_APMRESUMESUSPEND, PBT_APMRESUMEAUTOMATIC.
    RESUME_EVENTS = (0x7, 0x12)
    _names = itertools.count(1)

    def __init__(self, api: WindowsApi, on_change: Callable[[], object]) -> None:
        import ctypes
        from ctypes import wintypes

        from . import DISPLAY_NAME

        self.hwnd = None
        self._api = api
        self._wts = None
        self._power = None
        user32, kernel32 = api.user32, api.kernel32
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = LRESULT
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        user32.RegisterClassW.restype = wintypes.ATOM
        user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
        ]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        session_events, resume_events = self.SESSION_EVENTS, self.RESUME_EVENTS
        session_change, power, close = self.WM_WTSSESSION_CHANGE, self.WM_POWERBROADCAST, self.WM_CLOSE

        @WNDPROC
        def window_proc(hwnd, message, wparam, lparam):
            if message == close:
                # Restart Manager, and taskkill without /F, close every
                # top-level window of the app. The default would destroy this
                # one, and session notices would stop without a word: it goes
                # only with the hook (see close()).
                return 0
            try:
                if (message == session_change and wparam in session_events) or (
                    message == power and wparam in resume_events
                ):
                    on_change()
            except Exception:  # noqa: BLE001 - never break the hook's thread
                _log_ignored("the session window")
            return user32.DefWindowProcW(hwnd, message, wparam, lparam)

        self._proc = window_proc  # keep it alive as long as the window
        self._instance = kernel32.GetModuleHandleW(None)
        self._class_name = f"DoubleClickFixerHook-{os.getpid()}-{next(self._names)}"
        window_class = WNDCLASSW()
        window_class.lpfnWndProc = window_proc
        window_class.hInstance = self._instance
        window_class.lpszClassName = self._class_name
        if not user32.RegisterClassW(ctypes.byref(window_class)):
            log.warning("Couldn't register the hook's session window (error %d)", ctypes.get_last_error())
            self._class_name = None
            return
        WS_POPUP = 0x80000000
        self.hwnd = user32.CreateWindowExW(
            0, self._class_name, DISPLAY_NAME, WS_POPUP, 0, 0, 0, 0, None, None, self._instance, None
        )
        if not self.hwnd:
            log.warning("Couldn't create the hook's session window (error %d)", ctypes.get_last_error())
            self.close()
            return
        try:
            wts = ctypes.WinDLL("wtsapi32", use_last_error=True)
            wts.WTSRegisterSessionNotification.argtypes = [wintypes.HWND, wintypes.DWORD]
            wts.WTSRegisterSessionNotification.restype = wintypes.BOOL
            wts.WTSUnRegisterSessionNotification.argtypes = [wintypes.HWND]
            if wts.WTSRegisterSessionNotification(self.hwnd, 0):  # NOTIFY_FOR_THIS_SESSION
                self._wts = wts
            else:
                log.info("Session notifications are unavailable (error %d)", ctypes.get_last_error())
        except (OSError, AttributeError):
            _log_ignored("registering for session notifications")
        try:
            # Windows 8 and later: wake-up notices sent to this window itself,
            # however Windows decides to broadcast them.
            register = user32.RegisterSuspendResumeNotification
            register.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            register.restype = ctypes.c_void_p
            user32.UnregisterSuspendResumeNotification.argtypes = [ctypes.c_void_p]
            self._power = register(self.hwnd, 0) or None  # DEVICE_NOTIFY_WINDOW_HANDLE
        except AttributeError:
            pass

    def close(self) -> None:
        user32 = self._api.user32
        try:
            if self._power is not None:
                user32.UnregisterSuspendResumeNotification(self._power)
                self._power = None
            if self._wts is not None and self.hwnd:
                self._wts.WTSUnRegisterSessionNotification(self.hwnd)
                self._wts = None
            if self.hwnd:
                user32.DestroyWindow(self.hwnd)
                self.hwnd = None
            if self._class_name is not None:
                user32.UnregisterClassW(self._class_name, self._instance)
                self._class_name = None
        except Exception:  # noqa: BLE001 - the hook is ending either way
            _log_ignored("closing the session window")


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
            _log_ignored("checking whether a window accepts re-sent input")
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
        self._interval = DEFAULT_DOUBLE_CLICK_S  # until a read succeeds
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
                _log_ignored("reading the double-click interval")
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
        # or one re-sent behind events that came before it (deferred), or
        # behind the held release it delivers once its button is no longer
        # filtered (flush_held). Not a suppressed bounce, nor the contact
        # coming back mid-drag.
        pending = self._pending.pop(button, None)
        if result.pressed and not result.is_bounce and pending is not None:
            self._delivered[button] = pending


def _double_click_interval() -> float:
    """The user's double-click speed (System Settings > Mouse), in seconds.

    Read from the preference itself, as NSEvent.doubleClickInterval does,
    rather than through AppKit: this runs on the hook thread, and AppKit is
    the main thread's. An unset preference means macOS's default.
    """
    from CoreFoundation import CFPreferencesCopyAppValue, kCFPreferencesAnyApplication

    value = CFPreferencesCopyAppValue(DOUBLE_CLICK_KEY, kCFPreferencesAnyApplication)
    if value is None:
        return DEFAULT_DOUBLE_CLICK_S
    seconds = float(value)
    return seconds if math.isfinite(seconds) and seconds > 0 else DEFAULT_DOUBLE_CLICK_S


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
        _log_ignored("reading the mach timebase")
        status = -1
    if status != 0 or not info.numer or not info.denom:
        return 1, 1  # ticks read as nanoseconds; posted events still time right
    return info.numer, info.denom
