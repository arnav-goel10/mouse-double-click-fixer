"""The Windows hook: its decisions against a stand-in for Windows (run
everywhere), and the real hook with real input (Windows, `DCF_E2E=1`).

The real tests genuinely click and move the pointer, so they only run when
`DCF_E2E=1` is set (CI does). A second low-level hook installed *before* the
filter acts as the observer: Windows calls the most recently installed hook
first, so anything the filter suppresses never reaches it, the same thing an
application would see. Every system setting a test changes is put back.
"""

import faulthandler
import os
import platform
import statistics
import sys
import threading
import time
import unittest
from contextlib import contextmanager
from unittest import mock

from app.core import Button
from app.platform import (
    INJECTED_MARK,
    MOTION_MARK_FOR,
    TELEPORT_MARK,
    GlobalClickFilter,
    InputSender,
    WindowsHook,
    normalized_absolute,
    send_batch,
)

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
MESSAGES = {WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_RBUTTONDOWN, WM_RBUTTONUP, 0x0207, 0x0208}
PEN = 0xFF515701


def _run_e2e() -> bool:
    return platform.system() == "Windows" and os.environ.get("DCF_E2E") == "1"


class NormalizedAbsoluteTests(unittest.TestCase):
    """SendInput's absolute coordinates land on the pixel asked for."""

    def lands_on(self, value: int, origin: int, size: int) -> int:
        # How Windows maps an absolute coordinate back to a pixel.
        return origin + value * size // 65536

    def check(self, left: int, top: int, width: int, height: int) -> None:
        for x in range(left, left + width):
            dx, _ = normalized_absolute(x, top, left, top, width, height)
            self.assertGreaterEqual(dx, 1)
            self.assertLessEqual(dx, 65535)
            self.assertEqual(self.lands_on(dx, left, width), x, f"pixel {x} of {width} from {left}")
        for y in range(top, top + height):
            _, dy = normalized_absolute(left, y, left, top, width, height)
            self.assertEqual(self.lands_on(dy, top, height), y, f"pixel {y} of {height} from {top}")

    def test_every_pixel_of_a_single_screen(self) -> None:
        self.check(0, 0, 1024, 768)

    def test_every_pixel_of_a_wide_desktop_left_of_and_above_the_main_screen(self) -> None:
        self.check(-2560, -1440, 6400, 2880)

    def test_never_zero(self) -> None:
        self.assertEqual(normalized_absolute(0, 0, 0, 0, 1920, 1080), (1, 1))

    def test_positions_off_the_desktop_are_kept_on_it(self) -> None:
        self.assertEqual(normalized_absolute(-50, 5000, 0, 0, 1024, 768), normalized_absolute(0, 767, 0, 0, 1024, 768))


# -- the hook's decisions, against a stand-in for Windows ---------------------------

class FakeTimer:
    """Stands in for threading.Timer, so a test decides when each one fires."""

    created: list = []
    daemon = True

    def __init__(self, _interval, function, args=()):
        self.function, self.args = function, args
        FakeTimer.created.append(self)

    def start(self):
        pass

    def is_alive(self):
        return False

    def cancel(self):
        pass

    def fire(self):
        self.function(*self.args)


