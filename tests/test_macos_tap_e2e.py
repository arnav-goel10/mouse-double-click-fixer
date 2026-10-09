"""End to end on a real Mac: posted clicks through the real event tap.

The real GlobalClickFilter installs its tap, a test posts mouse events where
the hardware's would enter (kCGHIDEventTap), and a listen-only tap at the end
of the session's stream records what applications receive: which events, in
what order, where, and with what click count.

It moves the pointer and clicks wherever it is, so it runs only where nobody
is using the Mac: with DCF_E2E=1 on a CI runner (GITHUB_ACTIONS=true), whose
processes may create event taps and post events. DCF_E2E_ALLOW_LOCAL=1 lets
it run elsewhere, on a machine set aside for it.

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

RUN = (
    os.environ.get("DCF_E2E") == "1"
    and platform.system() == "Darwin"
    and (os.environ.get("GITHUB_ACTIONS") == "true" or os.environ.get("DCF_E2E_ALLOW_LOCAL") == "1")
)

THRESHOLD_MS = 46
#: Where the scenarios click. Inside the smallest runner display (1024 x 768).
P = (300.0, 300.0)
#: Marks the probe event posted before the scenarios, to show posting works.
PROBE_MARK = 0x0E2E


class Seen(NamedTuple):
    """One event as applications received it."""

    kind: str  # down, up, move or drag
    x: int
    y: int
    clicks: int  # kCGMouseEventClickState
    mark: int  # kCGEventSourceUserData


class Step(NamedTuple):
    at_ms: float
    kind: str  # down, up, move or drag
    point: tuple
    clicks: int = 1


def _mach_clock() -> Callable[[], int]:
    """Nanoseconds since boot, the clock CGEvent timestamps of posted events use."""

    class TimebaseInfo(ctypes.Structure):
        _fields_ = [("numer", ctypes.c_uint32), ("denom", ctypes.c_uint32)]

    system = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    system.mach_absolute_time.restype = ctypes.c_uint64
    info = TimebaseInfo()
    system.mach_timebase_info(ctypes.byref(info))
    return lambda: system.mach_absolute_time() * info.numer // info.denom


@unittest.skipUnless(RUN, "posts real input: runs with DCF_E2E=1 on a CI Mac only")
class MacTapEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import Quartz

        from app.core import Button
        from app.platform import GlobalClickFilter

        cls.Quartz = Quartz
        cls.kinds = {
            Quartz.kCGEventLeftMouseDown: "down",
            Quartz.kCGEventLeftMouseUp: "up",
            Quartz.kCGEventMouseMoved: "move",
            Quartz.kCGEventLeftMouseDragged: "drag",
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

        cls.filter = GlobalClickFilter(THRESHOLD_MS, [Button.LEFT], on_event=cls.log.append)
        cls.filter.start()
        cls.addClassCleanup(cls.filter.stop)
        time.sleep(0.3)

    # -- the observer: what applications receive ----------------------------------
    @classmethod
    def _start_observer(cls) -> None:
        Quartz = cls.Quartz

        def observe(_proxy, kind, event, _refcon):
            name = cls.kinds.get(kind)
            if name is not None:
                location = Quartz.CGEventGetLocation(event)
                cls.seen.append(
                    Seen(
                        name,
                        round(location.x),
                        round(location.y),
                        Quartz.CGEventGetIntegerValueField(event, Quartz.kCGMouseEventClickState),
                        Quartz.CGEventGetIntegerValueField(event, Quartz.kCGEventSourceUserData),
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
            event = Quartz.CGEventCreateMouseEvent(
                None, self.types[step.kind], step.point, Quartz.kCGMouseButtonLeft
            )
            if step.kind in ("down", "up"):
                Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventClickState, step.clicks)
            Quartz.CGEventSetTimestamp(event, due)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        self.late_ms = (self.now_ns() - due) / 1e6
        self._wait_until_quiet()
        return list(self.seen)

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


if __name__ == "__main__":
    unittest.main()
