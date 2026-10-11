"""Pausing: the timed pause, the global shortcut, and the promise that the
app never reads keystrokes (no keyboard hook, tap or monitor anywhere)."""

try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

try:
    import _trust_helpers as helpers
except ImportError:
    from tests import _trust_helpers as helpers

import ast
import ctypes
import os
import platform
import re
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from app import pause

APP = Path(__file__).resolve().parent.parent / "app"


class PauseHelpersTests(unittest.TestCase):
    def test_minutes_are_kept_between_one_and_a_day(self) -> None:
        self.assertEqual(pause.clamp_minutes(15), 15)
        self.assertEqual(pause.clamp_minutes(0), 1)
        self.assertEqual(pause.clamp_minutes(-5), 1)
        self.assertEqual(pause.clamp_minutes(10_000), 24 * 60)
        self.assertEqual(pause.clamp_minutes("x"), pause.DEFAULT_MINUTES)
        self.assertEqual(pause.clamp_minutes(None), pause.DEFAULT_MINUTES)

    def test_the_shortcut_is_written_the_systems_way(self) -> None:
        self.assertEqual(pause.shortcut_text("Darwin"), "⌃⌥⌘P")
        self.assertEqual(pause.shortcut_text("Windows"), "Ctrl+Alt+Shift+P")
        self.assertEqual(pause.shortcut_text("Linux"), "")

    def test_until_text_names_the_day_only_when_it_isnt_today(self) -> None:
        now = datetime(2026, 10, 10, 15, 30).timestamp()
        with mock.patch.object(pause, "_clock", lambda moment: f"{moment.hour}:{moment.minute:02d}"):
            self.assertEqual(pause.until_text(now + 15 * 60, now), "15:45")
            self.assertEqual(pause.until_text(datetime(2026, 10, 11, 0, 10).timestamp(), now), "tomorrow at 0:10")
            self.assertEqual(pause.until_text(datetime(2026, 10, 13, 9, 0).timestamp(), now), "Tuesday at 9:00")

    def test_the_clock_has_hours_and_minutes(self) -> None:
        text = pause._clock(datetime(2026, 10, 10, 15, 45))
        self.assertRegex(text, r"(15|3)[:.]45")

    def test_create_picks_the_systems_shortcut(self) -> None:
        self.assertIsInstance(pause.create(lambda: None, "Darwin"), pause.MacShortcut)
        self.assertIsInstance(pause.create(lambda: None, "Windows"), pause.WindowsShortcut)
        other = pause.create(lambda: None, "Linux")
        self.assertFalse(other.start())
        self.assertTrue(other.error)


class FakeHotKeyApi:
    """Stands in for user32 on the shortcut's thread."""

    MSG = None  # set in __init__ (wintypes may not import everywhere)

    def __init__(self, register_ok=True, error=0) -> None:
        import queue

        class MSG(ctypes.Structure):
            _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint), ("wParam", ctypes.c_size_t),
                        ("lParam", ctypes.c_ssize_t)]

        self.MSG = MSG
        self.register_ok = register_ok
        self.error = error
        self.calls = []
        self.queue = queue.Queue()

    def thread_id(self) -> int:
        self.calls.append("thread_id")
        return 4242

    def make_queue(self, message) -> None:
        self.calls.append("make_queue")

    def register(self) -> bool:
        self.calls.append("register")
        return self.register_ok

    def unregister(self) -> None:
        self.calls.append("unregister")

    def last_error(self) -> int:
        return self.error

    def get_message(self, message) -> int:
        kind, value = self.queue.get(timeout=5)
        message.message = kind
        message.wParam = value
        return 0 if kind == pause.WM_QUIT else 1

    def post_quit(self, thread_id: int) -> None:
        self.calls.append(("post_quit", thread_id))
        self.queue.put((pause.WM_QUIT, 0))

    def press(self, hotkey_id=pause.HOTKEY_ID) -> None:
        self.queue.put((pause.WM_HOTKEY, hotkey_id))


