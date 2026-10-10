"""The Windows hook: its decisions against a stand-in for Windows (run
everywhere), and the real hook with real input (Windows, `DCF_E2E=1`).

The real tests genuinely click and move the pointer, so they only run when
`DCF_E2E=1` is set (CI does). A second low-level hook installed *before* the
filter acts as the observer: Windows calls the most recently installed hook
first, so anything the filter suppresses never reaches it, the same thing an
application would see. Every system setting a test changes is put back.
"""

try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

import faulthandler
import itertools
import os
import platform
import statistics
import sys
import threading
import time
import unittest
from contextlib import contextmanager
from dataclasses import replace
from unittest import mock

from app.core import Button
from app.platform import (
    INJECTED_MARK,
    INJECTED_MOTION_MARKS,
    MOTION_MARK_FOR,
    TELEPORT_MARK,
    WINDOWS_STAMP_ERROR_S,
    FilterConfig,
    GlobalClickFilter,
    InputSender,
    WindowsApi,
    WindowsHook,
    mark_kind,
    mark_seq,
    normalized_absolute,
    send_batch,
)

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MOUSEWHEEL = 0x020A
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C
WM_MOUSEHWHEEL = 0x020E
MESSAGES = {
    WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_RBUTTONDOWN, WM_RBUTTONUP, 0x0207, 0x0208,
    WM_MOUSEWHEEL, WM_XBUTTONDOWN, WM_XBUTTONUP, WM_MOUSEHWHEEL,
}
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
        self.relocation_checks = 0
        self.now_ms = 10_000.0
        self.hook = WindowsHook(click_filter, self, accepts_injection=lambda _x, _y: True)
        click_filter._use_os_time = True
        click_filter._stamp_error_s = WINDOWS_STAMP_ERROR_S
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
        self.relocation_checks += 1
        return not self.hidden and not self.remote

    def buttons_swapped(self) -> bool:
        return self.swapped

    def within_drag_rect(self, point, location) -> bool:
        if point is None or location is None:
            return False
        return abs(location[0] - point[0]) < self.drag[0] and abs(location[1] - point[1]) < self.drag[1]

    def _swap(self, button: Button) -> Button:
        if self.swapped and button in (Button.LEFT, Button.RIGHT):
            return Button.RIGHT if button is Button.LEFT else Button.LEFT
        return button

    def button_flags(self, button: Button, pressed: bool):
        return (self._swap(button), pressed)  # the physical button

    def button_data(self, button: Button) -> int:
        return WindowsApi.BUTTON_DATA.get(button, 0)

    def button_input(self, flags, when, mark, data=0):
        assert data == self.button_data(flags[0]), "a side button is re-sent with its own mouseData"
        return ("button", flags, when, mark)

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

    def scroll(self, delta: int, axis: int = 1, extra: int = 0, flags: int = 0) -> None:
        """A wheel notch: WM_MOUSEWHEEL (axis 1) or WM_MOUSEHWHEEL (2)."""
        self.queue.append(("hand-wheel", axis, delta, extra, flags))

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
        elif kind == "hand-wheel":
            _, axis, delta, extra, flags = item
            data = (delta & 0xFFFF) << 16
            # The hook looks at the wheel only while the wheel fix is on.
            if self.hook._owner._wheel_on and self.hook.wheel(
                axis, data, flags, extra, int(self.now_ms), self.now_ms / 1000, int(self.now_ms)
            ):
                return  # dropped
            self.seen.append(("wheel", axis, delta, extra))
        else:
            _, (button, pressed), when, mark = item
            self._button(self._swap(button), pressed, self.INJECTED, mark, when or int(self.now_ms))

    def _move(self, point, flags, extra) -> None:
        if self.hook.watch[0] and self.hook.motion(
            point[0], point[1], flags, extra, int(self.now_ms), self.now_ms / 1000, int(self.now_ms)
        ):
            return  # held back: the pointer stays where it was
        self.cursor = point
        self.seen.append(("move", point, extra))

    def _button(self, button, pressed, flags, extra, tick) -> None:
        dropped = self.hook.button(
            button, pressed, self.cursor[0], self.cursor[1], flags, tick, extra, self.now_ms / 1000, int(self.now_ms)
        )
        if not dropped:
            self.seen.append(("down" if pressed else "up", button, self.cursor, tick, extra))

    # -- what apps saw -----------------------------------------------------
    def buttons(self) -> list:
        return [entry for entry in self.seen if entry[0] in ("down", "up")]

    def wheel(self) -> list:
        return [entry[1:3] for entry in self.seen if entry[0] == "wheel"]

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
        self.filter = GlobalClickFilter(FilterConfig.uniform(60, [Button.LEFT]))
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

    def test_small_motion_passes_and_the_up_goes_out_where_the_pointer_is(self) -> None:
        self.click()
        self.win.move(2, 0)                                           # inside the drag rectangle
        self.win.run()
        self.assertEqual(self.win.cursor, (202, 200), "a hand resting on the mouse isn't held back")
        self.fire_timers()
        # Still the same spot to apps (inside the drag rectangle): a plain up
        # there, with no jump to the click and back.
        self.assertEqual(self.win.buttons()[1][:3], ("up", Button.LEFT, (202, 200)))
        self.assertEqual(self.win.buttons()[1][3], int(self.win.now_ms), "stamped now: motion came first")
        self.assertEqual([entry[1:] for entry in self.win.seen if entry[0] == "move"], [((202, 200), 0)])
        self.assertEqual(self.win.cursor, (202, 200))
        self.assertFalse(self.win.hook.watch[0])

    def test_small_motion_then_leaving_the_click(self) -> None:
        self.click()
        self.win.move(2, 0)
        self.win.move(10, 0)
        self.win.run()
        self.assertEqual(self.win.buttons()[1][:3], ("up", Button.LEFT, (202, 200)))
        self.assertNotIn(TELEPORT_MARK, [entry[2] for entry in self.win.seen if entry[0] == "move"])
        self.assertEqual(self.win.cursor, (212, 200))

    def test_a_drop_still_inside_the_drag_rectangle_goes_out_where_the_pointer_is(self) -> None:
        # The drag rectangle is 4 px each way here: 3 px off is the same spot
        # (a plain up), 4 px off is not (taken back to where it came up).
        for step, up_at, marks in ((3, (303, 200), []), (4, (300, 200), [TELEPORT_MARK])):
            with self.subTest(step=step):
                self.setUp()
                self.win.press()
                self.win.wait(50)
                self.win.move(100, 0)
                self.win.wait(50)
                self.win.release()
                self.win.wait(1)
                self.win.move(step, 0)
                self.win.run()
                self.fire_timers()
                self.assertEqual(self.win.buttons()[1][:3], ("up", Button.LEFT, up_at))
                self.assertEqual(self.win.cursor, (300 + step, 200))
                moves = [entry[2] for entry in self.win.seen if entry[0] == "move"]
                self.assertEqual([mark for mark in moves if mark == TELEPORT_MARK], marks)

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
        marks = [mark_kind(entry[2]) for entry in self.win.seen if entry[0] == "move"][-2:]
        self.assertEqual(marks, [TELEPORT_MARK, MOTION_MARK_FOR[Button.LEFT]])
        self.assertFalse(self.win.hook.watch[0])

    def test_motion_past_the_window_drops_a_drag_where_it_came_up(self) -> None:
        # No timer: the first move stamped past the window settles the
        # release, which goes out at its spot before that move.
        self.win.press()
        self.win.wait(50)
        self.win.move(100, 0)
        self.win.wait(50)
        self.win.release()
        self.win.wait(1)
        self.win.move(10, 0)                                          # inside the window
        self.win.wait(70)
        self.win.move(10, 0)                                          # past it
        self.win.run()
        self.assertEqual(self.win.buttons()[1][:3], ("up", Button.LEFT, (300, 200)))
        self.assertEqual(self.win.cursor, (320, 200))
        marks = [mark_kind(entry[2]) for entry in self.win.seen if entry[0] == "move"][-3:]
        self.assertEqual(marks, [TELEPORT_MARK, MOTION_MARK_FOR[Button.LEFT], MOTION_MARK_FOR[Button.LEFT]])
        self.assertFalse(self.win.hook.watch[0])
        self.fire_timers()
        self.assertEqual(len(self.win.buttons()), 2, "the timer must not send the up again")

    def test_a_release_sent_with_its_way_back_is_waited_for_until_that_is_back(self) -> None:
        self.drag_and_let_go_while_moving()
        for timer in list(FakeTimer.created):
            timer.fire()
        ((number, _sent),) = self.filter._in_flight[Button.LEFT]
        there, release, back = self.win.queue
        self.assertEqual((mark_kind(there[2]), mark_seq(there[2])), (TELEPORT_MARK, 0))
        self.assertEqual((mark_kind(release[3]), mark_seq(release[3])), (INJECTED_MARK, 0), "it numbers nothing")
        self.assertEqual((mark_kind(back[2]), mark_seq(back[2])), (MOTION_MARK_FOR[Button.LEFT], number))
        self.win._process(self.win.queue.pop(0))
        self.win._process(self.win.queue.pop(0))
        self.assertEqual(self.win.buttons()[1][:3], ("up", Button.LEFT, (300, 200)))
        self.assertEqual(len(self.filter._in_flight[Button.LEFT]), 1, "the way back is still to come")
        self.win.run()
        self.assertEqual(len(self.filter._in_flight[Button.LEFT]), 0)
        self.assertFalse(self.win.hook.watch[0])

    def test_a_batch_that_does_not_go_in_is_not_waited_for(self) -> None:
        self.click()
        self.win.send = lambda inputs: 0                              # blocked: an elevated window
        self.win.move(50, 0)
        self.win.run()
        self.assertEqual(len(self.filter._in_flight[Button.LEFT]), 0)
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

    def test_a_teleport_that_cannot_be_made_leaves_nothing_in_flight(self) -> None:
        self.drag_and_let_go_while_moving()
        real_move_input = self.win.move_input

        def move_input(x, y, mark):
            if mark_kind(mark) == MOTION_MARK_FOR[Button.LEFT]:
                raise OSError("no virtual screen")
            return real_move_input(x, y, mark)

        self.win.move_input = move_input
        with mock.patch("app.platform._logged_sites", set()), self.assertLogs("app.platform", "WARNING"):
            self.fire_timers()
        self.assertEqual(len(self.filter._in_flight[Button.LEFT]), 0, "a way back never sent is still awaited")

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
        self.filter.update(replace(self.filter.config, buttons=frozenset([Button.LEFT, Button.RIGHT])))
        self.win.swapped = True
        self.click()
        self.fire_timers()
        self.assertEqual([entry[:2] for entry in self.win.buttons()], [("down", Button.RIGHT), ("up", Button.RIGHT)])
        self.assertEqual(mark_kind(self.win.buttons()[1][4]), INJECTED_MARK, "the up was held and re-sent")

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

    def hidden_pointer_stream(self) -> None:
        """A game's mouse-look, left and right filtered: the left's up is
        held with no place, the right goes down meanwhile, the left's up is
        re-sent by its timer while the hand keeps moving, and the right
        comes up behind it."""
        self.configure(buttons=frozenset({Button.LEFT, Button.RIGHT}))
        self.win.hidden = True
        self.win.press()
        self.win.wait(80)
        self.win.release()                                           # held, with no place
        self.win.wait(5)
        self.win.press(Button.RIGHT)
        self.win.wait(5)
        for _ in range(3):
            self.win.move(7, 0)
        self.win.run()
        for _ in range(5):
            self.win.move(7, 0)                                      # still to be handled ...
        for timer in list(FakeTimer.created):
            timer.fire()                                             # ... as the left's up is re-sent
        FakeTimer.created.clear()
        self.win.release(Button.RIGHT)                               # behind the left's up, still on its way
        for _ in range(5):
            self.win.move(7, 0)
        self.win.run()
        self.fire_timers()

    def test_a_hidden_pointer_never_has_the_hands_motion_re_sent(self) -> None:
        # A re-sent move is absolute: in a game's mouse-look it would jerk
        # the view. Buttons still keep their order across buttons; motion
        # waits for none of them.
        self.hidden_pointer_stream()
        moves = [entry for entry in self.win.seen if entry[0] == "move"]
        resent = [entry for entry in moves if mark_kind(entry[2]) in INJECTED_MOTION_MARKS or entry[2] == TELEPORT_MARK]
        self.assertEqual(resent, [], "no move was re-sent")
        self.assertEqual(len(moves), 13, "every move of the hand's went through, once")
        self.assertEqual(self.win.cursor, (291, 200))
        self.assertEqual(
            [entry[:2] for entry in self.win.buttons()],
            [("down", Button.LEFT), ("down", Button.RIGHT), ("up", Button.LEFT), ("up", Button.RIGHT)],
        )
        self.assertEqual([mark_kind(entry[4]) for entry in self.win.buttons()[2:]], [INJECTED_MARK] * 2,
                         "the left's up re-sent by its timer, the right's up re-sent behind it")
        self.assertFalse(self.win.hook.watch[0])

    def test_a_remote_session_never_has_the_hands_motion_re_sent(self) -> None:
        self.win.remote = True
        self.hidden_pointer_stream()
        self.assertEqual([entry for entry in self.win.seen if entry[0] == "move" and entry[2]], [])

    def test_a_hidden_pointer_never_holds_motion_back_past_the_window(self) -> None:
        # A re-sent move is absolute: in a game's mouse-look it would jump
        # the view. So motion past the window passes, and the timer delivers.
        self.win.hidden = True
        self.click()
        self.win.wait(100)
        self.win.move(50, 0)
        self.win.run()
        self.assertEqual([entry[0] for entry in self.win.seen], ["down", "move"])
        self.assertEqual([entry[2] for entry in self.win.seen if entry[0] == "move"], [0], "the hand's own move")
        self.fire_timers()
        self.assertEqual([entry[0] for entry in self.win.seen], ["down", "move", "up"])

    def test_pen_and_touch_clicks_pass_untouched(self) -> None:
        # Windows marks the clicks it makes from pen and touch input: they
        # are never filtered. Not held (the up comes at once, still marked as
        # Windows marked it), and a quick second tap is no bounce.
        self.win.press(extra=PEN)
        self.win.wait(80)
        self.win.release(extra=PEN)
        self.win.wait(5)
        self.win.press(extra=PEN)
        self.win.wait(30)
        self.win.release(extra=PEN)
        self.win.wait(5)
        self.win.move(50, 0, extra=PEN)
        self.win.run()
        self.assertEqual([entry[0] for entry in self.win.seen], ["down", "up", "down", "up", "move"])
        self.assertEqual({entry[-1] for entry in self.win.buttons()}, {PEN}, "nothing was re-sent")
        self.assertEqual(FakeTimer.created, [], "nothing was held")
        self.assertEqual(self.filter.filtered_count, 0)

    def test_a_remote_session_keeps_timer_delivery(self) -> None:
        self.win.remote = True
        self.drag_and_let_go_while_moving()
        self.fire_timers()
        self.assertEqual(self.win.buttons()[1][:3], ("up", Button.LEFT, (500, 200)))
        self.assertNotIn(TELEPORT_MARK, [entry[2] for entry in self.win.seen if entry[0] == "move"])

    def test_an_unknown_pointer_position_sends_the_release_where_it_is(self) -> None:
        # Windows couldn't say where the pointer was as watching began
        # (another desktop had the input), and no move has come through the
        # hook since to tell: the pointer was put somewhere without input
        # (SetCursorPos), which no hook sees.
        self.win.press()
        self.win.wait(50)
        self.win.move(100, 0)
        self.win.wait(50)
        self.win.cursor_known = False
        self.win.release()
        self.win.wait(1)
        self.win.cursor = (500, 200)
        self.fire_timers()
        self.assertEqual(self.win.buttons()[1][:3], ("up", Button.LEFT, (500, 200)))

    def test_other_programs_motion_is_left_alone(self) -> None:
        self.click()
        self.win.queue.append(("move", normalized_absolute(600, 600, *self.win.screen), 0))
        self.win.run()
        self.assertEqual(self.win.cursor, (600, 600))
        self.assertEqual(len(self.win.buttons()), 1, "still held: the move wasn't the hand's")
        # The click still ends where it was made, not where the other
        # program put the pointer (that would be a drag), and the pointer
        # stays where it was put.
        self.fire_timers()
        self.assertEqual(self.win.buttons()[1][:3], ("up", Button.LEFT, (200, 200)))
        self.assertEqual(self.win.cursor, (600, 600))

    def test_the_hand_moves_on_from_where_another_program_put_the_pointer(self) -> None:
        self.click()
        # Nothing of the hand's is held back, so the pointer stays where the
        # other program put it, and the hand's steps are taken from there.
        self.win.queue.append(("move", normalized_absolute(600, 600, *self.win.screen), 0))
        # Only 2 px, but 400 px from where the button came up: it leaves the
        # spot, so the up goes out there (taken to the click and back) and
        # this step waits behind it.
        self.win.move(2, 0)
        self.win.move(30, 0)                                          # waits behind it too
        self.win.run()
        self.assertEqual(self.win.buttons()[1][:3], ("up", Button.LEFT, (200, 200)))
        after_up = [entry[1] for entry in self.win.seen[self.win.seen.index(self.win.buttons()[1]) + 1:]]
        self.assertEqual(after_up, [(600, 600), (602, 600), (632, 600)], "back, then the hand's two steps")
        self.assertEqual(self.win.cursor, (632, 600))
        self.fire_timers()
        self.assertEqual(len(self.win.buttons()), 2, "the up went out once")

    def test_another_programs_move_while_the_hands_moves_are_held_back(self) -> None:
        # The hand leaves the click: the up is re-sent and the hand's move
        # waits behind it. Another program's move gets in before the up comes
        # back, then the hand moves on. The hand's held moves still land, after
        # the other program's, so its next step goes on from where they lead,
        # not from where the other program put the pointer.
        self.click()
        self.win.move(30, 0)                                          # held: (230, 200)
        self.win.queue.append(("move", normalized_absolute(600, 600, *self.win.screen), 0))
        self.win.move(10, 0)                                          # held: 10 px on from (230, 200)
        self.win.run()
        self.fire_timers()
        moves = [entry[1] for entry in self.win.seen if entry[0] == "move"]
        self.assertEqual(moves, [(600, 600), (230, 200), (240, 200)], "the hand's path, after the other program's move")
        self.assertEqual(self.win.cursor, (240, 200))
        self.assertFalse(self.win.hook.watch[0])

    def test_pen_motion_rebases_like_another_programs(self) -> None:
        self.click()                                                  # the mouse's click
        self.win.move(50, 0, extra=PEN)
        self.win.run()
        self.assertEqual(self.win.cursor, (250, 200))
        self.assertEqual(len(self.win.buttons()), 1, "pen motion doesn't settle the mouse's release")
        self.fire_timers()
        self.assertEqual(self.win.buttons()[1][:3], ("up", Button.LEFT, (200, 200)))
        self.assertEqual(self.win.cursor, (250, 200))


    # -- 1.0: side buttons, the wheel, pen and touch, devices ----------------------
    def configure(self, **changes) -> None:
        self.filter.update(replace(self.filter.config, **changes))

    def test_a_side_button_bounce_is_dropped_and_no_release_waits(self) -> None:
        self.configure(buttons=frozenset({Button.LEFT, Button.BACK}))
        self.win.press(Button.BACK)
        self.win.wait(60)
        self.win.release(Button.BACK)
        self.win.wait(6)
        self.win.press(Button.BACK)                                  # the bounce
        self.win.wait(10)
        self.win.release(Button.BACK)
        self.win.run()
        self.assertEqual([entry[:2] for entry in self.win.buttons()], [("down", Button.BACK), ("up", Button.BACK)])
        self.assertEqual([entry[4] for entry in self.win.buttons()], [0, 0], "the release went straight through")
        self.assertEqual(FakeTimer.created, [], "nothing held")

    def test_a_side_button_re_sent_keeps_its_xbutton_and_never_swaps(self) -> None:
        self.configure(buttons=frozenset({Button.LEFT, Button.FORWARD}))
        self.win.swapped = True                                      # left-handed: only left and right swap
        self.win.press(Button.RIGHT)                                 # the physical right is the logical left
        self.win.wait(80)
        self.win.release(Button.RIGHT)
        self.win.wait(70)                                            # past the left's window
        self.win.press(Button.FORWARD)                               # settles the left's up, goes out behind it
        self.win.run()
        buttons = [entry[:2] for entry in self.win.buttons()]
        self.assertEqual(buttons, [("down", Button.LEFT), ("up", Button.LEFT), ("down", Button.FORWARD)])
        self.assertEqual(mark_kind(self.win.buttons()[2][4]), INJECTED_MARK, "re-sent, with XBUTTON2 (FakeWindows checks)")

    def test_a_reversing_wheel_notch_is_dropped_on_its_own_axis(self) -> None:
        self.configure(wheel_fix=True, wheel_window_ms=50)
        heard = []
        self.filter._on_wheel = lambda axis, dropped: heard.append((axis, dropped))
        # (delta, axis, ms until the next): the vertical wheel's stray up
        # notch goes, the horizontal one's own reversal too; a reversal
        # 140 ms after the last vertical notch is the hand's.
        for delta, axis, gap in ((-120, 1, 20), (120, 1, 20), (-120, 1, 20), (120, 2, 20), (-120, 2, 100), (120, 1, 0)):
            self.win.scroll(delta, axis)
            self.win.wait(gap)
        self.win.run()
        self.assertEqual(self.win.wheel(), [(1, -120), (1, -120), (2, 120), (1, 120)])
        self.assertEqual(heard, [(1, False), (1, True), (1, False), (2, False), (2, True), (1, False)])
        self.assertEqual(self.win.buttons(), [])

    def test_the_wheel_is_left_alone_while_the_fix_is_off(self) -> None:
        for delta in (-120, 120, -120):
            self.win.scroll(delta)
            self.win.wait(5)
        self.win.run()
        self.assertEqual(self.win.wheel(), [(1, -120), (1, 120), (1, -120)])

    def test_other_programs_pen_and_touchpad_scrolling_pass(self) -> None:
        from app.devices_win import HandleInfo

        self.configure(wheel_fix=True)
        self.win.scroll(-120)
        self.win.wait(5)
        self.win.scroll(120, flags=self.win.INJECTED)                 # another program's
        self.win.scroll(120, extra=PEN)
        self.win.hook.device = lambda: HandleInfo("trackpad", "hid:04f3:3087:ELAN", "ELAN Touchpad")
        self.win.scroll(120)
        self.win.run()
        self.assertEqual(len(self.win.wheel()), 4)

    def test_touchpad_and_ignored_device_clicks_pass_untouched(self) -> None:
        from app.devices_win import HandleInfo

        devices = []
        self.filter._on_device = devices.append
        self.configure(ignored_devices=frozenset({"usb:046d:c08b:G502"}))
        for info in (HandleInfo("trackpad", "hid:04f3:3087:ELAN", "ELAN Touchpad"),
                     HandleInfo("mouse", "usb:046d:c08b:G502", "G502 HERO")):
            self.win.hook.device = lambda info=info: info
            self.win.seen.clear()
            self.win.press()
            self.win.wait(40)
            self.win.release()
            self.win.wait(1)                                         # a touchpad double-tap's gap
            self.win.press()
            self.win.wait(40)
            self.win.release()
            self.win.run()
            self.assertEqual([entry[0] for entry in self.win.buttons()], ["down", "up"] * 2, info.kind)
            self.assertEqual({entry[4] for entry in self.win.buttons()}, {0}, "nothing re-sent")
        self.assertEqual(FakeTimer.created, [])
        self.assertEqual([device.key for device in devices], ["hid:04f3:3087:ELAN", "usb:046d:c08b:G502"])
        self.assertEqual(self.filter.passed_counts, {"touch": 2, "ignored device": 2})

    def test_a_mouse_not_ignored_is_filtered_and_known(self) -> None:
        from app.devices_win import HandleInfo

        events = []
        self.filter._on_event = events.append
        self.win.hook.device = lambda: HandleInfo("mouse", "usb:046d:c08b:G502", "G502 HERO")
        self.win.press()
        self.win.wait(40)
        self.win.release()
        self.win.wait(5)
        self.win.press()                                             # a dropout: cancels the held up
        self.win.run()
        self.assertEqual([entry[0] for entry in self.win.buttons()], ["down"])
        self.assertEqual({event.device for event in events}, {"usb:046d:c08b:G502"})

    def test_a_pointer_with_absolute_positions_is_filtered_as_a_mouse(self) -> None:
        # A virtual machine's pointer (here VMware's) reports absolute
        # positions through Raw Input. Its clicks carry no pen or touch
        # signature, and its bounces are a mouse's: they are filtered, and the
        # user may still let it through by name.
        try:
            from test_devices import FakeRawInput
        except ImportError:  # run as tests.<module> from the repository root
            from tests.test_devices import FakeRawInput
        from app.devices_win import RawInputDevices

        devices, events = [], []
        self.filter._on_device = devices.append
        self.filter._on_event = events.append
        raw = RawInputDevices(FakeRawInput(), threaded=False)
        self.win.hook.device = raw.current
        raw.on_input(0x40)                                           # it moved
        self.win.press()
        self.win.wait(40)
        self.win.release()
        self.win.wait(5)
        self.win.press()                                             # a dropout: cancels the held up
        self.win.run()
        self.assertEqual([entry[0] for entry in self.win.buttons()], ["down"])
        self.assertEqual(self.filter.passed_counts, {}, "nothing passed as a touch")
        self.assertEqual({event.device for event in events}, {"usb:0e0f:0003:VMware Pointing Device"})
        (device,) = devices
        self.assertEqual((device.kind, device.filtered), ("mouse", True))
        # Ignored by name, it passes like any ignored device.
        self.configure(ignored_devices=frozenset({device.key}))
        self.assertEqual([(info.key, info.filtered) for info in self.filter.seen_devices()], [(device.key, False)])

    def test_a_pen_tap_while_the_mouses_release_is_held_goes_out_behind_it(self) -> None:
        self.click()                                                 # the mouse's up is held
        self.win.press(extra=PEN)
        self.win.run()
        self.assertEqual([entry[0] for entry in self.win.buttons()], ["down", "up", "down"])
        self.assertEqual([mark_kind(entry[4]) for entry in self.win.buttons()[1:]], [INJECTED_MARK] * 2,
                         "the held up, then the pen's down re-sent behind it")
        self.win.release(extra=PEN)
        self.win.run()
        self.assertEqual(self.win.buttons()[-1][0], "up")
        self.assertEqual(self.win.buttons()[-1][4], PEN, "its release passes untouched")

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
        sender = InputSender(api, lost=lambda _button, _seq: None)
        for index in range(50):
            sender.submit([(index, Button.LEFT, index + 1)])
        sender.close()
        self.assertEqual(api.sent, list(range(50)))
        self.assertEqual(set(api.threads), {"dcf-send"})

    def test_inputs_that_dont_go_in_are_settled_not_waited_for(self) -> None:
        lost = []
        batch = [("there", None, 0), ("up", Button.LEFT, 7), ("back", Button.RIGHT, 9)]
        sent = send_batch(self.Api(accept=1), batch, lambda button, seq: lost.append((button, seq)))
        self.assertEqual(sent, 1)
        self.assertEqual(lost, [(Button.LEFT, 7), (Button.RIGHT, 9)])

    def test_a_failing_send_settles_the_whole_batch(self) -> None:
        api = self.Api()
        api.send = mock.Mock(side_effect=OSError("blocked"))
        lost = []
        with mock.patch("app.platform._logged_sites", set()), self.assertLogs("app.platform", "WARNING"):
            self.assertEqual(send_batch(api, [("up", Button.RIGHT, 3)], lambda *settled: lost.append(settled)), 0)
        self.assertEqual(lost, [(Button.RIGHT, 3)])

    def test_after_closing_batches_are_sent_directly(self) -> None:
        api = self.Api()
        sender = InputSender(api, lost=lambda _button, _seq: None)
        sender.close()
        sender.submit([("late", Button.LEFT, 1)])
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
                        (int(message), (info.pt.x, info.pt.y), int(info.dwExtraInfo), int(info.time), time.perf_counter(),
                         int(info.mouseData))
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

    def start_filter(self, threshold: int = 60, buttons=(Button.LEFT,), **config) -> GlobalClickFilter:
        self.filter = GlobalClickFilter(FilterConfig.uniform(threshold, list(buttons), **config))
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

    def mouse(self, flags: int, dx: int = 0, dy: int = 0, data: int = 0, extra: int = 0):
        return self.api.INPUT(0, self.api.MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, extra))

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

    def test_the_filter_allows_for_how_late_a_stamp_can_say_an_event_came(self) -> None:
        self.assertEqual(self.filter._stamp_error_s, WINDOWS_STAMP_ERROR_S)

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
        self.assertIn(TELEPORT_MARK, [mark_kind(entry[2]) for entry in self.observed if entry[0] == WM_MOUSEMOVE])

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
        marks = [entry[2] for entry in self.observed_buttons()]
        self.report(f"[swapped] {[hex(message) for message in messages]} marks {[hex(mark) for mark in marks]}")
        self.assertEqual(len(messages), 4, messages)
        self.assertEqual(messages[0::2], [messages[0]] * 2)
        self.assertEqual(messages[1::2], [messages[0] + 1] * 2, "each re-sent release matches its press")
        # The releases apps saw are the ones this app re-sent: both were held.
        self.assertEqual([mark_kind(mark) for mark in marks[1::2]], [INJECTED_MARK] * 2, "a release went through without being held")

    def test_a_small_move_after_a_click_releases_in_place(self) -> None:
        # Inside the drag rectangle it is still the same spot: a plain up
        # where the pointer is, no jump to the click and back.
        self.start_filter()
        self.click_at_200()
        time.sleep(0.005)
        self.move_by(2, 0)
        time.sleep(0.3)
        up = self.first(WM_LBUTTONUP)
        self.assertEqual(self.observed[up][1], (202, 200), f"{self.observed}")
        self.assertEqual(mark_kind(self.observed[up][2]), INJECTED_MARK, "the up was held and re-sent")
        self.assertNotIn(TELEPORT_MARK, [mark_kind(entry[2]) for entry in self.observed if entry[0] == WM_MOUSEMOVE])
        self.assertEqual(self.cursor(), (202, 200))

    def test_another_programs_move_doesnt_turn_a_click_into_a_drag(self) -> None:
        # Pen-signed motion stands for another program's (this test's own
        # input counts as the hand's): the pointer goes where it was put, and
        # the held up still lands on the click.
        self.start_filter()
        self.click_at_200()
        time.sleep(0.005)
        with self.api.physical_pixels():
            self.send(self.api.move_input(600, 400, PEN))
        time.sleep(0.3)
        up = self.first(WM_LBUTTONUP)
        self.assertEqual(self.observed[up][1], (200, 200), f"the click ended off its spot: {self.observed}")
        self.assertEqual(self.cursor(), (600, 400))

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