class FakeWindows:
    """Windows' input pipeline in miniature, and WindowsApi's stand-in.

    One queue of input, handled in order as Windows' raw input thread does.
    Each event goes to the filter's hook first; what it lets through moves the
    pointer and reaches apps (`seen`), and what it drops leaves the pointer
    where it was. SendInput adds to the end of the queue, as it does while a
    low-level hook is installed. Buttons are swapped for a left-handed user
    the way this hook assumes Windows does: SendInput names the physical
    button, hooks and apps see the logical one.
    """

    INJECTED = 0x1

    def __init__(self, click_filter: GlobalClickFilter, cursor=(200, 200)) -> None:
        self.cursor = cursor
        self.screen = (0, 0, 1024, 768)
        self.drag = (4, 4)
        self.queue: list = []
        self.seen: list = []
        self.hidden = self.remote = self.swapped = False
        self.cursor_known = True
        self.now_ms = 10_000.0
        self.hook = WindowsHook(click_filter, self, accepts_injection=lambda _x, _y: True)
        click_filter._use_os_time = True
        click_filter._inject = self.hook.inject
        click_filter._is_near = self.within_drag_rect
        click_filter._set_motion_tap = self.hook.set_watch

    # -- WindowsApi --------------------------------------------------------
    @contextmanager
    def physical_pixels(self):
        yield

    def cursor_pos(self):
        return self.cursor if self.cursor_known else None

    def relocation_allowed(self) -> bool:
        return not self.hidden and not self.remote

    def within_drag_rect(self, point, location) -> bool:
        if point is None or location is None:
            return False
        return abs(location[0] - point[0]) < self.drag[0] and abs(location[1] - point[1]) < self.drag[1]

    def _swap(self, button: Button) -> Button:
        if self.swapped and button is not Button.MIDDLE:
            return Button.RIGHT if button is Button.LEFT else Button.LEFT
        return button

    def button_flags(self, button: Button, pressed: bool):
        return (self._swap(button), pressed)  # the physical button

    def button_input(self, flags, when):
        return ("button", flags, when, INJECTED_MARK)

    def move_input(self, x, y, mark):
        return ("move", normalized_absolute(x, y, *self.screen), mark)

    def send(self, inputs: list) -> int:
        # Motion is watched before anything goes out, so no real move can
        # overtake what is being sent.
        assert self.hook.watch[0], "sent input while motion wasn't watched"
        self.queue.extend(inputs)
        return len(inputs)

    # -- the hand ----------------------------------------------------------
    def move(self, dx: int, dy: int, extra: int = 0) -> None:
        self.queue.append(("hand-move", dx, dy, extra))

    def press(self, button: Button = Button.LEFT, extra: int = 0) -> None:
        self.queue.append(("hand-button", (button, True), extra))

    def release(self, button: Button = Button.LEFT, extra: int = 0) -> None:
        self.queue.append(("hand-button", (button, False), extra))

    def wait(self, ms: float) -> None:
        self.run()
        self.now_ms += ms

    def run(self) -> None:
        while self.queue:
            self._process(self.queue.pop(0))

    def _process(self, item) -> None:
        kind = item[0]
        if kind == "hand-move":
            _, dx, dy, extra = item
            left, top, width, height = self.screen
            x = min(max(self.cursor[0] + dx, left), left + width - 1)
            y = min(max(self.cursor[1] + dy, top), top + height - 1)
            self._move((x, y), 0, extra)
        elif kind == "move":
            _, (dx, dy), mark = item
            left, top, width, height = self.screen
            self._move((left + dx * width // 65536, top + dy * height // 65536), self.INJECTED, mark)
        elif kind == "hand-button":
            _, (button, pressed), extra = item
            self._button(self._swap(button), pressed, 0, extra, int(self.now_ms))
        else:
            _, (button, pressed), when, mark = item
            self._button(self._swap(button), pressed, self.INJECTED, mark, when or int(self.now_ms))

    def _move(self, point, flags, extra) -> None:
        if self.hook.watch[0] and self.hook.motion(point[0], point[1], flags, extra):
            return  # held back: the pointer stays where it was
        self.cursor = point
        self.seen.append(("move", point, extra))

    def _button(self, button, pressed, flags, extra, tick) -> None:
        dropped = self.hook.button(
            button, pressed, self.cursor[0], self.cursor[1], flags, tick, extra, self.now_ms / 1000, int(self.now_ms)
        )
        if not dropped:
            self.seen.append(("down" if pressed else "up", button, self.cursor, tick))

    # -- what apps saw -----------------------------------------------------
    def buttons(self) -> list:
        return [entry for entry in self.seen if entry[0] != "move"]

    def moves_between(self, first: str, second: str) -> list:
        kinds = [entry[0] for entry in self.seen]
        start, end = kinds.index(first), kinds.index(second)
        return [entry for entry in self.seen[start:end] if entry[0] == "move"]


class WindowsHookLogicTests(unittest.TestCase):
    """The hook's decisions, driven through a stand-in for Windows' input."""

    def setUp(self) -> None:
        FakeTimer.created = []
        patch = mock.patch("app.platform.threading.Timer", FakeTimer)
        patch.start()
        self.addCleanup(patch.stop)
        self.filter = GlobalClickFilter(60, [Button.LEFT])
        self.win = FakeWindows(self.filter)

    def fire_timers(self) -> None:
        for timer in list(FakeTimer.created):
            timer.fire()
        self.win.run()

    def click(self) -> None:
        self.win.press()
        self.win.wait(80)
        self.win.release()
        self.win.wait(5)

    def test_a_click_then_a_quick_move_keeps_the_up_at_the_click(self) -> None:
        self.click()
        self.win.move(220, 130)
        self.win.run()
        self.assertEqual(
            [entry[:3] for entry in self.win.buttons()],
            [("down", Button.LEFT, (200, 200)), ("up", Button.LEFT, (200, 200))],
        )
        self.assertEqual(self.win.moves_between("down", "up"), [], "apps saw the pointer leave a held button")
        self.assertEqual(self.win.cursor, (420, 330))
        self.assertFalse(self.win.hook.watch[0], "motion is watched only while it matters")
        self.fire_timers()
        self.assertEqual(len(self.win.buttons()), 2, "the timer must not send the up again")

    def test_the_up_keeps_its_own_time_when_nothing_passed_it(self) -> None:
        self.win.press()
        self.win.wait(80)
        released_at = int(self.win.now_ms)
        self.win.release()
        self.win.wait(5)
        self.win.move(50, 0)
        self.win.run()
        self.assertEqual(self.win.buttons()[1][3], released_at)

    def test_a_burst_of_moves_is_rebased_exactly(self) -> None:
        for interleaved in (False, True):
            with self.subTest(interleaved=interleaved):
                self.setUp()
                self.click()
                for _ in range(30):
                    self.win.move(7, 4)
                    if interleaved:
                        self.win.wait(1)
                self.win.run()
                self.assertEqual(self.win.buttons()[1][2], (200, 200))
                self.assertEqual(self.win.moves_between("down", "up"), [])
                self.assertEqual(self.win.cursor, (410, 320))

    def test_small_motion_passes_and_the_timer_puts_the_up_back_on_the_click(self) -> None:
        self.click()
        self.win.move(2, 0)                                           # inside the drag rectangle
        self.win.run()
        self.assertEqual(self.win.cursor, (202, 200), "a hand resting on the mouse isn't held back")
        self.fire_timers()
        self.assertEqual(self.win.buttons()[1][:3], ("up", Button.LEFT, (200, 200)))
        self.assertEqual(self.win.buttons()[1][3], int(self.win.now_ms), "stamped now: motion came first")
        self.assertEqual(self.win.cursor, (202, 200))
        self.assertFalse(self.win.hook.watch[0])

    def test_small_motion_then_leaving_the_click(self) -> None:
        self.click()
        self.win.move(2, 0)
        self.win.move(10, 0)
        self.win.run()
        self.assertEqual(self.win.buttons()[1][:3], ("up", Button.LEFT, (200, 200)))
        self.assertEqual(self.win.cursor, (212, 200))

    def drag_and_let_go_while_moving(self) -> None:
        self.win.press()
        self.win.wait(50)
        self.win.move(100, 0)
        self.win.wait(50)
        self.win.release()
        self.win.wait(1)
        self.win.move(200, 0)
        self.win.run()

    def test_a_drag_let_go_while_moving_drops_where_the_button_came_up(self) -> None:
        self.drag_and_let_go_while_moving()
        self.assertEqual(self.win.cursor, (500, 200), "motion after a moving release isn't held")
        self.fire_timers()
        self.assertEqual(self.win.buttons()[1][:3], ("up", Button.LEFT, (300, 200)))
        self.assertEqual(self.win.cursor, (500, 200), "the pointer comes back")
        marks = [entry[2] for entry in self.win.seen if entry[0] == "move"][-2:]
        self.assertEqual(marks, [TELEPORT_MARK, MOTION_MARK_FOR[Button.LEFT]])
        self.assertFalse(self.win.hook.watch[0])

    def test_a_move_inside_the_teleport_is_kept(self) -> None:
        for position in (1, 2):  # after the move there, after the release
            with self.subTest(position=position):
                self.setUp()
                self.drag_and_let_go_while_moving()
                for timer in list(FakeTimer.created):
                    timer.fire()
                self.win.queue.insert(position, ("hand-move", 10, 0, 0))
                self.win.run()
                self.assertEqual(self.win.buttons()[1][:3], ("up", Button.LEFT, (300, 200)))
                self.assertEqual(self.win.cursor, (510, 200))

    def test_a_second_click_while_motion_is_held_lands_where_the_hand_was(self) -> None:
        # Windows' queue is backed up (the hook's thread was busy): moves and
        # a new click all wait behind the first click's release.
        self.click()
        for _ in range(3):
            self.win.move(20, 0)
        self.win.press()
        self.win.release()
        self.win.now_ms += 100
        self.win.run()
        downs = [entry for entry in self.win.buttons() if entry[0] == "down"]
        self.assertEqual(downs[1][2], (260, 200))
        self.fire_timers()
        ups = [entry for entry in self.win.buttons() if entry[0] == "up"]
        self.assertEqual([up[2] for up in ups], [(200, 200), (260, 200)])

    def test_swapped_buttons_come_back_in_pairs(self) -> None:
        self.filter.update(buttons=[Button.LEFT, Button.RIGHT])
        self.win.swapped = True
        self.click()
        self.fire_timers()
        self.assertEqual([entry[:2] for entry in self.win.buttons()], [("down", Button.RIGHT), ("up", Button.RIGHT)])

    def test_a_hidden_pointer_keeps_timer_delivery(self) -> None:
        # A game's mouse-look: no motion is held back, and the release goes
        # out where the pointer is, never moving it.
        self.win.hidden = True
        self.click()
        self.win.move(50, 0)
        self.win.run()
        self.assertEqual([entry[0] for entry in self.win.seen], ["down", "move"])
        self.fire_timers()
        self.assertEqual(self.win.buttons()[1][:3], ("up", Button.LEFT, (250, 200)))
        self.assertEqual([entry[0] for entry in self.win.seen], ["down", "move", "up"])

    def test_pen_and_touch_keep_timer_delivery(self) -> None:
        self.win.press(extra=PEN)
        self.win.wait(80)
        self.win.release(extra=PEN)
        self.win.wait(5)
        self.win.move(50, 0, extra=PEN)
        self.win.run()
        self.assertEqual([entry[0] for entry in self.win.seen], ["down", "move"])
        self.fire_timers()
        self.assertEqual([entry[0] for entry in self.win.seen], ["down", "move", "up"])

    def test_a_remote_session_keeps_timer_delivery(self) -> None:
        self.win.remote = True
        self.drag_and_let_go_while_moving()
        self.fire_timers()
        self.assertEqual(self.win.buttons()[1][:3], ("up", Button.LEFT, (500, 200)))
        self.assertNotIn(TELEPORT_MARK, [entry[2] for entry in self.win.seen if entry[0] == "move"])

    def test_an_unknown_pointer_position_sends_the_release_where_it_is(self) -> None:
        self.drag_and_let_go_while_moving()
        self.win.cursor_known = False
        self.fire_timers()
        self.assertEqual(self.win.buttons()[1][:3], ("up", Button.LEFT, (500, 200)))

    def test_other_programs_motion_is_left_alone(self) -> None:
        self.click()
        self.win.queue.append(("move", normalized_absolute(600, 600, *self.win.screen), 0))
        self.win.run()
        self.assertEqual(self.win.cursor, (600, 600))
        self.assertEqual(len(self.win.buttons()), 1, "still held: the move wasn't the hand's")


class InputSenderTests(unittest.TestCase):
    """The hook never sends input itself: SendInput on its thread re-enters
    its callback, and a send from there never returns."""

    class Api:
        def __init__(self, accept: int = 99) -> None:
            self.accept = accept
            self.sent: list = []
            self.threads: list = []

        @contextmanager
        def physical_pixels(self):
            yield

        def send(self, inputs: list) -> int:
            self.threads.append(threading.current_thread().name)
            taken = inputs[: self.accept]
            self.sent.extend(taken)
            return len(taken)

    def test_batches_go_out_in_order_on_the_senders_thread(self) -> None:
        api = self.Api()
        sender = InputSender(api, lost=lambda _button: None)
        for index in range(50):
            sender.submit([(index, Button.LEFT)])
        sender.close()
        self.assertEqual(api.sent, list(range(50)))
        self.assertEqual(set(api.threads), {"dcf-send"})

    def test_inputs_that_dont_go_in_are_settled_not_waited_for(self) -> None:
        lost = []
        sent = send_batch(self.Api(accept=1), [("there", None), ("up", Button.LEFT), ("back", Button.LEFT)], lost.append)
        self.assertEqual(sent, 1)
        self.assertEqual(lost, [Button.LEFT, Button.LEFT])

    def test_a_failing_send_settles_the_whole_batch(self) -> None:
        api = self.Api()
        api.send = mock.Mock(side_effect=OSError("blocked"))
        lost = []
        with mock.patch("app.platform._logged_sites", set()), self.assertLogs("app.platform", "WARNING"):
            self.assertEqual(send_batch(api, [("up", Button.RIGHT)], lost.append), 0)
        self.assertEqual(lost, [Button.RIGHT])

    def test_after_closing_batches_are_sent_directly(self) -> None:
        api = self.Api()
        sender = InputSender(api, lost=lambda _button: None)
        sender.close()
        sender.submit([("late", Button.LEFT)])
        self.assertEqual(api.sent, ["late"])
        self.assertEqual(api.threads, [threading.current_thread().name])


# -- the real hook ------------------------------------------------------------------

class RealWindows(unittest.TestCase):
    """Real input, observed by a second low-level hook behind the filter."""

    def setUp(self) -> None:
        import ctypes
        from ctypes import wintypes

        from app.platform import WindowsApi

        self.ctypes = ctypes
        self.wintypes = wintypes
        self.api = WindowsApi()
        self.user32 = self.api.user32
        self.user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
        self.user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
        self.user32.SystemParametersInfoW.restype = wintypes.BOOL
        self.user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.user32.SendMessageW.restype = ctypes.c_ssize_t
        self.user32.SwapMouseButton.argtypes = [wintypes.BOOL]
        self.user32.SwapMouseButton.restype = wintypes.BOOL
        os.environ["DCF_FILTER_INJECTED"] = "1"  # the test has no real mouse
        self.addCleanup(os.environ.pop, "DCF_FILTER_INJECTED", None)
        self.observed: list = []
        self._observer = None
        self.filter = None
        # A hook that stops answering would hang the job: show every thread's
        # stack and end the run instead.
        faulthandler.dump_traceback_later(120, exit=True)
        self.addCleanup(faulthandler.cancel_dump_traceback_later)

    def tearDown(self) -> None:
        if self.filter is not None:
            self.filter.stop()
        if self._observer is not None:
            self._stop_observer()

    def report(self, text: str) -> None:
        print(f"\n{text}", file=sys.stderr, flush=True)

    # -- the observer hook -------------------------------------------------
    def start_observer(self) -> None:
        ctypes, wintypes = self.ctypes, self.wintypes
        ready = threading.Event()
        self._observer_thread_id = None
        self._observer_stop = threading.Event()
        LRESULT = ctypes.c_ssize_t
        HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, wintypes.DWORD]
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        # Without argtypes, ctypes rejects the 64-bit LPARAM it is handed.
        user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.CallNextHookEx.restype = LRESULT
        user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), ctypes.c_void_p, wintypes.UINT, wintypes.UINT]
        user32.GetMessageW.restype = ctypes.c_int
        user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self._observer_user32 = user32
        record = ctypes.POINTER(self.api.MSLLHOOKSTRUCT)

        def run() -> None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self._observer_thread_id = kernel32.GetCurrentThreadId()

            @HOOKPROC
            def callback(code: int, message: int, data: int) -> int:
                if code >= 0 and message in MESSAGES:
                    info = ctypes.cast(data, record).contents
                    self.observed.append(
                        (int(message), (info.pt.x, info.pt.y), int(info.dwExtraInfo), int(info.time), time.perf_counter())
                    )
                return user32.CallNextHookEx(None, code, message, data)

            self._observer_callback = callback  # keep it alive
            hook = user32.SetWindowsHookExW(14, callback, None, 0)
            ready.set()
            message = wintypes.MSG()
            while not self._observer_stop.is_set():
                if user32.GetMessageW(ctypes.byref(message), None, 0, 0) <= 0:
                    break
            user32.UnhookWindowsHookEx(hook)

        self._observer = threading.Thread(target=run, daemon=True)
        self._observer.start()
        ready.wait(5)
        time.sleep(0.2)

    def _stop_observer(self) -> None:
        self._observer_stop.set()
        if self._observer_thread_id:
            self._observer_user32.PostThreadMessageW(self._observer_thread_id, 0x0012, 0, 0)
        self._observer.join(timeout=2)
        self._observer = None

    def start_filter(self, threshold: int = 60, buttons=(Button.LEFT,)) -> GlobalClickFilter:
        self.filter = GlobalClickFilter(threshold, list(buttons))
        self.filter.start()
        self.assertTrue(self.filter.running)
        return self.filter

    # -- system settings, always put back ---------------------------------
    def exact_relative_motion(self) -> None:
        """Pointer speed 10 and no acceleration: a relative move of n moves
        the pointer n pixels, so expected positions are exact."""
        ctypes = self.ctypes
        SPI_GETMOUSE, SPI_SETMOUSE, SPI_GETMOUSESPEED, SPI_SETMOUSESPEED = 0x3, 0x4, 0x70, 0x71
        mouse = (ctypes.c_int * 3)()
        speed = ctypes.c_int()
        self.assertTrue(self.user32.SystemParametersInfoW(SPI_GETMOUSE, 0, mouse, 0))
        self.assertTrue(self.user32.SystemParametersInfoW(SPI_GETMOUSESPEED, 0, ctypes.byref(speed), 0))
        saved_mouse, saved_speed = list(mouse), speed.value

        def restore() -> None:
            self.user32.SystemParametersInfoW(SPI_SETMOUSE, 0, (ctypes.c_int * 3)(*saved_mouse), 0)
            self.user32.SystemParametersInfoW(SPI_SETMOUSESPEED, 0, ctypes.c_void_p(saved_speed), 0)

        self.addCleanup(restore)
        self.user32.SystemParametersInfoW(SPI_SETMOUSE, 0, (ctypes.c_int * 3)(0, 0, 0), 0)
        self.user32.SystemParametersInfoW(SPI_SETMOUSESPEED, 0, ctypes.c_void_p(10), 0)

    def pointer_where_it_counts(self) -> None:
        """A hosted runner may have no visible pointer, or be a remote
        session; the hook then keeps timer delivery (tested above). These
        tests are about the pointer's place, so they act as a desktop with a
        mouse would."""
        from app.platform import WindowsApi

        self.report(f"[runner] pointer showing: {self.api.cursor_showing()}, remote: {self.api.remote_session()}")
        if not self.api.relocation_allowed():
            patch = mock.patch.object(WindowsApi, "relocation_allowed", return_value=True)
            patch.start()
            self.addCleanup(patch.stop)

    # -- input -------------------------------------------------------------
    def send(self, *inputs) -> None:
        self.assertEqual(self.api.send(list(inputs)), len(inputs))

    def mouse(self, flags: int, dx: int = 0, dy: int = 0):
        return self.api.INPUT(0, self.api.MOUSEINPUT(dx, dy, 0, flags, 0, 0))

    def button(self, flags: int) -> None:
        self.send(self.mouse(flags))

    def move_by(self, dx: int, dy: int) -> None:
        self.send(self.mouse(0x0001, dx, dy))

    def cursor(self):
        return self.api.cursor_pos()

    def wait_for_cursor(self, where, timeout: float = 1.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and self.cursor() != where:
            time.sleep(0.002)
        return self.cursor()

    def place(self, x: int, y: int) -> None:
        with self.api.physical_pixels():
            self.user32.SetCursorPos(x, y)
        self.assertEqual(self.wait_for_cursor((x, y)), (x, y))
        time.sleep(0.05)
        self.observed.clear()

    def observed_buttons(self) -> list:
        return [entry for entry in self.observed if entry[0] != WM_MOUSEMOVE]

    def first(self, message: int) -> int:
        return next(index for index, entry in enumerate(self.observed) if entry[0] == message)


@unittest.skipUnless(_run_e2e(), "needs Windows and DCF_E2E=1 (it injects real input)")
class WindowsPointerTests(RealWindows):
    def test_absolute_moves_land_on_the_exact_pixel(self) -> None:
        # (b) Every 7th pixel and both edges, along each axis.
        left, top, width, height = self.api.virtual_screen()
        self.report(f"[runner] virtual screen {left},{top} {width}x{height}")
        mid_x, mid_y = left + width // 2, top + height // 2
        xs = sorted(set(range(left, left + width, 7)) | {left, left + width - 1})
        ys = sorted(set(range(top, top + height, 7)) | {top, top + height - 1})
        misses = []
        with self.api.physical_pixels():
            for target in [(x, mid_y) for x in xs] + [(mid_x, y) for y in ys]:
                self.send(self.api.move_input(target[0], target[1], 0))
                landed = self.wait_for_cursor(target, 0.2)
                if landed != target:
                    misses.append((target, landed))
        self.assertEqual(misses, [])


@unittest.skipUnless(_run_e2e(), "needs Windows and DCF_E2E=1 (it injects real input)")
class WindowsHookTests(RealWindows):
    def setUp(self) -> None:
        super().setUp()
        self.start_observer()
        self.start_filter()

    def _click(self, down: bool) -> None:
        self.user32.mouse_event(0x0002 if down else 0x0004, 0, 0, 0, 0)
        time.sleep(0.02)

    def messages(self) -> list:
        return [entry[0] for entry in self.observed_buttons()]

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

        messages = self.messages()
        self.assertEqual(messages.count(WM_LBUTTONDOWN), 4, f"expected the bounce to be removed, saw {messages}")
        self.assertEqual(messages.count(WM_LBUTTONUP), 4, "a suppressed press must take its release with it")
        self.assertEqual(self.filter.filtered_count, 1)

    def test_contact_dropout_mid_drag_keeps_the_drag(self) -> None:
        self.observed.clear()
        self._click(True)           # start a drag
        time.sleep(0.3)
        self._click(False)          # contact drops out for ~10 ms
        time.sleep(0.01)
        self._click(True)           # and comes back
        time.sleep(0.3)
        self._click(False)          # the real lift
        time.sleep(0.4)             # past the filter window: the lift is re-sent

        self.assertEqual(
            self.messages(), [WM_LBUTTONDOWN, WM_LBUTTONUP], f"expected one unbroken drag, saw {self.messages()}"
        )

    def test_two_quick_dropouts_keep_the_drag(self) -> None:
        # A badly worn switch can drop out twice within one filter window.
        self.observed.clear()
        self._click(True)
        time.sleep(0.3)
        self._click(False)          # dropout one
        time.sleep(0.005)
        self._click(True)
        time.sleep(0.005)
        self._click(False)          # dropout two, while the first timer is pending
        time.sleep(0.005)
        self._click(True)
        time.sleep(0.3)
        self._click(False)          # the real lift
        time.sleep(0.4)
        self.assertEqual(
            self.messages(), [WM_LBUTTONDOWN, WM_LBUTTONUP], f"expected one unbroken drag, saw {self.messages()}"
        )

    def test_gaps_are_timed_finer_than_the_tick(self) -> None:
        # Windows stamps input in 15.6 ms ticks; the filter's gaps must follow
        # the real ones to within a few milliseconds.
        events = []
        self.filter._on_event = events.append
        sent_gaps, measured_gaps, tick_gaps = [], [], []
        for _ in range(8):
            self.observed.clear()
            events.clear()
            self.button(0x0002)
            time.sleep(0.1)
            released = time.perf_counter()
            self.button(0x0004)
            time.sleep(0.005)
            pressed = time.perf_counter()
            self.button(0x0002)
            time.sleep(0.1)
            self.button(0x0004)
            time.sleep(0.25)
            sent_gaps.append((pressed - released) * 1000)
            measured_gaps.append([event for event in events if event.pressed][1].gap_ms)
            # The bounce never reaches the observer; the stamps Windows gave
            # the clicks around it show the tick.
            ticks = [entry[3] for entry in self.observed_buttons()]
            tick_gaps.append([later - earlier for earlier, later in zip(ticks, ticks[1:])])
        self.report(f"[timing] sent gaps ms {[round(gap, 1) for gap in sent_gaps]}")
        self.report(f"[timing] filter gaps ms {[round(gap, 1) for gap in measured_gaps]}")
        self.report(f"[timing] info.time steps between the clicks' events ms {tick_gaps}")
        for sent, measured in zip(sent_gaps, measured_gaps):
            self.assertAlmostEqual(measured, sent, delta=4.0)


@unittest.skipUnless(_run_e2e(), "needs Windows and DCF_E2E=1 (it injects real input)")
class WindowsMotionTests(RealWindows):
    """A held release is delivered where it happened."""

    def setUp(self) -> None:
        super().setUp()
        self.exact_relative_motion()
        self.pointer_where_it_counts()
        self.start_observer()

    def click_at_200(self) -> None:
        self.place(200, 200)
        self.button(0x0002)
        time.sleep(0.08)
        self.button(0x0004)

    def assert_up_before_any_move(self, at=(200, 200)) -> None:
        down, up = self.first(WM_LBUTTONDOWN), self.first(WM_LBUTTONUP)
        self.assertEqual(self.observed[down][1], at)
        self.assertEqual(self.observed[up][1], at, f"the up landed off the click: {self.observed}")
        moves = [entry for entry in self.observed[down:up] if entry[0] == WM_MOUSEMOVE]
        self.assertEqual(moves, [], "apps saw the pointer leave while the button was down")

    def test_a_click_then_a_quick_move_keeps_the_up_at_the_click(self) -> None:
        # (c) This failed on 0.5.3: the move passed, the up came 60 ms later.
        self.start_filter()
        self.click_at_200()
        time.sleep(0.005)
        self.move_by(220, 130)
        time.sleep(0.3)
        self.assert_up_before_any_move()
        self.assertEqual(self.cursor(), (420, 330))

    def test_a_burst_of_moves_after_a_click_ends_exactly(self) -> None:
        # (d)
        self.start_filter()
        self.click_at_200()
        for _ in range(30):
            time.sleep(0.001)
            self.move_by(7, 4)
        time.sleep(0.3)
        self.assert_up_before_any_move()
        self.assertEqual(self.cursor(), (410, 320))

    def test_a_drag_let_go_while_moving_drops_where_the_button_came_up(self) -> None:
        # (e)
        self.start_filter()
        self.place(200, 200)
        self.button(0x0002)
        time.sleep(0.05)
        self.move_by(100, 0)
        time.sleep(0.05)
        self.button(0x0004)
        self.move_by(200, 0)
        time.sleep(0.3)
        up = self.first(WM_LBUTTONUP)
        self.assertEqual(self.observed[up][1], (300, 200), f"the drop landed off its spot: {self.observed}")
        self.assertEqual(self.cursor(), (500, 200))
        self.assertIn(TELEPORT_MARK, [entry[2] for entry in self.observed if entry[0] == WM_MOUSEMOVE])

    def test_swapped_buttons_come_back_in_pairs(self) -> None:
        # (f) A left-handed setup: SendInput names the physical button, and a
        # re-sent release must still match its press.
        previous = self.user32.SwapMouseButton(True)
        self.addCleanup(self.user32.SwapMouseButton, previous)
        self.start_filter(buttons=(Button.LEFT, Button.RIGHT))
        self.place(200, 200)
        self.button(0x0002)                                           # the physical left button
        time.sleep(0.08)
        self.button(0x0004)
        time.sleep(0.3)                                               # the timer re-sends the up
        self.button(0x0002)
        time.sleep(0.08)
        self.button(0x0004)
        self.move_by(40, 0)                                           # motion re-sends the up
        time.sleep(0.3)
        messages = [entry[0] for entry in self.observed_buttons()]
        self.report(f"[swapped] {[hex(message) for message in messages]}")
        self.assertEqual(len(messages), 4, messages)
        self.assertEqual(messages[0::2], [messages[0]] * 2)
        self.assertEqual(messages[1::2], [messages[0] + 1] * 2, "each re-sent release matches its press")

    def test_the_hook_stays_cheap(self) -> None:
        # (g) Every move reaches this Python callback; while nothing is held
        # it must cost next to nothing, and little more while it is.
        self.start_filter(threshold=200)
        self.filter._callback_timings = timings = []
        self.place(400, 400)
        for index in range(5000):
            self.move_by(1 if index % 2 else -1, 0)
            if index % 100 == 99:
                time.sleep(0.005)
        time.sleep(0.2)
        watched_moves = rounds = 0
        while watched_moves < 500 and rounds < 40:
            rounds += 1
            self.button(0x0002)
            time.sleep(0.05)
            self.button(0x0004)
            for index in range(60):
                self.move_by(1 if index % 2 else -1, 0)
            time.sleep(0.3)
            watched_moves = sum(1 for message, watched, _ in timings if message == WM_MOUSEMOVE and watched)
        moves = [(watched, seconds * 1000) for message, watched, seconds in timings if message == WM_MOUSEMOVE]
        unwatched = sorted(ms for watched, ms in moves if not watched)
        watched = sorted(ms for watched, ms in moves if watched)

        def p99(values: list) -> float:
            return values[int(len(values) * 0.99) - 1]

        self.report(
            f"[cost] unwatched n={len(unwatched)} median={statistics.median(unwatched):.4f} ms "
            f"p99={p99(unwatched):.4f} ms max={unwatched[-1]:.3f} ms; watched n={len(watched)} "
            f"median={statistics.median(watched):.4f} ms p99={p99(watched):.4f} ms max={watched[-1]:.3f} ms"
        )
        self.assertGreaterEqual(len(unwatched), 5000)
        self.assertGreaterEqual(len(watched), 500)
        self.assertLess(p99(unwatched), 1.0)
        self.assertLess(p99(watched), 1.0)


@unittest.skipUnless(_run_e2e(), "needs Windows and DCF_E2E=1 (it installs real hooks)")
class WindowsHookResilienceTests(RealWindows):
    """The hook is re-installed on a timer and when the session comes back."""

    def wait_for_rearms(self, count: int, timeout: float = 3.0) -> int:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and self.filter.hook_rearms < count:
            time.sleep(0.02)
        return self.filter.hook_rearms

    def test_unlocking_reconnecting_and_waking_re_arm_the_hook(self) -> None:
        self.start_filter()
        window = self.filter._hook_window
        self.assertTrue(window, "the hook's session window was not created")
        WM_WTSSESSION_CHANGE, WM_POWERBROADCAST = 0x02B1, 0x0218
        notices = [(WM_WTSSESSION_CHANGE, 0x8), (WM_WTSSESSION_CHANGE, 0x1), (WM_POWERBROADCAST, 0x12)]
        for count, (message, reason) in enumerate(notices, start=1):
            self.user32.SendMessageW(window, message, reason, 0)
            self.assertGreaterEqual(self.wait_for_rearms(count), count, f"message {message:#x}/{reason:#x}")
        self.user32.SendMessageW(window, WM_WTSSESSION_CHANGE, 0x7, 0)  # locking: nothing to do
        time.sleep(0.3)
        self.assertEqual(self.filter.hook_rearms, 3)

    def test_the_hook_is_re_armed_periodically(self) -> None:
        import app.platform

        self.assertEqual(app.platform.WINDOWS_REARM_INTERVAL_MS, 15_000)
        self.start_observer()
        with mock.patch("app.platform.WINDOWS_REARM_INTERVAL_MS", 200):
            self.start_filter()
        self.assertGreaterEqual(self.wait_for_rearms(3), 3)
        # And it still filters afterwards: a dropout mid-drag is removed.
        self.observed.clear()
        self.user32.mouse_event(0x0002, 0, 0, 0, 0)
        time.sleep(0.05)
        self.user32.mouse_event(0x0004, 0, 0, 0, 0)
        time.sleep(0.005)
        self.user32.mouse_event(0x0002, 0, 0, 0, 0)
        time.sleep(0.05)
        self.user32.mouse_event(0x0004, 0, 0, 0, 0)
        time.sleep(0.4)
        self.assertEqual([entry[0] for entry in self.observed_buttons()], [WM_LBUTTONDOWN, WM_LBUTTONUP])


@unittest.skipUnless(platform.system() == "Windows", "Windows only")
class InjectableWindowsTests(unittest.TestCase):
    """The check that decides whether a release may be held back."""

    def test_own_and_ordinary_windows_accept_injection(self) -> None:
        import ctypes
        from ctypes import wintypes

        from app.platform import InjectableWindows

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        targets = InjectableWindows(user32, kernel32)
        # The desktop belongs to Explorer (or nothing on a server runner),
        # which runs as the same user: a re-sent release reaches it.
        self.assertTrue(targets.accepts_injection(wintypes.POINT(5, 5)))

    def test_a_process_that_cannot_be_opened_is_refused(self) -> None:
        import ctypes

        from app.platform import InjectableWindows

        targets = InjectableWindows(ctypes.WinDLL("user32"), ctypes.WinDLL("kernel32"))
        self.assertFalse(targets._process_ok(4), "PID 4 is the System process")


if __name__ == "__main__":
    unittest.main()