class WindowsShortcutTests(unittest.TestCase):
    def test_registers_ctrl_alt_shift_p_without_repeats(self) -> None:
        self.assertEqual(pause.WINDOWS_MODIFIERS, 0x0002 | 0x0001 | 0x0004 | 0x4000)
        self.assertEqual(pause.VK_P, ord("P"))

    def test_a_press_calls_back_and_stop_unregisters(self) -> None:
        api = FakeHotKeyApi()
        pressed = threading.Event()
        shortcut = pause.WindowsShortcut(pressed.set, api=api)
        self.assertTrue(shortcut.start())
        self.assertTrue(shortcut.registered)
        api.press(hotkey_id=99)  # someone else's id: ignored
        api.press()
        self.assertTrue(pressed.wait(5))
        shortcut.stop()
        self.assertFalse(shortcut.registered)
        self.assertIn(("post_quit", 4242), api.calls)
        self.assertEqual(api.calls[-1], "unregister")
        self.assertEqual(api.calls[:3], ["thread_id", "make_queue", "register"])

    def test_a_taken_shortcut_says_so(self) -> None:
        api = FakeHotKeyApi(register_ok=False, error=pause.ERROR_HOTKEY_ALREADY_REGISTERED)
        shortcut = pause.WindowsShortcut(lambda: None, api=api)
        self.assertFalse(shortcut.start())
        self.assertFalse(shortcut.registered)
        self.assertEqual(shortcut.error, "Another program already uses this shortcut.")
        self.assertNotIn("unregister", api.calls, "nothing was registered")

    def test_another_failure_gives_its_code(self) -> None:
        shortcut = pause.WindowsShortcut(lambda: None, api=FakeHotKeyApi(register_ok=False, error=5))
        self.assertFalse(shortcut.start())
        self.assertIn("error 5", shortcut.error)

    def test_a_failing_action_doesnt_end_the_thread(self) -> None:
        api = FakeHotKeyApi()
        calls = []

        def action():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("boom")

        shortcut = pause.WindowsShortcut(action, api=api)
        self.assertTrue(shortcut.start())
        api.press()
        api.press()
        deadline = time.monotonic() + 5
        while len(calls) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        shortcut.stop()
        self.assertEqual(len(calls), 2)

    def test_start_again_after_stop(self) -> None:
        api = FakeHotKeyApi()
        shortcut = pause.WindowsShortcut(lambda: None, api=api)
        self.assertTrue(shortcut.start())
        shortcut.stop()
        self.assertTrue(shortcut.start())
        shortcut.stop()
        self.assertEqual(api.calls.count("register"), 2)
        self.assertEqual(api.calls.count("unregister"), 2)


class FakeCarbon:
    """Stands in for Carbon's hot key functions."""

    def __init__(self, register_status=0) -> None:
        self.register_status = register_status
        self.registered = None
        self.handler = None
        self.removed = 0
        self.unregistered = 0
        self.event_id = (pause.four_cc(pause.SIGNATURE), pause.HOTKEY_ID)

    def GetApplicationEventTarget(self):  # noqa: N802 - Carbon's name
        return 0xA11

    def InstallEventHandler(self, target, callback, count, spec, user_data, out):  # noqa: N802
        spec = spec._obj
        self.handler = (target, callback, count, spec.eventClass, spec.eventKind)
        out._obj.value = 0xBEEF
        return 0

    def RemoveEventHandler(self, ref):  # noqa: N802
        self.removed += 1
        return 0

    def RegisterEventHotKey(self, code, modifiers, hotkey_id, target, options, out):  # noqa: N802
        self.registered = (code, modifiers, hotkey_id.signature, hotkey_id.id, target, options)
        if self.register_status == 0:
            out._obj.value = 0xF00D
        return self.register_status

    def UnregisterEventHotKey(self, ref):  # noqa: N802
        self.unregistered += 1
        return 0

    def GetEventParameter(self, event, name, kind, actual, size, actual_size, data):  # noqa: N802
        self.asked = (name, kind)
        found = data._obj
        found.signature, found.id = self.event_id
        return 0