MOUSEEVENTF_XDOWN, MOUSEEVENTF_XUP, MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x0080, 0x0100, 0x0800, 0x1000


@unittest.skipUnless(_run_e2e(), "needs Windows and DCF_E2E=1 (it injects real input)")
class WindowsInputFeatureTests(RealWindows):
    """1.0: side buttons, the wheel, pen and touch, Raw Input."""

    def setUp(self) -> None:
        super().setUp()
        self.start_observer()

    def side(self, down: bool, number: int, extra: int = 0) -> None:
        self.send(self.mouse(MOUSEEVENTF_XDOWN if down else MOUSEEVENTF_XUP, data=number, extra=extra))

    def notch(self, delta: int, horizontal: bool = False) -> None:
        self.send(self.mouse(MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL, data=delta))

    @staticmethod
    def high_word(value: int) -> int:
        word = (value >> 16) & 0xFFFF
        return word - 0x10000 if word >= 0x8000 else word

    def test_a_side_button_bounce_is_dropped_with_no_release_held(self) -> None:
        self.start_filter(buttons=(Button.LEFT, Button.BACK))
        self.observed.clear()
        self.side(True, 1)
        time.sleep(0.06)
        self.side(False, 1)
        time.sleep(0.008)
        self.side(True, 1)                                            # the bounce, 8 ms on
        time.sleep(0.03)
        self.side(False, 1)
        time.sleep(0.3)
        self.side(True, 2)                                            # forward, not filtered here
        time.sleep(0.06)
        self.side(False, 2)
        time.sleep(0.3)
        sides = [(entry[0], self.high_word(entry[5]), entry[2]) for entry in self.observed if entry[0] in (WM_XBUTTONDOWN, WM_XBUTTONUP)]
        self.assertEqual(
            sides,
            [(WM_XBUTTONDOWN, 1, 0), (WM_XBUTTONUP, 1, 0), (WM_XBUTTONDOWN, 2, 0), (WM_XBUTTONUP, 2, 0)],
            "the bounce went, and each release went straight through (none re-sent)",
        )
        self.assertEqual(self.filter.filtered_count, 1)

    def test_a_side_button_re_sent_behind_a_held_release_keeps_its_xbutton(self) -> None:
        self.exact_relative_motion()
        self.pointer_where_it_counts()
        self.start_filter(buttons=(Button.LEFT, Button.FORWARD))
        self.place(200, 200)
        self.button(0x0002)
        time.sleep(0.08)
        self.button(0x0004)                                           # held in place
        time.sleep(0.005)
        # Leaving the spot re-sends the left up, and the move waits behind
        # it; the forward press, which came after, waits there too and is
        # re-sent: as XBUTTON2, as it came.
        self.send(self.mouse(0x0001, 40, 0), self.mouse(MOUSEEVENTF_XDOWN, data=2))
        time.sleep(0.3)
        self.side(False, 2)
        time.sleep(0.3)
        order = [(entry[0], self.high_word(entry[5]) if entry[0] in (WM_XBUTTONDOWN, WM_XBUTTONUP) else 0, mark_kind(entry[2]))
                 for entry in self.observed_buttons()]
        self.report(f"[side] {[(hex(message), word, hex(mark)) for message, word, mark in order]}")
        self.assertEqual([entry[:2] for entry in order],
                         [(WM_LBUTTONDOWN, 0), (WM_LBUTTONUP, 0), (WM_XBUTTONDOWN, 2), (WM_XBUTTONUP, 2)])
        self.assertEqual([entry[2] for entry in order[1:3]], [INJECTED_MARK] * 2, "the left up held, the press re-sent")

    def test_a_reversing_wheel_notch_is_dropped(self) -> None:
        self.start_filter(wheel_fix=True, wheel_window_ms=50)
        self.observed.clear()
        for delta, pause in ((-120, 0.02), (-120, 0.01), (120, 0.01), (-120, 0.2), (120, 0.05)):
            self.notch(delta)
            time.sleep(pause)
        self.notch(120, horizontal=True)                              # its own axis: no reversal
        time.sleep(0.3)
        wheel = [(entry[0], self.high_word(entry[5])) for entry in self.observed if entry[0] in (WM_MOUSEWHEEL, WM_MOUSEHWHEEL)]
        self.assertEqual(wheel, [(WM_MOUSEWHEEL, -120), (WM_MOUSEWHEEL, -120), (WM_MOUSEWHEEL, -120),
                                 (WM_MOUSEWHEEL, 120), (WM_MOUSEHWHEEL, 120)])
        self.assertEqual(self.filter.wheel_dropped, 1)

    def test_the_wheel_passes_while_the_fix_is_off(self) -> None:
        self.start_filter()
        self.observed.clear()
        for delta in (-120, 120, -120):
            self.notch(delta)
            time.sleep(0.005)
        time.sleep(0.2)
        self.assertEqual([self.high_word(entry[5]) for entry in self.observed if entry[0] == WM_MOUSEWHEEL], [-120, 120, -120])

    def test_a_pen_marked_click_passes_untouched(self) -> None:
        self.start_filter()
        self.observed.clear()
        for flags, pause in ((0x0002, 0.04), (0x0004, 0.003), (0x0002, 0.04), (0x0004, 0.3)):
            self.send(self.mouse(flags, extra=PEN))                   # a pen's tap, then another 3 ms on
            time.sleep(pause)
        buttons = [(entry[0], entry[2]) for entry in self.observed_buttons()]
        self.assertEqual(buttons, [(WM_LBUTTONDOWN, PEN), (WM_LBUTTONUP, PEN)] * 2, "not filtered, held or re-sent")
        self.assertEqual(self.filter.passed_counts, {"touch": 2})

    def test_raw_input_is_registered_on_the_hooks_window(self) -> None:
        self.start_filter()
        devices = self.filter._raw_input
        self.assertIsNotNone(devices)
        self.assertTrue(devices.registered, "RegisterRawInputDevices failed")
        self.filter._callback_timings = timings = []
        for _ in range(50):
            self.move_by(1, 0)
            self.move_by(-1, 0)
        time.sleep(0.3)
        raw = [seconds for message, _watched, seconds in timings if message == 0x00FF]
        self.report(f"[raw input] {len(raw)} WM_INPUT; current device {devices.current()}")
        self.assertGreater(len(raw), 0, "no WM_INPUT reached the hook's window")
        self.assertIsNone(devices.current(), "SendInput names no device")

    def test_the_wheel_side_buttons_and_raw_input_stay_cheap(self) -> None:
        self.start_filter(buttons=(Button.LEFT, Button.BACK), wheel_fix=True)
        self.filter._callback_timings = timings = []
        for index in range(2000):
            self.notch(120 if (index // 7) % 2 else -120)
            if index % 100 == 99:
                time.sleep(0.005)
        for _ in range(50):
            self.side(True, 1)
            time.sleep(0.002)
            self.side(False, 1)
            time.sleep(0.02)
        time.sleep(0.3)

        def summary(name: str, values: list) -> str:
            ordered = sorted(seconds * 1000 for seconds in values)
            if not ordered:
                return f"{name} n=0"
            p99 = ordered[max(0, int(len(ordered) * 0.99) - 1)]
            return f"{name} n={len(ordered)} median={statistics.median(ordered):.4f} ms p99={p99:.4f} ms max={ordered[-1]:.3f} ms"

        wheel = [seconds for message, _w, seconds in timings if message == WM_MOUSEWHEEL]
        sides = [seconds for message, _w, seconds in timings if message in (WM_XBUTTONDOWN, WM_XBUTTONUP)]
        raw = [seconds for message, _w, seconds in timings if message == 0x00FF]
        self.report(f"[cost] Windows: {summary('wheel', wheel)}; {summary('side', sides)}; {summary('WM_INPUT', raw)}")
        self.assertGreaterEqual(len(wheel), 2000)
        self.assertLess(sorted(wheel)[int(len(wheel) * 0.99) - 1] * 1000, 1.0)
        self.assertLess(sorted(raw)[int(len(raw) * 0.99) - 1] * 1000, 1.0)


@unittest.skipUnless(_run_e2e(), "needs Windows and DCF_E2E=1 (it injects real input)")
class WindowsRawInputOrderTests(RealWindows):
    """A measurement the device attribution rests on (see devices_win): on
    one thread that has both a low-level mouse hook and a window registered
    for Raw Input, as the filter's hook thread has, does the hook's callback
    for a click run before or after that click's WM_INPUT; is the WM_INPUT
    already in the thread's queue while the callback runs, so that the
    callback could read it; and does a click the hook drops still produce
    one? SendInput's input stands in for a mouse's: its WM_INPUT names no
    device, but it goes through the same queue."""

    QS_RAWINPUT = 0x0400
    PM_REMOVE = 0x0001
    PM_QS_INPUT = 0x1C07 << 16  # QS_INPUT: input only, no sent messages (no hook re-entry)
    RID_INPUT = 0x10000003
    WM_INPUT = 0x00FF

    def probe(self, drop: bool, drain: bool, clicks: int = 12) -> dict:
        import ctypes
        from ctypes import wintypes

        from app.devices_win import Win32RawInput
        from app.platform import SessionWindow

        events: list = []
        ready, done = threading.Event(), threading.Event()
        state = {"in_hook": False, "thread": None, "registered": False}
        LRESULT = ctypes.c_ssize_t
        HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

        def run() -> None:
            api = WindowsApi()
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, wintypes.DWORD]
            user32.SetWindowsHookExW.restype = ctypes.c_void_p
            user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
            user32.CallNextHookEx.restype = LRESULT
            user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
            user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
            user32.PeekMessageW.argtypes = [
                ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT
            ]
            user32.PeekMessageW.restype = wintypes.BOOL
            user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
            user32.GetQueueStatus.argtypes = [wintypes.UINT]
            user32.GetQueueStatus.restype = wintypes.DWORD
            user32.GetRawInputData.argtypes = [
                wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p, ctypes.POINTER(wintypes.UINT), wintypes.UINT
            ]
            user32.GetRawInputData.restype = wintypes.UINT
            state["thread"] = kernel32.GetCurrentThreadId()
            raw = Win32RawInput()
            header = raw._header_size
            buffer = (ctypes.c_ubyte * 1024)()

            def on_input(lparam: int) -> None:
                size = wintypes.UINT(1024)
                if user32.GetRawInputData(lparam, self.RID_INPUT, buffer, ctypes.byref(size), header) == 0xFFFFFFFF:
                    return
                data = bytes(buffer[: size.value])
                kind = int.from_bytes(data[0:4], "little")
                flags = int.from_bytes(data[header + 4: header + 6], "little") if kind == 0 else 0
                events.append(("input", time.perf_counter(), flags, state["in_hook"]))

            window = SessionWindow(api, lambda: None, on_input=on_input)
            state["registered"] = bool(window.hwnd) and raw.register(window.hwnd)
            message = wintypes.MSG()
            peeked = wintypes.MSG()

            @HOOKPROC
            def callback(code: int, wparam: int, lparam: int) -> int:
                if code >= 0 and wparam in (WM_LBUTTONDOWN, WM_LBUTTONUP):
                    queued = bool((user32.GetQueueStatus(self.QS_RAWINPUT) >> 16) & self.QS_RAWINPUT)
                    events.append(("hook", time.perf_counter(), int(wparam), queued))
                    if drain:
                        state["in_hook"] = True
                        try:
                            while user32.PeekMessageW(
                                ctypes.byref(peeked), window.hwnd, self.WM_INPUT, self.WM_INPUT,
                                self.PM_REMOVE | self.PM_QS_INPUT,
                            ):
                                user32.DispatchMessageW(ctypes.byref(peeked))
                        finally:
                            state["in_hook"] = False
                    if drop:
                        return 1
                return user32.CallNextHookEx(None, code, wparam, lparam)

            hook = user32.SetWindowsHookExW(14, callback, None, 0)
            ready.set()
            try:
                while not done.is_set():
                    if user32.GetMessageW(ctypes.byref(message), None, 0, 0) <= 0:
                        break
                    user32.TranslateMessage(ctypes.byref(message))
                    user32.DispatchMessageW(ctypes.byref(message))
            finally:
                user32.UnhookWindowsHookEx(hook)
                raw.register(None, remove=True)
                window.close()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.assertTrue(ready.wait(5))
        self.assertTrue(state["registered"], "Raw Input registration failed")
        time.sleep(0.2)
        for _ in range(clicks):
            self.button(0x0002)
            time.sleep(0.05)
            self.button(0x0004)
            time.sleep(0.1)
        time.sleep(0.3)
        done.set()
        self.user32.PostThreadMessageW(state["thread"], 0x0012, 0, 0)
        thread.join(3)
        # Pair each hook call with the WM_INPUT carrying the same transition
        # (the k-th left down with the k-th RI_MOUSE_LEFT_BUTTON_DOWN).
        result = {"hook first": 0, "input first": 0, "queued at hook": 0, "read in hook": 0, "unpaired": 0,
                  "inputs": 0, "hooks": 0, "lag_ms": []}
        for message, flag in ((WM_LBUTTONDOWN, 0x0001), (WM_LBUTTONUP, 0x0002)):
            hooks = [(index, entry) for index, entry in enumerate(events) if entry[0] == "hook" and entry[2] == message]
            inputs = [(index, entry) for index, entry in enumerate(events) if entry[0] == "input" and entry[2] & flag]
            result["hooks"] += len(hooks)
            result["inputs"] += len(inputs)
            for pair in itertools.zip_longest(hooks, inputs[: len(hooks)]):
                if pair[0] is None or pair[1] is None:
                    result["unpaired"] += 1
                    continue
                (hook_index, hook), (input_index, raw) = pair
                result["queued at hook"] += hook[3]
                result["read in hook"] += raw[3]
                result["hook first" if hook_index < input_index else "input first"] += 1
                result["lag_ms"].append(round((raw[1] - hook[1]) * 1000, 3))
        return result

    def test_the_order_of_a_clicks_hook_call_and_its_raw_input(self) -> None:
        self.place(300, 300)
        passed = self.probe(drop=False, drain=False)
        drained = self.probe(drop=False, drain=True)
        dropped = self.probe(drop=True, drain=False)
        for name, result in (("passed", passed), ("passed, read in the hook", drained), ("dropped", dropped)):
            lags = sorted(result.pop("lag_ms"))
            spread = f"lag ms min={lags[0]} median={statistics.median(lags)} max={lags[-1]}" if lags else "no pairs"
            self.report(f"[raw order] {name}: {result}; WM_INPUT minus hook call: {spread}")
        self.assertEqual(passed["hooks"], 24)
        self.assertEqual(passed["inputs"], 24, "every injected press and release produced a WM_INPUT")


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

    def test_closing_the_session_window_leaves_it_listening(self) -> None:
        # Restart Manager, and taskkill without /F, send WM_CLOSE to every
        # top-level window; the session window must outlive it.
        self.start_filter()
        window = self.filter._hook_window
        self.user32.IsWindow.argtypes = [self.wintypes.HWND]
        self.user32.IsWindow.restype = self.wintypes.BOOL
        self.assertEqual(self.user32.SendMessageW(window, 0x0010, 0, 0), 0)  # WM_CLOSE
        self.assertTrue(self.user32.IsWindow(window), "WM_CLOSE destroyed the session window")
        self.user32.SendMessageW(window, 0x02B1, 0x8, 0)                      # and it still hears an unlock
        self.assertGreaterEqual(self.wait_for_rearms(1), 1)

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
class WindowsApiButtonTests(unittest.TestCase):
    """What SendInput is given for each button (nothing is sent)."""

    def test_side_buttons_name_their_xbutton_and_never_swap(self) -> None:
        api = WindowsApi()
        for swapped in (False, True):
            with self.subTest(swapped=swapped), mock.patch.object(api, "buttons_swapped", return_value=swapped):
                self.assertEqual(api.button_flags(Button.LEFT, True), 0x0008 if swapped else 0x0002)
                self.assertEqual(api.button_flags(Button.MIDDLE, False), 0x0040)
                self.assertEqual(api.button_flags(Button.BACK, True), 0x0080)
                self.assertEqual(api.button_flags(Button.FORWARD, False), 0x0100)
        self.assertEqual([api.button_data(button) for button in Button], [0, 0, 0, 1, 2])
        sent = api.button_input(0x0080, 1234, INJECTED_MARK, 2)
        self.assertEqual((sent.mi.dwFlags, sent.mi.mouseData, sent.mi.time, sent.mi.dwExtraInfo), (0x0080, 2, 1234, INJECTED_MARK))


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
