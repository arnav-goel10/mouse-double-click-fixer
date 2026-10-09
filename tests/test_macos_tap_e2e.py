"""End to end on a real Mac: posted clicks through the real event tap.

The real GlobalClickFilter installs its tap, a test posts mouse events where
the hardware's would enter (kCGHIDEventTap), and a listen-only tap at the end
of the session's stream records what applications receive: which events, in
what order, where, and with what click count.

It moves the pointer and clicks wherever it is, so it runs only where nobody
is using the Mac: with DCF_E2E=1 on a CI runner (GITHUB_ACTIONS=true), whose
processes may create event taps and post events. DCF_E2E_ALLOW_LOCAL=1 lets
it run elsewhere, on a machine set aside for it. Without DCF_E2E=1 it is
skipped; with it, anything that would skip it fails instead, so a job that
asked for it can't pass having run nothing.

Events are posted on a schedule, each stamped with its planned time. A
background process on these runners oversleeps short waits by tens of
milliseconds (the system coalesces its timers), which would turn a bounce
into a slow re-press, so the schedule is kept by spinning on the clock.
"""

try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

import contextlib
import ctypes
import os
import platform
import sys
import threading
import time
import unittest
from typing import Callable, NamedTuple
from unittest import mock

#: Asked for: a skip is then a failure.
E2E = os.environ.get("DCF_E2E") == "1"
#: Where it may post input: a Mac set aside for it.
ALLOWED_HERE = platform.system() == "Darwin" and (
    os.environ.get("GITHUB_ACTIONS") == "true" or os.environ.get("DCF_E2E_ALLOW_LOCAL") == "1"
)
RUN = E2E and ALLOWED_HERE
SKIP_REASON = "posts real input: runs with DCF_E2E=1 on a CI Mac only"

THRESHOLD_MS = 46
#: Where the scenarios click. Inside the smallest runner display (1024 x 768).
P = (300.0, 300.0)
#: Marks the probe event posted before the scenarios, to show posting works.
PROBE_MARK = 0x0E2E


class Seen(NamedTuple):
    """One event as applications received it."""

    kind: str  # down, up, move, drag, odown, oup (other buttons) or scroll
    x: int
    y: int
    clicks: int  # kCGMouseEventClickState
    mark: int  # kCGEventSourceUserData
    number: int = 0  # kCGMouseEventButtonNumber
    subtype: int = 0  # kCGMouseEventSubtype
    delta: int = 0  # a scroll's kCGScrollWheelEventDeltaAxis1
    continuous: int = 0  # a scroll's kCGScrollWheelEventIsContinuous


class Step(NamedTuple):
    at_ms: float
    kind: str  # down, up, move, drag, odown, oup or scroll
    point: tuple
    clicks: int = 1
    number: int = 0  # odown, oup: the button number (3 back, 4 forward)
    subtype: int = 0  # down, up: kCGMouseEventSubtype (3 is a touch)
    delta: int = 0  # scroll: lines, vertical
    continuous: int = 0  # scroll: 1 for a trackpad's


def _mach_clock() -> Callable[[], int]:
    """Nanoseconds since boot, the clock CGEvent timestamps of posted events use."""

    class TimebaseInfo(ctypes.Structure):
        _fields_ = [("numer", ctypes.c_uint32), ("denom", ctypes.c_uint32)]

    system = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    system.mach_absolute_time.restype = ctypes.c_uint64
    info = TimebaseInfo()
    system.mach_timebase_info(ctypes.byref(info))
    return lambda: system.mach_absolute_time() * info.numer // info.denom


class MacTapEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not RUN:
            if E2E:
                raise AssertionError(
                    "DCF_E2E=1 asks for the event tap end-to-end test, but it can't run here: it needs macOS and "
                    "GITHUB_ACTIONS=true (or DCF_E2E_ALLOW_LOCAL=1 on a Mac set aside for it)"
                )
            raise unittest.SkipTest(SKIP_REASON)
        import Quartz

        from app.core import Button
        from app.platform import FilterConfig, GlobalClickFilter

        cls.Quartz = Quartz
        cls.FilterConfig = FilterConfig
        cls.kinds = {
            Quartz.kCGEventLeftMouseDown: "down",
            Quartz.kCGEventLeftMouseUp: "up",
            Quartz.kCGEventMouseMoved: "move",
            Quartz.kCGEventLeftMouseDragged: "drag",
            Quartz.kCGEventOtherMouseDown: "odown",
            Quartz.kCGEventOtherMouseUp: "oup",
            Quartz.kCGEventScrollWheel: "scroll",
        }
        cls.types = {name: kind for kind, name in cls.kinds.items()}
        cls.now_ns = staticmethod(_mach_clock())
        # The filter, the observer and the poster share the GIL: hand it over
        # often, so a tap callback never waits long behind the spinning poster.
        cls.switch_interval = sys.getswitchinterval()
        sys.setswitchinterval(0.0005)
        # Posted clicks are filtered only when this is set (the app leaves
        # other programs' clicks alone).
        cls.environment = mock.patch.dict(os.environ, {"DCF_FILTER_INJECTED": "1"})
        cls.environment.start()
        cls.seen: list[Seen] = []
        cls.log: list = []
        cls.stopping = threading.Event()
        cls._start_observer()
        cls.addClassCleanup(cls._stop_observer)
        cls.addClassCleanup(cls.environment.stop)
        cls.addClassCleanup(sys.setswitchinterval, cls.switch_interval)

        cls.original_pointer = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
        cls.addClassCleanup(Quartz.CGWarpMouseCursorPosition, cls.original_pointer)
        Quartz.CGWarpMouseCursorPosition(P)
        cls._require_posting_reaches_taps()

        cls.config = FilterConfig.uniform(THRESHOLD_MS, [Button.LEFT, Button.BACK, Button.FORWARD])
        cls.filter = GlobalClickFilter(cls.config, on_event=cls.log.append)
        cls.filter.start()
        cls.addClassCleanup(cls.filter.stop)
        time.sleep(0.3)

    def skipTest(self, reason: str) -> None:
        if E2E:
            self.fail(f"DCF_E2E=1 asked for this scenario, which would have been skipped: {reason}")
        super().skipTest(reason)

    # -- the observer: what applications receive ----------------------------------
    @classmethod
    def _start_observer(cls) -> None:
        Quartz = cls.Quartz

        def observe(_proxy, kind, event, _refcon):
            name = cls.kinds.get(kind)
            if name is not None:
                location = Quartz.CGEventGetLocation(event)
                field = Quartz.CGEventGetIntegerValueField
                cls.seen.append(
                    Seen(
                        name,
                        round(location.x),
                        round(location.y),
                        field(event, Quartz.kCGMouseEventClickState),
                        field(event, Quartz.kCGEventSourceUserData),
                        field(event, Quartz.kCGMouseEventButtonNumber) if name in ("odown", "oup") else 0,
                        field(event, Quartz.kCGMouseEventSubtype),
                        field(event, Quartz.kCGScrollWheelEventDeltaAxis1) if name == "scroll" else 0,
                        field(event, Quartz.kCGScrollWheelEventIsContinuous) if name == "scroll" else 0,
                    )
                )
            return event

        mask = 0
        for kind in cls.kinds:
            mask |= Quartz.CGEventMaskBit(kind)
        tap = Quartz.CGEventTapCreate(
            Quartz.kCGAnnotatedSessionEventTap,
            Quartz.kCGTailAppendEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            mask,
            observe,
            None,
        )
        if tap is None:
            raise AssertionError("macOS refused the observer's event tap: this runner can't run the test")
        ready = threading.Event()

        def run() -> None:
            loop = Quartz.CFRunLoopGetCurrent()
            source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
            Quartz.CFRunLoopAddSource(loop, source, Quartz.kCFRunLoopCommonModes)
            Quartz.CGEventTapEnable(tap, True)
            ready.set()
            while not cls.stopping.is_set():
                Quartz.CFRunLoopRunInMode(Quartz.kCFRunLoopDefaultMode, 0.05, False)
            Quartz.CGEventTapEnable(tap, False)
            Quartz.CFRunLoopRemoveSource(loop, source, Quartz.kCFRunLoopCommonModes)
            Quartz.CFMachPortInvalidate(tap)

        cls.observer_thread = threading.Thread(target=run, name="e2e-observer", daemon=True)
        cls.observer_thread.start()
        ready.wait(5)
        cls._observer_callback = observe  # keep the callback alive with the tap

    @classmethod
    def _stop_observer(cls) -> None:
        cls.stopping.set()
        cls.observer_thread.join(timeout=2)

    @classmethod
    def _require_posting_reaches_taps(cls) -> None:
        """Without this, every scenario would fail for a reason that has
        nothing to do with the filter."""
        Quartz = cls.Quartz
        cls.seen.clear()
        event = Quartz.CGEventCreateMouseEvent(
            None, Quartz.kCGEventMouseMoved, (P[0] + 1, P[1]), Quartz.kCGMouseButtonLeft
        )
        Quartz.CGEventSetIntegerValueField(event, Quartz.kCGEventSourceUserData, PROBE_MARK)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not any(item.mark == PROBE_MARK for item in cls.seen):
            time.sleep(0.02)
        if not any(item.mark == PROBE_MARK for item in cls.seen):
            raise AssertionError("an event posted at kCGHIDEventTap never reached the session: can't test here")
        Quartz.CGWarpMouseCursorPosition(P)

    # -- posting --------------------------------------------------------------------
    def play(self, steps: list[Step]) -> list[Seen]:
        """Post `steps` on schedule, each stamped with its planned time, and
        return what applications received once things are quiet again."""
        Quartz = self.Quartz
        time.sleep(0.3)  # well clear of the last scenario
        self.seen.clear()
        self.log.clear()
        start = self.now_ns() + 20_000_000
        for step in steps:
            due = start + int(step.at_ms * 1_000_000)
            while self.now_ns() < due:
                time.sleep(0)  # lets the taps' threads take the GIL
            event = self.make(step)
            Quartz.CGEventSetTimestamp(event, due)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        self.late_ms = (self.now_ns() - due) / 1e6
        self._wait_until_quiet()
        return list(self.seen)

    def make(self, step: Step):
        Quartz = self.Quartz
        if step.kind == "scroll":
            event = Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitLine, 1, step.delta)
            Quartz.CGEventSetIntegerValueField(event, Quartz.kCGScrollWheelEventIsContinuous, step.continuous)
            Quartz.CGEventSetLocation(event, step.point)
            return event
        event = Quartz.CGEventCreateMouseEvent(
            None, self.types[step.kind], step.point, step.number or Quartz.kCGMouseButtonLeft
        )
        if step.kind in ("odown", "oup"):
            Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventButtonNumber, step.number)
        if step.kind in ("down", "up", "odown", "oup"):
            Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventClickState, step.clicks)
        if step.subtype:
            Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventSubtype, step.subtype)
        return event

    def configure(self, **changes) -> None:
        """Hand the running filter a new configuration, as the app does."""
        from dataclasses import replace

        self.filter.update(replace(self.filter.config, **changes))
        self.addCleanup(self.filter.update, self.config)

    def wheel_in_tap(self, wanted: bool, limit_s: float = 3.0) -> None:
        """Wait for the filter to re-create its tap with the scroll wheel in
        its mask, or out of it."""
        deadline = time.monotonic() + limit_s
        while time.monotonic() < deadline:
            tap = self.filter._tap
            if tap is not None and self.filter._wheel_on == wanted and self._tap_has_wheel(tap) == wanted:
                time.sleep(0.1)
                return
            time.sleep(0.02)
        self.fail(f"the tap didn't {'take' if wanted else 'leave out'} the scroll wheel")

    def _tap_has_wheel(self, tap) -> bool:
        Quartz = self.Quartz
        error, taps, count = Quartz.CGGetEventTapList(64, None, None)
        own = [entry for entry in (taps or [])[:count] if entry.tappingProcess == os.getpid() and entry.enabled]
        bit = Quartz.CGEventMaskBit(Quartz.kCGEventScrollWheel)
        return any(entry.eventsOfInterest & bit and entry.options == Quartz.kCGEventTapOptionDefault for entry in own)

    def _wait_until_quiet(self, quiet_s: float = 0.4, limit_s: float = 5.0) -> None:
        deadline = time.monotonic() + limit_s
        count, since = -1, time.monotonic()
        while time.monotonic() < deadline:
            if len(self.seen) != count:
                count, since = len(self.seen), time.monotonic()
            elif time.monotonic() - since >= quiet_s:
                return
            time.sleep(0.05)

    @contextlib.contextmanager
    def explained(self):
        """Adds what applications saw and what the filter decided to a failure."""
        try:
            yield
        except AssertionError as error:
            lines = [str(error), f"(the last event went out {self.late_ms:.1f} ms after its time)", "applications saw:"]
            lines += [f"    {item}" for item in self.seen]
            lines.append("the filter decided:")
            for event in self.log:
                verdict = (
                    "pass" if event.accepted else "defer" if event.deferred else "hold" if event.held
                    else "cancel" if event.cancels_held else "flush" if event.flush_held else "block"
                )
                gap = "" if event.gap_ms is None else f" (gap {event.gap_ms:.1f} ms)"
                lines.append(f"    {'down' if event.pressed else 'up'}: {verdict}{gap}")
            raise AssertionError("\n".join(lines)) from None

    @staticmethod
    def buttons(seen: list[Seen]) -> list[Seen]:
        return [item for item in seen if item.kind in ("down", "up")]

    @staticmethod
    def at(dx: float, dy: float = 0.0) -> tuple:
        return (P[0] + dx, P[1] + dy)

    # -- scenarios ------------------------------------------------------------------
    def test_a_bounce_is_removed(self) -> None:
        # The contact opens and closes again 8 ms after the release: one click.
        seen = self.play([
            Step(0, "down", P), Step(70, "up", P),
            Step(78, "down", P, clicks=2), Step(130, "up", P, clicks=2),
        ])
        with self.explained():
            buttons = self.buttons(seen)
            self.assertEqual([item.kind for item in buttons], ["down", "up"])
            self.assertEqual([item.clicks for item in buttons], [1, 1])
            self.assertEqual([(item.x, item.y) for item in buttons], [(300, 300)] * 2)

    def test_a_drag_survives_a_dropout_while_moving(self) -> None:
        steps = [Step(0, "down", P)]
        steps += [Step(10 * i, "drag", self.at(10 * i)) for i in range(1, 8)]
        steps += [Step(78, "up", self.at(70)), Step(88, "down", self.at(70))]  # drops out, comes back
        steps += [Step(10 * i + 18, "drag", self.at(10 * i)) for i in range(8, 15)]
        steps += [Step(260, "up", self.at(140))]
        seen = self.play(steps)
        with self.explained():
            buttons = self.buttons(seen)
            self.assertEqual([item.kind for item in buttons], ["down", "up"], "the drag was broken in two")
            self.assertEqual((buttons[0].x, buttons[1].x), (300, 440))
            self.assertEqual(seen[-1].kind, "up", "motion came after the drag ended")
            drags = [item.x for item in seen if item.kind == "drag"]
            self.assertEqual(drags, sorted(drags), "the drag went backwards")
            self.assertNotIn("move", [item.kind for item in seen], "part of the drag arrived as plain motion")

    def test_a_drag_survives_a_dropout_before_it_moves(self) -> None:
        steps = [Step(0, "down", P), Step(60, "up", P), Step(72, "down", P)]
        steps += [Step(72 + 10 * i, "drag", self.at(10 * i)) for i in range(1, 8)]
        steps += [Step(300, "up", self.at(70))]
        seen = self.play(steps)
        with self.explained():
            kinds = [item.kind for item in seen]
            buttons = self.buttons(seen)
            self.assertEqual([item.kind for item in buttons], ["down", "up"], "the drag was broken in two")
            self.assertEqual(buttons[1].x, 370)
            self.assertNotIn("up", kinds[: kinds.index("drag")], "the dropout reached applications")

    def test_a_click_then_a_move_delivers_the_release_first_and_in_place(self) -> None:
        steps = [Step(0, "down", P), Step(70, "up", P)]
        steps += [Step(70 + 8 * i, "move", self.at(8 * i, 4 * i)) for i in range(1, 6)]
        seen = self.play(steps)
        with self.explained():
            kinds = [item.kind for item in seen]
            self.assertEqual(kinds[:2], ["down", "up"], "a move reached applications before the release")
            self.assertEqual([(item.x, item.y) for item in seen[:2]], [(300, 300)] * 2, "the release moved")
            self.assertNotIn("drag", kinds, "the click became a drag")
            self.assertEqual([item for item in kinds if item in ("down", "up")], ["down", "up"])
            self.assertGreaterEqual(kinds.count("move"), 5, "pointer motion was lost")
            last = [item for item in seen if item.kind == "move"][-1]
            self.assertEqual((last.x, last.y), (340, 320), "the pointer didn't end where the hand left it")

    def test_a_double_click_keeps_its_click_counts(self) -> None:
        seen = self.play([
            Step(0, "down", P), Step(60, "up", P),
            Step(180, "down", P, clicks=2), Step(240, "up", P, clicks=2),
        ])
        with self.explained():
            buttons = self.buttons(seen)
            self.assertEqual([item.kind for item in buttons], ["down", "up", "down", "up"])
            self.assertEqual([item.clicks for item in buttons], [1, 1, 2, 2])

    def test_fast_single_clicks_all_arrive(self) -> None:
        steps = []
        for i in range(3):
            point = self.at(40 * i)
            steps += [Step(150 * i, "down", point), Step(150 * i + 60, "up", point)]
        seen = self.play(steps)
        with self.explained():
            buttons = self.buttons(seen)
            self.assertEqual([item.kind for item in buttons], ["down", "up"] * 3)
            self.assertEqual([item.x for item in buttons], [300, 300, 340, 340, 380, 380])

    def test_a_click_a_move_and_a_click_elsewhere(self) -> None:
        steps = [Step(0, "down", P), Step(70, "up", P)]
        steps += [Step(70 + 8 * i, "move", self.at(8 * i)) for i in range(1, 6)]
        steps += [Step(190, "down", self.at(40)), Step(260, "up", self.at(40))]
        seen = self.play(steps)
        with self.explained():
            buttons = self.buttons(seen)
            self.assertEqual([item.kind for item in buttons], ["down", "up", "down", "up"])
            self.assertEqual([item.x for item in buttons], [300, 300, 340, 340])
            self.assertEqual([item.clicks for item in buttons], [1, 1, 1, 1])
            first_up, second_down = seen.index(buttons[1]), seen.index(buttons[2])
            between = {item.kind for item in seen[first_up + 1 : second_down]}
            self.assertEqual(between, {"move"})


    # -- 1.0: side buttons, the wheel, touch -----------------------------------------
    def test_a_side_button_bounce_is_dropped_and_its_release_never_waits(self) -> None:
        seen = self.play([
            Step(0, "odown", P, number=3), Step(70, "oup", P, number=3),
            Step(78, "odown", P, number=3, clicks=2), Step(130, "oup", P, number=3, clicks=2),
            Step(400, "odown", P, number=4), Step(470, "oup", P, number=4),
        ])
        with self.explained():
            others = [item for item in seen if item.kind in ("odown", "oup")]
            self.assertEqual([(item.kind, item.number) for item in others],
                             [("odown", 3), ("oup", 3), ("odown", 4), ("oup", 4)])
            self.assertEqual([item.mark for item in others], [0] * 4, "a release was held and re-sent")
            self.assertEqual([event.button.value for event in self.log if event.is_bounce], ["back"])

    def test_the_wheel_fix_drops_a_stray_reversing_notch(self) -> None:
        self.configure(wheel_fix=True, wheel_window_ms=50)
        self.wheel_in_tap(True)
        dropped = self.filter.wheel_dropped
        seen = self.play([
            Step(0, "scroll", P, delta=-1), Step(20, "scroll", P, delta=-1),
            Step(30, "scroll", P, delta=1),                                  # the stray notch
            Step(40, "scroll", P, delta=-1),
            Step(50, "scroll", P, delta=3, continuous=1),                    # a trackpad's: never touched
            Step(200, "scroll", P, delta=1),                                 # the hand rolls it back
        ])
        with self.explained():
            scrolls = [(item.delta, item.continuous) for item in seen if item.kind == "scroll"]
            self.assertEqual(scrolls, [(-1, 0), (-1, 0), (-1, 0), (3, 1), (1, 0)])
            self.assertEqual(self.filter.wheel_dropped - dropped, 1)

    def test_with_the_wheel_fix_off_the_wheel_is_not_in_the_tap(self) -> None:
        self.configure(wheel_fix=True)
        self.wheel_in_tap(True)
        self.configure(wheel_fix=False)
        self.wheel_in_tap(False)
        seen = self.play([Step(0, "scroll", P, delta=-1), Step(10, "scroll", P, delta=1)])
        with self.explained():
            self.assertEqual([item.delta for item in seen if item.kind == "scroll"], [-1, 1])
        # And clicks are still filtered by the tap that replaced the others.
        seen = self.play([Step(0, "down", P), Step(70, "up", P), Step(78, "down", P, clicks=2), Step(130, "up", P)])
        with self.explained():
            self.assertEqual([item.kind for item in self.buttons(seen)], ["down", "up"])

    def test_a_touch_click_passes_untouched(self) -> None:
        # A trackpad's double-tap: 1 ms between release and press, marked as
        # a touch (kCGMouseEventSubtype 3). Never filtered, never re-sent.
        seen = self.play([
            Step(0, "down", P, subtype=3), Step(40, "up", P, subtype=3),
            Step(41, "down", P, clicks=2, subtype=3), Step(80, "up", P, clicks=2, subtype=3),
        ])
        with self.explained():
            buttons = self.buttons(seen)
            self.assertEqual([item.kind for item in buttons], ["down", "up", "down", "up"])
            self.assertEqual([item.mark for item in buttons], [0] * 4)
            self.assertEqual([item.subtype for item in buttons], [3] * 4)
            self.assertEqual([item.clicks for item in buttons], [1, 1, 2, 2])
            self.assertEqual(self.log, [], "the filter judged none of them")

    def test_a_touch_press_waits_behind_the_mouses_held_release(self) -> None:
        seen = self.play([
            Step(0, "down", P), Step(70, "up", P),                           # the mouse's click: up held
            Step(80, "down", P, subtype=3), Step(120, "up", P, subtype=3),  # a tap on the trackpad
        ])
        with self.explained():
            buttons = self.buttons(seen)
            self.assertEqual([item.kind for item in buttons], ["down", "up", "down", "up"], "apps saw down, down")
            self.assertEqual([item.subtype for item in buttons], [0, 0, 3, 3])
            self.assertEqual([event.pressed for event in self.log], [True, False], "only the mouse's was judged")

    def test_the_tap_stays_cheap_with_the_wheel_in_it(self) -> None:
        """Measures (and reports) the tap callback's cost for scroll events
        with the wheel fix on, and for clicks and motion beside them."""
        import statistics

        self.configure(wheel_fix=True)
        self.wheel_in_tap(True)
        timings: list = []
        self.filter._callback_timings = timings
        self.addCleanup(setattr, self.filter, "_callback_timings", None)
        steps = [Step(i * 2.0, "scroll", P, delta=-1 if (i // 7) % 2 else 1) for i in range(600)]
        steps += [Step(1300 + i * 100.0, kind, P) for i, kind in enumerate(["down", "up"] * 10)]
        steps += [Step(3400 + i * 2.0, "move", (P[0] + i % 5, P[1])) for i in range(300)]
        self.play(steps)
        Quartz = self.Quartz
        groups = {
            "scroll": [t for kind, _w, t in timings if kind == Quartz.kCGEventScrollWheel],
            "button": [t for kind, _w, t in timings if kind in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp)],
            "move": [t for kind, _w, t in timings if kind == Quartz.kCGEventMouseMoved],
        }
        lines = []
        for name, values in groups.items():
            if not values:
                lines.append(f"{name} n=0")
                continue
            ordered = sorted(ms * 1000 for ms in values)
            p99 = ordered[max(0, int(len(ordered) * 0.99) - 1)]
            lines.append(f"{name} n={len(ordered)} median={statistics.median(ordered):.4f} ms "
                         f"p99={p99:.4f} ms max={ordered[-1]:.3f} ms")
        print(f"\n[cost] macOS tap callback: {'; '.join(lines)}", file=sys.stderr, flush=True)
        self.assertGreaterEqual(len(groups["scroll"]), 500, "scroll events didn't reach the tap")
        scroll = sorted(groups["scroll"])
        self.assertLess(scroll[int(len(scroll) * 0.99) - 1] * 1000, 2.0)

    def test_device_lookups_cost_little(self) -> None:
        """Reports what looking a sender up costs on this machine: once per
        device and connection, in the tap callback at worst."""
        from app import devices_mac

        started = time.perf_counter()
        connected = devices_mac.connected_senders()
        listing_ms = (time.perf_counter() - started) * 1000
        costs = []
        for sender in list(connected)[:20]:
            started = time.perf_counter()
            devices_mac.resolve_sender(sender)
            costs.append((time.perf_counter() - started) * 1000)
        names = sorted({f"{device.name} ({device.kind})" for device in connected.values()})
        print(f"\n[cost] macOS device lookup: listing {len(connected)} senders took {listing_ms:.2f} ms; "
              f"one sender {max(costs) if costs else float('nan'):.3f} ms at most; devices {names}",
              file=sys.stderr, flush=True)
        started = time.perf_counter()
        self.assertIsNone(devices_mac.resolve_sender(0x7FFFFFFFFFFF), "no such entry")
        print(f"[cost] an unknown sender: {(time.perf_counter() - started) * 1000:.3f} ms", file=sys.stderr, flush=True)



class RequestedRunTests(unittest.TestCase):
    """DCF_E2E=1 never ends in an all-skipped pass. These never post input:
    they only take the paths where the scenarios don't run."""

    def outcome_of_set_up(self, e2e: bool) -> Exception:
        with mock.patch.object(sys.modules[__name__], "E2E", e2e), \
                mock.patch.object(sys.modules[__name__], "RUN", False):
            try:
                MacTapEndToEndTests.setUpClass()
            except (unittest.SkipTest, AssertionError) as outcome:
                return outcome
        raise AssertionError("setUpClass went ahead where it may not post input")

    def test_asked_for_where_it_cant_run_fails(self) -> None:
        outcome = self.outcome_of_set_up(e2e=True)
        self.assertIsInstance(outcome, AssertionError)
        self.assertNotIsInstance(outcome, unittest.SkipTest)
        self.assertIn("DCF_E2E=1", str(outcome))

    def test_not_asked_for_it_skips(self) -> None:
        self.assertIsInstance(self.outcome_of_set_up(e2e=False), unittest.SkipTest)

    def test_a_scenario_skipped_while_asked_for_fails(self) -> None:
        scenario = MacTapEndToEndTests("test_a_bounce_is_removed")
        with mock.patch.object(sys.modules[__name__], "E2E", True):
            with self.assertRaises(AssertionError):
                scenario.skipTest("no display")
        with mock.patch.object(sys.modules[__name__], "E2E", False):
            with self.assertRaises(unittest.SkipTest):
                scenario.skipTest("no display")


if __name__ == "__main__":
    unittest.main()