class MacShortcutTests(unittest.TestCase):
    def test_registers_control_option_command_p_on_the_app_target(self) -> None:
        carbon = FakeCarbon()
        shortcut = pause.MacShortcut(lambda: None, carbon=carbon)
        self.assertTrue(shortcut.start())
        code, modifiers, signature, hotkey_id, target, options = carbon.registered
        self.assertEqual(code, 0x23, "kVK_ANSI_P")
        self.assertEqual(modifiers, 4096 | 2048 | 256, "controlKey | optionKey | cmdKey")
        self.assertEqual((signature, hotkey_id, target, options), (pause.four_cc("DCFx"), pause.HOTKEY_ID, 0xA11, 0))
        _target, _callback, count, event_class, kind = carbon.handler
        self.assertEqual((count, event_class, kind), (1, pause.four_cc("keyb"), 5), "kEventHotKeyPressed only")
        shortcut.stop()
        self.assertEqual((carbon.unregistered, carbon.removed), (1, 1))
        self.assertFalse(shortcut.registered)

    def test_its_own_hot_key_calls_back_and_others_pass_on(self) -> None:
        carbon = FakeCarbon()
        pressed = []
        shortcut = pause.MacShortcut(lambda: pressed.append(1), carbon=carbon)
        shortcut.start()
        self.assertEqual(shortcut._handle(None, 0x1, None), 0)
        self.assertEqual(carbon.asked, (pause.four_cc("----"), pause.four_cc("hkid")))
        self.assertEqual(pressed, [1])
        carbon.event_id = (pause.four_cc("XXXX"), 7)
        self.assertEqual(shortcut._handle(None, 0x1, None), pause.EVENT_NOT_HANDLED)
        self.assertEqual(pressed, [1])

    def test_a_refused_hot_key_says_why_and_leaves_no_handler(self) -> None:
        carbon = FakeCarbon(register_status=-9878)
        shortcut = pause.MacShortcut(lambda: None, carbon=carbon)
        self.assertFalse(shortcut.start())
        self.assertEqual(shortcut.error, "Another app already uses this shortcut.")
        self.assertEqual(carbon.removed, 1)
        carbon = FakeCarbon(register_status=-9879)
        shortcut = pause.MacShortcut(lambda: None, carbon=carbon)
        self.assertFalse(shortcut.start())
        self.assertEqual(shortcut.error, "RegisterEventHotKey failed (-9879).")

    @unittest.skipUnless(platform.system() == "Darwin", "macOS only")
    def test_carbon_has_every_function_it_calls(self) -> None:
        # Looking the symbols up registers nothing.
        library = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
        for name in ("GetApplicationEventTarget", "InstallEventHandler", "RemoveEventHandler",
                     "RegisterEventHotKey", "UnregisterEventHotKey", "GetEventParameter"):
            self.assertTrue(getattr(library, name), name)


class NeverKeystrokesTests(unittest.TestCase):
    """The README promises the app never reads keystrokes. Nothing in app/
    may install a keyboard hook, a keyboard event tap, a key event monitor,
    raw keyboard input or a key-state poll."""

    FORBIDDEN = (
        # Windows
        # (devices_win names RIM_TYPEKEYBOARD, the type of a raw input
        # report, but asks for mouse and digitizer reports only: see
        # test_raw_input_registers_pointers_only.)
        "WH_KEYBOARD", "WH_KEYBOARD_LL", "GetAsyncKeyState", "GetKeyboardState", "GetKeyState", "WM_KEYDOWN",
        "WM_KEYUP", "WM_SYSKEYDOWN", "RAWKEYBOARD",
        # macOS
        "kCGEventKeyDown", "kCGEventKeyUp", "kCGEventFlagsChanged", "kCGEventMaskForAllEvents", "NSEventMaskKeyDown",
        "NSEventMaskKeyUp", "NSKeyDownMask", "NSKeyUpMask", "NSEventMaskFlagsChanged", "NSEventMaskAny",
        "NSAnyEventMask", "addGlobalMonitorForEventsMatchingMask", "addGlobalMonitorForEventsMatchingMask_handler_",
        "CGEventSourceKeyState", "IOHIDManagerRegisterInputValueCallback", "IOHIDDeviceRegisterInputValueCallback",
    )

    def sources(self):
        for path in sorted(APP.rglob("*.py")):
            yield path, path.read_text(encoding="utf-8")

    @staticmethod
    def code_words(text: str) -> set:
        """Every name, attribute and string the code uses, docstrings and
        comments left out (they may name what the code never does)."""
        tree = ast.parse(text)
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                body = node.body
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                    docstrings.add(id(body[0].value))
        words = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                words.add(node.id)
            elif isinstance(node, ast.Attribute):
                words.add(node.attr)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                words.update(alias.asname or alias.name for alias in node.names)
                words.update(alias.name for alias in node.names)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                words.update(re.findall(r"\w+", node.value))
        return words

    def test_no_keyboard_api_is_named_anywhere(self) -> None:
        for path, text in self.sources():
            words = self.code_words(text)
            for name in self.FORBIDDEN:
                with self.subTest(path=path.name, name=name):
                    self.assertFalse(name in words, f"{path.name} uses {name}")

    def test_the_guard_sees_what_it_looks_for(self) -> None:
        words = self.code_words(
            'import Quartz\n"""WH_KEYBOARD_LL in a docstring"""\n'
            "x = Quartz.kCGEventKeyDown  # WM_KEYDOWN in a comment\n"
            'getattr(Quartz, "kCGEventMaskForAllEvents")\nhook(WH_KEYBOARD_LL)\n'
        )
        self.assertIn("kCGEventKeyDown", words)
        self.assertIn("kCGEventMaskForAllEvents", words)
        self.assertIn("WH_KEYBOARD_LL", words)
        self.assertNotIn("WM_KEYDOWN", words)
        words = self.code_words('"""WH_KEYBOARD_LL in a docstring"""\n')
        self.assertNotIn("WH_KEYBOARD_LL", words)

    def test_every_windows_hook_is_the_low_level_mouse_hook(self) -> None:
        found = 0
        for path, text in self.sources():
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and getattr(node.func, "attr", "").startswith("SetWindowsHookEx"):
                    found += 1
                    first = node.args[0]
                    name = getattr(first, "id", None)
                    with self.subTest(path=path.name, line=node.lineno):
                        self.assertEqual(name, "WH_MOUSE_LL")
            self.assertNotRegex(text, r"WH_MOUSE_LL\s*=\s*(?!14\b)\d+")
        self.assertGreater(found, 0, "the mouse hook is still found")

    def test_raw_input_registers_pointers_only(self) -> None:
        from app import devices_win

        for page, usage in devices_win.USAGES:
            with self.subTest(page=page, usage=usage):
                # Generic desktop keyboard (6) and keypad (7) are never asked for.
                self.assertFalse(page == 0x01 and usage in (0x06, 0x07))
                self.assertIn((page, usage), {(0x01, 0x02), (0x0D, 0x05), (0x0D, 0x04)})

    def test_event_taps_ask_for_mouse_and_tablet_events_only(self) -> None:
        text = (APP / "platform.py").read_text(encoding="utf-8")
        start = text.index("def mask_for(")
        body = text[start:text.index("return mask", start)]
        self.assertIn("(*BUTTONS, *MOTION, *((SCROLL,) if wheel else ()))", body)
        # What those three are made of: mouse button, motion and wheel events.
        before = text[text.index("def _run_macos(") : start]
        parts = [
            re.search(r"BUTTONS = \{(.*?)\n\s*\}", before, re.S).group(1),
            re.search(r"DRAGGED = \((.*?)\)", before, re.S).group(1),
            re.search(r"MOTION = (.*?)\n", before).group(1),
            re.search(r"SCROLL = (.*?)\n", before).group(1),
        ]
        names = re.findall(r"Quartz\.(kCGEvent\w+)", " ".join(parts))
        self.assertGreaterEqual(len(names), 10)
        for name in names:
            with self.subTest(event=name):
                self.assertTrue("Mouse" in name or name == "kCGEventScrollWheel", name)
        # The permission probe and the self-test ask for tablet proximity.
        probe = (APP / "permissions.py").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"1 << (\w+)", probe), ["_TABLET_PROXIMITY"])
        self.assertIn("_TABLET_PROXIMITY = 24", probe)
        for path, source in self.sources():
            for match in re.finditer(r"CGEventMaskBit\(([^)]*)\)", source):
                with self.subTest(path=path.name, mask=match.group(1)):
                    self.assertNotRegex(match.group(1), r"Key|Flags|\b1[0-2]\b")

    def test_the_pause_shortcut_registers_a_hot_key_and_nothing_else(self) -> None:
        source = (APP / "pause.py").read_text(encoding="utf-8")
        code = "\n".join(line.split("#", 1)[0] for line in source.splitlines())
        for needed in ("RegisterEventHotKey", "RegisterHotKey"):
            self.assertIn(needed, code)
        for unwanted in ("SetWindowsHookEx", "CGEventTapCreate", "addGlobalMonitor", "RegisterRawInputDevices"):
            self.assertNotIn(unwanted, code)


class ControllerPauseTests(helpers.ControllerCase):
    def setUp(self) -> None:
        super().setUp()
        self.changes = []
        self.controller.pause_changed.connect(lambda: self.changes.append(self.controller.paused))

    def test_pause_stops_the_filter_but_keeps_the_users_on(self) -> None:
        controller = self.controller
        controller.set_active(True)
        controller.pause(15)
        self.assertFalse(controller.active)
        self.assertTrue(helpers.FakeFilter.instances[0].stopped)
        self.assertTrue(controller.paused)
        self.assertTrue(controller.wanted, "the menus still show it on")
        self.assertTrue(controller.settings["fix_enabled"])
        self.assertEqual(controller.paused_until, self.clock.now + 15 * 60)
        self.assertEqual(self.changes, [True])

    def test_nothing_to_pause_while_off(self) -> None:
        self.controller.pause(15)
        self.assertFalse(self.controller.paused)
        self.assertEqual(self.changes, [])

    def test_resume_starts_filtering_again(self) -> None:
        controller = self.controller
        controller.set_active(True)
        controller.pause(15)
        controller.resume()
        self.assertTrue(controller.active)
        self.assertFalse(controller.paused)
        self.assertIsNone(controller.paused_until)
        self.assertEqual(len(helpers.FakeFilter.instances), 2)
        self.assertEqual(self.changes, [True, False])

    def test_the_pause_ends_by_itself_by_the_wall_clock(self) -> None:
        controller = self.controller
        controller.set_active(True)
        controller.pause(15)
        self.clock.now += 14 * 60
        controller._check_pause_end()
        self.assertTrue(controller.paused)
        self.clock.now += 61  # a sleep through the end counts too
        controller._check_pause_end()
        self.assertFalse(controller.paused)
        self.assertTrue(controller.active)

    def test_the_apps_own_restarts_keep_the_pause(self) -> None:
        # A wake, a session switch back or a retry all go through set_active.
        controller = self.controller
        controller.set_active(True)
        controller.pause(15)
        self.assertFalse(controller.set_active(True))
        self.assertFalse(controller.active)
        self.assertTrue(controller.paused)
        self.assertEqual(controller.failure, "", "not a failure")

    def test_turning_off_ends_the_pause(self) -> None:
        controller = self.controller
        controller.set_active(True)
        controller.pause(15)
        controller.set_active(False)
        self.assertFalse(controller.paused)
        self.assertFalse(controller.wanted)
        self.assertFalse(controller.settings["fix_enabled"])
        self.assertEqual(self.changes, [True, False])
        controller._check_pause_end()
        self.assertFalse(controller.active, "the old pause doesn't turn it back on")

    def test_pausing_again_restarts_the_time(self) -> None:
        controller = self.controller
        controller.set_active(True)
        controller.pause(15)
        self.clock.now += 600
        controller.pause(30)
        self.assertEqual(controller.paused_until, self.clock.now + 30 * 60)

    def test_resume_with_no_pause_still_ends_calibrations_pause(self) -> None:
        controller = self.controller
        controller.set_active(True)
        controller.suspend()
        controller.resume()
        self.assertTrue(controller.active)
        controller.suspend()
        controller.end_suspension()
        self.assertTrue(controller.active)

    def test_calibration_ending_during_a_pause_doesnt_start_the_filter(self) -> None:
        controller = self.controller
        controller.enable_after_calibration()  # on, calibration measuring
        controller.pause(15)
        controller.end_suspension()
        self.assertFalse(controller.active)
        self.assertTrue(controller.paused)
        controller.resume()
        self.assertTrue(controller.active)

    def test_turned_on_from_calibration_during_a_pause_keeps_the_pause(self) -> None:
        controller = self.controller
        controller.set_active(True)
        controller.pause(15)
        controller.enable_after_calibration()
        self.assertTrue(controller.paused)
        self.assertFalse(controller.suspended, "a pause and calibration's never overlap")
        self.clock.now += 16 * 60
        controller._check_pause_end()
        self.assertTrue(controller.active)

    def test_a_pause_while_calibration_measures_takes_over(self) -> None:
        # The shortcut pressed while Test and Tune measures: the user's pause
        # replaces calibration's, so resume() can only mean the pause.
        controller = self.controller
        controller.set_active(True)
        controller.suspend()
        self.assertTrue(controller.suspended)
        controller.pause(15)
        self.assertFalse(controller.suspended)
        self.assertTrue(controller.paused)
        controller.suspend()  # Test and Tune measuring again: nothing to stop
        self.assertFalse(controller.suspended)
        controller.resume()
        self.assertFalse(controller.paused)
        self.assertTrue(controller.active)

    def test_measuring_ending_during_a_pause_leaves_the_pause_alone(self) -> None:
        # What the 1.1 window does when Test and Tune stops measuring: resume()
        # only while suspended. During a pause it isn't, so the pause runs on.
        controller = self.controller
        controller.set_active(True)
        controller.suspend()
        controller.pause(15)
        if controller.suspended:
            controller.resume()
        self.assertTrue(controller.paused)
        self.assertFalse(controller.active)

    def test_status_and_tooltip_say_until_when(self) -> None:
        controller = self.controller
        controller.set_active(True)
        with mock.patch("app.pause._clock", return_value="3:45 PM"):
            controller.pause(15)
            self.assertEqual(controller.status_text(), "Paused until 3:45 PM")
            self.assertEqual(controller.status_sentence(), "Paused until 3:45 PM.")
            self.assertEqual(controller.tooltip_text(), "Mouse Double-Click Fixer: paused until 3:45 PM")

    def test_diagnostics_say_how_long(self) -> None:
        controller = self.controller
        self.assertEqual(controller.diagnostic_state()["paused"], "no")
        controller.set_active(True)
        controller.pause(15)
        self.clock.now += 5 * 60
        self.assertEqual(controller.diagnostic_state()["paused"], "for 10 more minutes")


class ControllerShortcutTests(helpers.ControllerCase):
    def test_no_shortcut_until_the_app_starts_its_services(self) -> None:
        self.assertEqual(helpers.FakeShortcut.made, [])
        self.assertFalse(self.controller.pause_shortcut_registered)
        self.controller.start_services()
        self.assertTrue(self.controller.pause_shortcut_registered)
        self.assertEqual(len(helpers.FakeShortcut.made), 1)

    def test_the_shortcut_pauses_and_resumes(self) -> None:
        controller = self.controller
        controller.start_services()
        controller.set_active(True)
        shortcut = helpers.FakeShortcut.made[0]
        shortcut.press()
        self.process_events()  # handled on the UI thread, queued
        self.assertTrue(controller.paused)
        self.assertEqual(controller.paused_until, self.clock.now + pause.DEFAULT_MINUTES * 60)
        shortcut.press()
        self.process_events()
        self.assertFalse(controller.paused)
        self.assertTrue(controller.active)

    def test_the_shortcut_from_another_thread_arrives_on_the_ui_thread(self) -> None:
        controller = self.controller
        controller.start_services()
        controller.set_active(True)
        seen = []
        original = controller.pause
        controller.pause = lambda minutes=15: (seen.append(threading.current_thread()), original(minutes))
        worker = threading.Thread(target=helpers.FakeShortcut.made[0].press)
        worker.start()
        worker.join()
        deadline = time.monotonic() + 5
        while not seen and time.monotonic() < deadline:
            self.process_events()
        self.assertEqual(seen, [threading.main_thread()])

    def test_the_shortcut_does_nothing_while_off(self) -> None:
        self.controller.start_services()
        helpers.FakeShortcut.made[0].press()
        self.process_events()
        self.assertFalse(self.controller.paused)
        self.assertFalse(self.controller.active)

    def test_turning_the_shortcut_off_and_on(self) -> None:
        controller = self.controller
        controller.start_services()
        shortcut = helpers.FakeShortcut.made[0]
        controller.set_pause_shortcut_enabled(False)
        self.assertFalse(controller.settings["pause_shortcut_enabled"])
        self.assertEqual(shortcut.stops, 1)
        self.assertFalse(controller.pause_shortcut_registered)
        self.assertEqual(controller.diagnostic_state()["pause shortcut"], f"{controller.pause_shortcut_text}, turned off"
                         if controller.pause_shortcut_text else "none on this system, turned off")
        controller.set_pause_shortcut_enabled(True)
        self.assertTrue(controller.pause_shortcut_registered)
        self.assertEqual(shortcut.starts, 2)

    def test_writing_the_setting_directly_registers_and_unregisters(self) -> None:
        # The 1.1 window writes "pause_shortcut_enabled" with _apply.
        controller = self.controller
        controller.start_services()
        shortcut = helpers.FakeShortcut.made[0]
        controller._apply(pause_shortcut_enabled=False)
        self.assertFalse(shortcut.registered)
        controller._apply(pause_shortcut_enabled=True)
        self.assertTrue(shortcut.registered)
        controller._apply(pause_shortcut_enabled=True)  # unchanged: left alone
        self.assertEqual((shortcut.starts, shortcut.stops), (2, 1))

    def test_turning_it_on_again_retries_a_refused_shortcut(self) -> None:
        helpers.FakeShortcut.refuse = "Another program already uses this shortcut."
        controller = self.controller
        controller.start_services()
        helpers.FakeShortcut.refuse = ""
        controller.set_pause_shortcut_enabled(True)
        self.assertTrue(controller.pause_shortcut_registered)

    def test_a_refused_shortcut_is_explained(self) -> None:
        helpers.FakeShortcut.refuse = "Another program already uses this shortcut."
        controller = self.controller
        controller.start_services()
        self.assertFalse(controller.pause_shortcut_registered)
        self.assertEqual(controller.pause_shortcut_error, "Another program already uses this shortcut.")
        self.assertIn("unavailable: Another program", controller.diagnostic_state()["pause shortcut"])

    def test_off_by_setting_means_never_registered(self) -> None:
        self.controller.set_pause_shortcut_enabled(False)
        self.controller.start_services()
        self.assertEqual(helpers.FakeShortcut.made, [])

    def test_quitting_unregisters(self) -> None:
        self.controller.start_services()
        shortcut = helpers.FakeShortcut.made[0]
        self.controller.shutdown()
        self.assertFalse(shortcut.registered)

    def test_the_text_is_the_systems(self) -> None:
        self.assertEqual(self.controller.pause_shortcut_text, pause.shortcut_text())


@unittest.skipUnless(os.environ.get("DCF_E2E") == "1", "registers a real global shortcut: CI sets DCF_E2E=1")
class RealShortcutTests(unittest.TestCase):
    """The real system calls, on a CI machine (never the owner's)."""

    @unittest.skipUnless(platform.system() == "Darwin", "macOS only")
    def test_carbon_registers_and_unregisters(self) -> None:
        shortcut = pause.MacShortcut(lambda: None)
        self.assertTrue(shortcut.start(), shortcut.error)
        self.assertTrue(shortcut.registered)
        shortcut.stop()
        self.assertFalse(shortcut.registered)
        self.assertTrue(shortcut.start(), "again after a stop")
        shortcut.stop()

    @unittest.skipUnless(platform.system() == "Darwin", "macOS only")
    def test_the_shortcut_installs_no_event_tap(self) -> None:
        # The system's own list of event taps: none is this process's, so no
        # keystroke (or anything else) is tapped for the shortcut.
        import Quartz

        shortcut = pause.MacShortcut(lambda: None)
        self.assertTrue(shortcut.start(), shortcut.error)
        try:
            error, taps, count = Quartz.CGGetEventTapList(256, None, None)
            self.assertEqual(error, 0)
            mine = [tap for tap in list(taps)[:count] if tap.tappingProcess == os.getpid()]
            self.assertEqual(mine, [])
        finally:
            shortcut.stop()

    @unittest.skipUnless(platform.system() == "Darwin", "macOS only")
    def test_carbon_delivers_the_combination(self) -> None:
        import AppKit
        import Quartz

        if not Quartz.CGPreflightPostEventAccess():
            self.skipTest("this machine may not post events")
        application = AppKit.NSApplication.sharedApplication()
        application.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        application.finishLaunching()
        pressed = []
        shortcut = pause.MacShortcut(lambda: pressed.append(1))
        self.assertTrue(shortcut.start(), shortcut.error)
        try:
            flags = Quartz.kCGEventFlagMaskControl | Quartz.kCGEventFlagMaskAlternate | Quartz.kCGEventFlagMaskCommand
            for down in (True, False):
                event = Quartz.CGEventCreateKeyboardEvent(None, pause.KVK_ANSI_P, down)
                Quartz.CGEventSetFlags(event, flags)
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
            deadline = time.monotonic() + 5
            while not pressed and time.monotonic() < deadline:
                event = application.nextEventMatchingMask_untilDate_inMode_dequeue_(
                    AppKit.NSEventMaskAny, AppKit.NSDate.dateWithTimeIntervalSinceNow_(0.1),
                    AppKit.NSDefaultRunLoopMode, True,
                )
                if event is not None:
                    application.sendEvent_(event)
        finally:
            shortcut.stop()
        self.assertEqual(pressed, [1])

    @unittest.skipUnless(platform.system() == "Windows", "Windows only")
    def test_register_hot_key_delivers_the_combination(self) -> None:
        from ctypes import wintypes

        pressed = threading.Event()
        shortcut = pause.WindowsShortcut(pressed.set)
        self.assertTrue(shortcut.start(), shortcut.error)
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)

            class KEYBDINPUT(ctypes.Structure):
                _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                            ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

            class MOUSEINPUT(ctypes.Structure):
                _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                            ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

            class INPUTUNION(ctypes.Union):
                _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

            class INPUT(ctypes.Structure):
                _fields_ = [("type", wintypes.DWORD), ("u", INPUTUNION)]

            keys = (0x11, 0x12, 0x10, pause.VK_P)  # Ctrl, Alt, Shift, P
            inputs = []
            for key in keys:
                inputs.append(INPUT(1, INPUTUNION(ki=KEYBDINPUT(key, 0, 0, 0, 0))))
            for key in reversed(keys):
                inputs.append(INPUT(1, INPUTUNION(ki=KEYBDINPUT(key, 0, 0x0002, 0, 0))))  # KEYEVENTF_KEYUP
            array = (INPUT * len(inputs))(*inputs)
            sent = user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT))
            self.assertEqual(sent, len(inputs), ctypes.get_last_error())
            self.assertTrue(pressed.wait(5), "WM_HOTKEY arrived")
        finally:
            shortcut.stop()
        self.assertFalse(shortcut.registered)

    @unittest.skipUnless(platform.system() == "Windows", "Windows only")
    def test_a_second_registration_is_refused_and_explained(self) -> None:
        first = pause.WindowsShortcut(lambda: None)
        self.assertTrue(first.start(), first.error)
        try:
            second = pause.WindowsShortcut(lambda: None)
            self.assertFalse(second.start())
            self.assertEqual(second.error, "Another program already uses this shortcut.")
        finally:
            first.stop()


if __name__ == "__main__":
    unittest.main()
