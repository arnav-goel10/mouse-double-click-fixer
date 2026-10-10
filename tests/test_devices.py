"""Which device and which app: devices_win (Raw Input), devices_mac (IOKit)
and frontmost. The parsing and the bookkeeping run everywhere against
stand-ins; the real system calls run on their own platform, read-only (no
input is posted)."""

try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

import platform
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from app import devices_mac, devices_win, frontmost
from app.devices_mac import MacDevice, SenderCache
from app.devices_win import RIM_TYPEHID, RIM_TYPEMOUSE, HandleInfo, PathInfo, RawInputDevices

USB_MOUSE = r"\\?\HID#VID_046D&PID_C08B&MI_01&Col01#8&2f1c3a5&0&0000#{378de44c-56ef-11d1-bc8c-00a0c91405dd}"
BT_MOUSE = r"\\?\HID#{00001124-0000-1000-8000-00805f9b34fb}_VID&0002046d_PID&b014&Col01#8&1a2b3c&0&0000#{378de44c-56ef-11d1-bc8c-00a0c91405dd}"
BLE_MOUSE = r"\\?\HID#{00001812-0000-1000-8000-00805f9b34fb}&Dev&VID_045e&PID_0b13&REV_0517&e1a0c1b2c3d4&Col01#9&1&0000#{378de44c-56ef-11d1-bc8c-00a0c91405dd}"
PS2_MOUSE = r"\\?\ACPI#PNP0F13#4&1d4a2f&0#{378de44c-56ef-11d1-bc8c-00a0c91405dd}"
I2C_TOUCHPAD = r"\\?\HID#ELAN0001&Col02#5&3a1b2c&0&0001#{4d1e55b2-f16f-11cf-88cb-001111000030}"
#: Pointers that report absolute positions: a virtual machine's (VMware's,
#: with its tools; Hyper-V's synthetic mouse) and the Remote Desktop mouse.
VMWARE_POINTER = r"\\?\HID#VID_0E0F&PID_0003&MI_01#7&2a3b4c&0&0000#{378de44c-56ef-11d1-bc8c-00a0c91405dd}"
HYPERV_POINTER = r"\\?\HID#{cfa8b69e-5b4a-4cc0-b98b-8ba1a1f3f95a}#5&1a8c06af&0&0000#{378de44c-56ef-11d1-bc8c-00a0c91405dd}"
RDP_POINTER = r"\\?\TERMINPUT_BUS#UMB#2&1f5b2c&0&RDP_MOU&0000#{378de44c-56ef-11d1-bc8c-00a0c91405dd}"


class WindowsDevicePathTests(unittest.TestCase):
    def test_usb(self) -> None:
        info = devices_win.parse_path(USB_MOUSE)
        self.assertEqual((info.bus, info.vendor, info.product), ("usb", 0x046D, 0xC08B))
        self.assertEqual(info.hardware, "VID_046D&PID_C08B&MI_01&Col01")

    def test_bluetooth_classic_and_low_energy(self) -> None:
        self.assertEqual(devices_win.parse_path(BT_MOUSE)[:3], ("bt", 0x046D, 0xB014))
        self.assertEqual(devices_win.parse_path(BLE_MOUSE)[:3], ("bt", 0x045E, 0x0B13))

    def test_devices_without_ids(self) -> None:
        self.assertEqual(devices_win.parse_path(PS2_MOUSE), PathInfo("acpi", 0, 0, "ACPI#PNP0F13"))
        self.assertEqual(devices_win.parse_path(I2C_TOUCHPAD)[:3], ("hid", 0, 0))
        self.assertEqual(devices_win.parse_path("")[:3], ("hid", 0, 0))

    def test_keys_prefer_the_serial_then_the_product_then_the_hardware_id(self) -> None:
        usb = devices_win.parse_path(USB_MOUSE)
        self.assertEqual(devices_win.device_key(usb, "AB12", "G502 HERO"), "usb:046d:c08b:AB12")
        self.assertEqual(devices_win.device_key(usb, "", "G502 HERO"), "usb:046d:c08b:G502 HERO")
        self.assertEqual(devices_win.device_key(devices_win.parse_path(PS2_MOUSE)), "acpi:0000:0000:ACPI#PNP0F13")
        # The same mouse on another USB port: another instance path, the same key.
        other_port = USB_MOUSE.replace("8&2f1c3a5&0&0000", "8&99aa11&0&0003")
        self.assertEqual(devices_win.device_key(devices_win.parse_path(other_port), "", "G502 HERO"),
                         devices_win.device_key(usb, "", "G502 HERO"))

    def test_kinds(self) -> None:
        kind_of = devices_win.kind_of
        self.assertEqual(kind_of(RIM_TYPEHID, 0x0D, 0x05), "trackpad")
        self.assertEqual(kind_of(RIM_TYPEHID, 0x0D, 0x04), "touchscreen")
        self.assertEqual(kind_of(RIM_TYPEHID, 0x0D, 0x02), "pen")
        self.assertEqual(kind_of(RIM_TYPEHID, 0x01, 0x05), "unknown")
        self.assertEqual(kind_of(RIM_TYPEMOUSE), "mouse")

    def test_registers_for_mice_touchpads_and_touchscreens(self) -> None:
        self.assertEqual(devices_win.USAGES, ((0x01, 0x02), (0x0D, 0x05), (0x0D, 0x04)))
        self.assertEqual(devices_win.RIDEV_INPUTSINK, 0x100)

    def test_registration_asks_to_hear_devices_come_and_go(self) -> None:
        # RegisterRawInputDevices is handed every usage with RIDEV_INPUTSINK
        # and RIDEV_DEVNOTIFY, and with RIDEV_REMOVE (and no window) to stop.
        import ctypes

        class RAWINPUTDEVICE(ctypes.Structure):
            _fields_ = [("usUsagePage", ctypes.c_ushort), ("usUsage", ctypes.c_ushort),
                        ("dwFlags", ctypes.c_uint32), ("hwndTarget", ctypes.c_void_p)]

        calls = []

        def register(devices, count, size):
            calls.append([(entry.usUsagePage, entry.usUsage, entry.dwFlags, entry.hwndTarget) for entry in devices[:count]])
            return 1

        api = devices_win.Win32RawInput.__new__(devices_win.Win32RawInput)
        api._ctypes, api.RAWINPUTDEVICE = ctypes, RAWINPUTDEVICE
        api.user32 = SimpleNamespace(RegisterRawInputDevices=register)
        self.assertTrue(api.register(0xABC))
        self.assertTrue(api.register(None, remove=True))
        self.assertEqual(calls[0], [(page, usage, 0x2100, 0xABC) for page, usage in devices_win.USAGES])
        self.assertEqual(calls[1], [(page, usage, 0x1, None) for page, usage in devices_win.USAGES])
        self.assertEqual(devices_win.RIDEV_DEVNOTIFY, 0x2000)

    def test_the_bits_a_mouse_reports_for_each_button(self) -> None:
        # RAWMOUSE numbers buttons as the hand presses them; the hook sees
        # them after the user's swap, which only ever swaps left and right.
        from app.core import Button

        flag = devices_win.button_flag
        self.assertEqual([flag(button, True) for button in Button], [0x0001, 0x0004, 0x0010, 0x0040, 0x0100])
        self.assertEqual([flag(button, False) for button in Button], [0x0002, 0x0008, 0x0020, 0x0080, 0x0200])
        self.assertEqual([flag(button, True, swapped=True) for button in Button], [0x0004, 0x0001, 0x0010, 0x0040, 0x0100])
        self.assertEqual((devices_win.wheel_flag(1), devices_win.wheel_flag(2)), (0x0400, 0x0800))


class FakeRawInput:
    """Win32RawInput's stand-in: a WM_INPUT's lparam is the device handle,
    or (handle, RAWMOUSE.usButtonFlags) for a report of a mouse's buttons or
    wheel.

    Each device is (Raw Input type, whether its reports carry absolute
    positions, HID usage, path, (product, serial)). Whether a mouse reports
    absolute positions is what Windows would say of it; nothing may take it
    for a sign of a touch surface (see devices_win's notes), so no call here
    gives it out: a test only says what the device is.

    `queued` holds WM_INPUT still waiting in the hook thread's queue: drain()
    hands each to `dispatch`, as DispatchMessage hands it to the window.
    `gone` holds the handles of unplugged devices: Windows can still hand
    out a report they made, but the handle no longer names a path or a usage."""

    def __init__(self) -> None:
        self.devices = {
            0x10: (RIM_TYPEMOUSE, False, (0, 0), USB_MOUSE, ("G502 HERO", "")),
            0x20: (RIM_TYPEHID, False, (0x0D, 0x05), I2C_TOUCHPAD, ("ELAN Touchpad", "")),
            0x30: (RIM_TYPEHID, True, (0x0D, 0x04), "", ("", "")),
            0x40: (RIM_TYPEMOUSE, True, (0, 0), VMWARE_POINTER, ("VMware Pointing Device", "")),
            0x50: (RIM_TYPEMOUSE, True, (0, 0), HYPERV_POINTER, ("", "")),
            0x60: (RIM_TYPEMOUSE, True, (0, 0), RDP_POINTER, ("", "")),
        }
        self.registered: list = []
        self.string_reads: list = []
        self.queued: list = []
        self.gone: set = set()
        self.dispatch = lambda _lparam: None
        self.refuse = False
        self.error = 0

    def register(self, hwnd, remove=False):
        self.registered.append((hwnd, remove))
        if self.refuse and not remove:
            self.error = 5  # ERROR_ACCESS_DENIED
            return False
        return True

    def read(self, lparam):
        handle, buttons = lparam if isinstance(lparam, tuple) else (lparam, 0)
        if handle == 0:
            return 0, RIM_TYPEMOUSE, buttons  # SendInput, or a touchpad's gestures turned into mouse input
        raw_type = self.devices[handle][0]
        return handle, raw_type, buttons if raw_type == RIM_TYPEMOUSE else 0

    def drain(self, hwnd):
        count = 0
        while self.queued:
            self.dispatch(self.queued.pop(0))
            count += 1
        return count

    def hid_usage(self, handle):
        return (0, 0) if handle in self.gone else self.devices[handle][2]

    def device_path(self, handle):
        return "" if handle in self.gone else self.devices[handle][3]

    def strings(self, path):
        self.string_reads.append(path)
        return next(entry[4] for entry in self.devices.values() if entry[3] == path)


class RawInputDevicesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.api = FakeRawInput()
        self.seen: list = []
        self.devices = RawInputDevices(self.api, seen=self.seen.append, threaded=False)

    def test_the_device_that_reported_last_is_current(self) -> None:
        self.assertTrue(self.devices.register(0xABC))
        self.assertIsNone(self.devices.current())
        self.devices.on_input(0x10)
        self.assertEqual(self.devices.current(), HandleInfo("mouse", "usb:046d:c08b:G502 HERO", "G502 HERO"))
        self.devices.on_input(0x20)
        self.assertEqual(self.devices.current().kind, "trackpad")
        self.devices.on_input(0)
        self.assertEqual(self.devices.current().kind, "trackpad", "input that names no device changes nothing")
        self.devices.on_input(0x10)
        self.assertEqual(self.devices.current().kind, "mouse")

    def test_a_quiet_touch_device_no_longer_counts(self) -> None:
        from time import monotonic

        self.devices.on_input(0x20)
        self.assertEqual(self.devices.current().kind, "trackpad")
        later = monotonic() + devices_win.TOUCH_QUIET_S + 0.1
        self.assertIsNone(self.devices.current(now=later), "a mouse clicked without moving may not have reported")
        self.devices.on_input(0x10)
        self.assertEqual(self.devices.current(now=later).kind, "mouse", "a mouse counts however long it rests")

    def test_each_device_is_classified_and_named_once(self) -> None:
        for handle in (0x10, 0x10, 0x20, 0x10, 0x20):
            self.devices.on_input(handle)
        self.assertEqual(self.api.string_reads, [USB_MOUSE, I2C_TOUCHPAD])
        self.assertEqual([info.key for info in self.seen], ["usb:046d:c08b:G502 HERO", "hid:0000:0000:ELAN Touchpad"])

    def test_a_device_with_no_path_keeps_its_kind_and_no_key(self) -> None:
        self.devices.on_input(0x30)
        self.assertEqual(self.devices.current().kind, "touchscreen")
        self.assertIsNone(self.devices.current().key)
        self.assertEqual(self.seen, [])

    def test_a_handle_with_no_path_is_asked_again_when_it_reports_next(self) -> None:
        # An unreadable path is a dead handle's, or a read that failed for a
        # moment: either way, not something to remember.
        self.devices.on_input(0x30)
        self.devices.on_input(0x30)
        self.api.devices[0x30] = (RIM_TYPEHID, False, (0x0D, 0x05), I2C_TOUCHPAD, ("ELAN Touchpad", ""))
        self.devices.on_input(0x30)
        self.assertEqual(self.devices.current(), HandleInfo("trackpad", "hid:0000:0000:ELAN Touchpad", "ELAN Touchpad"))
        self.assertEqual(self.api.string_reads, [I2C_TOUCHPAD])

    def test_pointers_with_absolute_positions_are_mice(self) -> None:
        # A virtual machine's pointer, Hyper-V's and the Remote Desktop
        # mouse report absolute positions. Their buttons are a mouse's, and
        # bounce like one's: they are filtered, as 0.5.3 filtered them.
        for handle, key in ((0x40, "usb:0e0f:0003:VMware Pointing Device"),
                            (0x50, "hid:0000:0000:{cfa8b69e-5b4a-4cc0-b98b-8ba1a1f3f95a}"),
                            (0x60, "hid:0000:0000:UMB")):
            with self.subTest(path=self.api.devices[handle][3]):
                self.devices.on_input(handle)
                info = self.devices.current()
                self.assertEqual((info.kind, info.key), ("mouse", key))
                self.assertNotIn(info.kind, devices_win.TOUCH_KINDS)

    def test_names_are_read_off_the_hook_thread_and_handed_back(self) -> None:
        woken = threading.Event()
        reader_threads = []
        api = self.api
        original = api.strings

        def strings(path):
            reader_threads.append(threading.current_thread().name)
            return original(path)

        api.strings = strings
        devices = RawInputDevices(api, wake=woken.set, seen=self.seen.append)
        devices.on_input(0x10)
        self.assertEqual(devices.current().kind, "mouse")
        self.assertIsNone(devices.current().key, "not named yet")
        self.assertTrue(woken.wait(2))
        self.assertEqual(reader_threads, ["dcf-device-names"])
        devices.resolved()                                       # the hook's thread, on WM_DEVICE
        self.assertEqual(devices.current().key, "usb:046d:c08b:G502 HERO")
        self.assertEqual(len(self.seen), 1)
        devices.close()

    def test_close_unregisters(self) -> None:
        self.devices.register(0xABC)
        self.devices.close()
        self.assertEqual(self.api.registered, [(0xABC, False), (None, True)])
        self.assertFalse(self.devices.registered)

    def test_without_raw_input_nothing_is_known(self) -> None:
        devices = RawInputDevices(None, unavailable="OSError: no user32")
        self.assertFalse(devices.register(0xABC))
        devices.on_input(0x10)
        self.assertIsNone(devices.current())
        self.assertIsNone(devices.attribute(devices_win.RI_MOUSE_BUTTON_DOWN[1]))
        self.assertEqual(devices.status, "raw input unavailable: OSError: no user32")

    def test_the_status_says_whether_raw_input_works(self) -> None:
        self.assertEqual(self.devices.status, "raw input unavailable: not registered yet")
        self.assertTrue(self.devices.register(0xABC))
        self.assertEqual(self.devices.status, "raw input ok")
        no_window = RawInputDevices(FakeRawInput())
        self.assertFalse(no_window.register(None))
        self.assertEqual(no_window.status, "raw input unavailable: the hook's window couldn't be created")
        api = FakeRawInput()
        api.refuse = True
        refused = RawInputDevices(api)
        self.assertFalse(refused.register(0xABC))
        self.assertEqual(refused.status, "raw input unavailable: RegisterRawInputDevices failed (error 5)")

    # -- which device a click came from (devices_win's module notes) ------------
    LEFT_DOWN, LEFT_UP, RIGHT_DOWN = 0x0001, 0x0002, 0x0004

    def clock(self, start: float = 1000.0) -> list:
        """devices_win's clock, moved by the test: now[0] seconds."""
        now = [start]
        patch = mock.patch("app.devices_win.monotonic", lambda: now[0])
        patch.start()
        self.addCleanup(patch.stop)
        return now

    def test_a_mouses_own_report_of_the_transition_wins_over_a_resting_palm(self) -> None:
        now = self.clock()
        for _ in range(200):                                         # a palm on the touchpad, mice still
            self.devices.on_input(0x20)
            now[0] += 0.008
        self.devices.on_input((0x10, self.LEFT_DOWN))                # the external mouse's press ...
        self.devices.on_input(0x20)                                  # ... the palm reporting again
        self.assertEqual(self.devices.current().kind, "trackpad", "the touchpad reported last")
        self.assertEqual(self.devices.attribute(self.LEFT_DOWN).key, "usb:046d:c08b:G502 HERO", "the mouse's press")

    def test_a_report_counts_for_one_event(self) -> None:
        now = self.clock()
        self.devices.on_input((0x10, self.LEFT_DOWN))                # the G502's press
        self.devices.on_input(0x40)                                  # another mouse moves
        now[0] += 0.002
        self.assertEqual(self.devices.attribute(self.LEFT_DOWN).key, "usb:046d:c08b:G502 HERO")
        self.assertEqual(self.devices.attribute(self.LEFT_DOWN).key, "usb:0e0f:0003:VMware Pointing Device",
                         "a second left press with no report of its own: the mouse that reported last (rule 3)")

    def test_evidence_is_only_for_its_own_transition_and_only_for_a_while(self) -> None:
        now = self.clock()
        self.devices.on_input(0x20)
        now[0] += 2.0                                                # mice still, the touchpad too, for long
        self.devices.on_input((0x10, self.RIGHT_DOWN))
        self.devices.on_input(0x20)
        self.assertEqual(self.devices.attribute(self.LEFT_DOWN).kind, "mouse",
                         "a right press is no evidence for a left one, but the mouse just reported (rule 3)")
        now[0] += devices_win.MOUSE_QUIET_S + 0.1
        self.devices.on_input(0x20)
        self.assertEqual(self.devices.attribute(self.RIGHT_DOWN).kind, "trackpad", "stale evidence counts for nothing")

    def test_a_touch_device_counts_only_while_the_mice_are_still(self) -> None:
        now = self.clock()
        self.devices.on_input(0x10)                                  # the mouse moves ...
        now[0] += 0.3
        self.devices.on_input(0x20)                                  # ... and the touchpad is tapped
        self.assertEqual(self.devices.attribute(self.LEFT_DOWN).key, "usb:046d:c08b:G502 HERO",
                         "within MOUSE_QUIET_S of the mouse moving, a click with no evidence is the mouse's")
        now[0] += devices_win.MOUSE_QUIET_S
        self.devices.on_input(0x20)
        self.assertEqual(self.devices.attribute(self.LEFT_DOWN).kind, "trackpad")
        now[0] += devices_win.TOUCH_QUIET_S + 0.1
        self.assertIsNone(self.devices.attribute(self.LEFT_DOWN), "nothing reported lately: an unknown device")

    WHEEL, HWHEEL = devices_win.RI_MOUSE_WHEEL, devices_win.RI_MOUSE_HWHEEL

    def test_a_touchpads_scroll_near_a_mouse_is_nobodys_notch(self) -> None:
        # A precision touchpad's two-finger scroll reaches the hook as wheel
        # input Windows makes from the gesture: no mouse reports a notch for
        # it. However lately a mouse moved, it is not that mouse's.
        now = self.clock()
        self.devices.on_input(0x10)                                  # the mouse moves ...
        now[0] += 0.3
        for _ in range(6):
            self.devices.on_input(0x20)                              # ... the touchpad scrolls
            self.assertIsNone(self.devices.attribute(self.WHEEL), "no mouse reported a notch: not judged")
            now[0] += 0.008
        now[0] += devices_win.MOUSE_QUIET_S + 0.1
        self.devices.on_input(0x20)
        self.assertIsNone(self.devices.attribute(self.WHEEL), "mice still: the touchpad's, but not judged either")
        self.assertEqual(self.devices.attribute(self.LEFT_DOWN).kind, "trackpad", "a click is judged as before")

    def test_a_wheel_notch_is_a_mouses_on_its_own_report_for_its_own_axis_alone(self) -> None:
        now = self.clock()
        for _ in range(100):                                         # a palm on the touchpad
            self.devices.on_input(0x20)
            now[0] += 0.008
        self.devices.on_input((0x10, self.WHEEL))                    # the G502's wheel turns
        self.devices.on_input(0x20)
        self.assertIsNone(self.devices.attribute(self.HWHEEL), "a vertical notch is no evidence for a horizontal one")
        self.assertEqual(self.devices.attribute(self.WHEEL).key, "usb:046d:c08b:G502 HERO")
        self.assertIsNone(self.devices.attribute(self.WHEEL), "a report counts for one notch")
        self.devices.on_input((0x10, self.HWHEEL))
        self.assertEqual(self.devices.attribute(self.HWHEEL).key, "usb:046d:c08b:G502 HERO")
        self.devices.on_input((0x10, self.WHEEL))
        now[0] += devices_win.MOUSE_EVIDENCE_S + 0.05
        self.assertIsNone(self.devices.attribute(self.WHEEL), "a report from too long ago is no evidence")

    def test_a_wheel_notch_is_a_mouses_when_its_report_is_still_in_the_queue(self) -> None:
        self.clock()
        self.devices.register(0xABC)
        self.api.dispatch = self.devices.on_input
        self.devices.on_input(0x20)
        self.api.queued.append((0x10, self.WHEEL))
        self.assertEqual(self.devices.attribute(self.WHEEL).kind, "mouse")
        self.assertEqual((self.api.queued, self.devices.drained), ([], 1))

    def test_without_raw_input_no_notch_is_judged(self) -> None:
        devices = RawInputDevices(None, unavailable="OSError: no user32")
        devices.on_input((0x10, self.WHEEL))
        self.assertIsNone(devices.attribute(self.WHEEL))

    def test_a_late_mouse_report_stands_in_only_for_a_mouse_that_reported_in_the_last_second(self) -> None:
        # The module notes: rule 2's wait for the mice to be still covers a
        # mouse click whose own report is missing (rule 1 finding nothing)
        # only when that mouse reported in the previous second.
        now = self.clock()
        self.devices.on_input(0x10)
        for _ in range(200):                                         # a palm rests on the touchpad
            self.devices.on_input(0x20)
            now[0] += 0.004
        self.assertLess(now[0] - 1000.0, devices_win.MOUSE_QUIET_S)  # (the mouse reported 0.8 s ago)
        self.assertEqual(self.devices.attribute(self.LEFT_DOWN).kind, "mouse", "its press, unreported: the mouse's")
        for _ in range(100):
            self.devices.on_input(0x20)
            now[0] += 0.004
        self.assertEqual(self.devices.attribute(self.LEFT_DOWN).kind, "trackpad",
                         "a mouse still for over a second, with its press unreported, is taken for the palm")

    def test_the_modules_notes_say_when_the_mouse_still_wait_holds(self) -> None:
        notes = " ".join(devices_win.__doc__.split())
        self.assertNotIn("whose report were late", notes)
        self.assertIn("only if that mouse reported in the previous second", notes)
        self.assertIn("The design rests on this order, not on rule 2", notes)
        self.assertIn("a click's WM_INPUT comes before the hook's call", notes)
        self.assertIn("read out by the drain", notes)

    def test_reports_still_waiting_in_the_queue_are_read_first(self) -> None:
        # The hook's call can be handled before a WM_INPUT already posted
        # for the same click (a sent message goes first): attribute() reads
        # the waiting ones out of the queue before it decides.
        self.clock()
        self.devices.register(0xABC)
        self.api.dispatch = self.devices.on_input
        self.devices.on_input(0x20)
        self.api.queued.append((0x10, self.LEFT_DOWN))
        self.assertEqual(self.devices.attribute(self.LEFT_DOWN).kind, "mouse")
        self.assertEqual((self.api.queued, self.devices.drained), ([], 1))
        unregistered = RawInputDevices(self.api, threaded=False)
        self.api.queued.append((0x10, self.LEFT_DOWN))
        unregistered.attribute(self.LEFT_DOWN)
        self.assertEqual(len(self.api.queued), 1, "no window of its own: nothing to read")

    def test_a_removed_devices_handle_is_looked_at_afresh(self) -> None:
        # Windows reuses a removed device's handle: the next device to get
        # it must not inherit the old one's kind, key or evidence.
        self.clock()
        self.devices.on_input((0x10, self.LEFT_DOWN))
        self.assertEqual(self.devices.current().kind, "mouse")
        self.devices.device_changed(devices_win.GIDC_ARRIVAL, 0x20)
        self.assertEqual(self.devices.current().kind, "mouse", "an arrival changes nothing for other handles")
        self.devices.device_changed(devices_win.GIDC_REMOVAL, 0x10)
        self.assertIsNone(self.devices.current())
        self.assertIsNone(self.devices.attribute(self.LEFT_DOWN), "its evidence went with it")
        self.api.devices[0x10] = self.api.devices[0x20]              # the touchpad now has handle 0x10
        self.devices.on_input(0x10)
        self.assertEqual(self.devices.current(), HandleInfo("trackpad", "hid:0000:0000:ELAN Touchpad", "ELAN Touchpad"))
        self.assertEqual(self.api.string_reads, [USB_MOUSE, I2C_TOUCHPAD], "named afresh")
        self.assertEqual((self.devices.arrivals, self.devices.removals), (1, 1))

    def test_a_late_report_of_a_removed_device_is_not_remembered(self) -> None:
        # The last WM_INPUT of an unplugged mouse can be read after its
        # GIDC_REMOVAL; its handle then names no path. That must not put the
        # dead handle back in the cache.
        self.clock()
        self.devices.on_input(0x10)
        self.devices.device_changed(devices_win.GIDC_REMOVAL, 0x10)
        self.api.gone.add(0x10)
        self.devices.on_input((0x10, self.LEFT_DOWN))                # read after the removal
        self.assertEqual(len(self.seen), 1, "no new device came of it")
        self.devices.on_input(0x20)                                  # ... and the touchpad reports
        self.api.gone.discard(0x10)
        self.api.devices[0x10] = self.api.devices[0x20]              # then the touchpad gets the handle
        self.devices.on_input(0x10)
        self.assertEqual(self.devices.current(), HandleInfo("trackpad", "hid:0000:0000:ELAN Touchpad", "ELAN Touchpad"))

    def test_a_handle_given_to_a_new_device_is_forgotten_on_its_arrival_too(self) -> None:
        # Whatever order Windows' messages come in, GIDC_ARRIVAL means the
        # handle is a new device's: nothing known of an earlier one stays.
        self.clock()
        self.devices.on_input((0x10, self.LEFT_DOWN))                # the G502, known, its press the evidence
        self.api.devices[0x10] = self.api.devices[0x20]              # its handle is the touchpad's now ...
        self.devices.device_changed(devices_win.GIDC_ARRIVAL, 0x10)  # ... and no removal was ever heard of
        self.assertIsNone(self.devices.current(), "the old device is not the one that reported last")
        self.assertIsNone(self.devices.attribute(self.LEFT_DOWN), "its evidence went with it")
        self.devices.on_input(0x10)
        self.assertEqual(self.devices.current().kind, "trackpad")
        self.assertEqual(self.api.string_reads, [USB_MOUSE, I2C_TOUCHPAD], "named afresh")
        self.assertEqual(self.devices.arrivals, 1)

    def test_a_touchpad_reusing_the_handle_of_a_removed_mouse_is_a_touchpad(self) -> None:
        # The whole sequence: removal, the dead mouse's last report, the
        # arrival of the touchpad that is given its handle.
        now = self.clock()
        self.devices.on_input(0x10)
        self.devices.device_changed(devices_win.GIDC_REMOVAL, 0x10)
        self.api.gone.add(0x10)
        self.devices.on_input((0x10, self.LEFT_DOWN))
        self.api.gone.discard(0x10)
        self.api.devices[0x10] = self.api.devices[0x20]
        self.devices.device_changed(devices_win.GIDC_ARRIVAL, 0x10)
        self.devices.on_input(0x10)
        self.assertEqual(self.devices.current().kind, "trackpad")
        now[0] += devices_win.MOUSE_QUIET_S + 0.1                    # the dead mouse's report is long past
        self.devices.on_input(0x10)
        self.assertEqual(self.devices.attribute(self.LEFT_DOWN).kind, "trackpad", "and its taps are a touch's")

    def test_names_read_for_a_handle_since_reused_are_dropped(self) -> None:
        results = threading.Semaphore(0)
        devices = RawInputDevices(self.api, wake=results.release, seen=self.seen.append)
        self.api.devices[0x11] = self.api.devices[0x10]              # (the fake finds names by path)
        devices.on_input(0x10)                                       # the mouse's names are being read ...
        devices.device_changed(devices_win.GIDC_REMOVAL, 0x10)       # ... as it is unplugged,
        self.api.devices[0x10] = self.api.devices[0x20]              # and the touchpad gets its handle
        devices.on_input(0x10)
        self.assertTrue(results.acquire(timeout=2) and results.acquire(timeout=2))
        devices.resolved()
        self.assertEqual(devices.current().key, "hid:0000:0000:ELAN Touchpad")
        self.assertEqual([info.key for info in self.seen], ["hid:0000:0000:ELAN Touchpad"], "the mouse's names went nowhere")
        devices.close()


@unittest.skipUnless(platform.system() == "Windows", "Windows Raw Input")
class RealRawInputTests(unittest.TestCase):
    def test_the_structures_are_the_sizes_windows_expects(self) -> None:
        import ctypes

        api = devices_win.Win32RawInput()
        self.assertEqual(ctypes.sizeof(api.RID_DEVICE_INFO), 32)
        header = 24 if ctypes.sizeof(ctypes.c_void_p) == 8 else 16
        self.assertEqual(ctypes.sizeof(api.RAWINPUTHEADER), header)
        # RAWINPUT with RAWMOUSE: the header, then usFlags, the ULONG-aligned
        # button union (usButtonFlags first), and four more ULONG/LONGs.
        self.assertEqual(ctypes.sizeof(api.RAWINPUTMOUSE), header + 24)
        self.assertEqual(api.RAWINPUTMOUSE.usButtonFlags.offset, header + 4)
        self.assertEqual(api.RAWINPUTMOUSE.ulExtraInformation.offset, header + 20)

    def test_every_pointing_device_here_can_be_named(self) -> None:
        import ctypes
        from ctypes import wintypes

        api = devices_win.Win32RawInput()

        class RAWINPUTDEVICELIST(ctypes.Structure):
            _fields_ = [("hDevice", wintypes.HANDLE), ("dwType", wintypes.DWORD)]

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        count = wintypes.UINT()
        user32.GetRawInputDeviceList(None, ctypes.byref(count), ctypes.sizeof(RAWINPUTDEVICELIST))
        listed = (RAWINPUTDEVICELIST * max(1, count.value))()
        found = user32.GetRawInputDeviceList(listed, ctypes.byref(count), ctypes.sizeof(RAWINPUTDEVICELIST))
        for entry in listed[: max(0, found)]:
            if entry.dwType not in (RIM_TYPEMOUSE, RIM_TYPEHID):
                continue
            path = api.device_path(entry.hDevice)
            self.assertTrue(path.startswith("\\\\?\\"), path)
            info = devices_win.parse_path(path)
            product, serial = api.strings(path)
            print(f"\n[device] {devices_win.device_key(info, serial, product)} from {path}", flush=True)


class MacDeviceTests(unittest.TestCase):
    # IOHIDDevice properties as this MacBook's registry gives them (an AULA
    # F75 keyboard over Bluetooth LE, the HP mouse, the internal trackpad),
    # with the classes IOKitRegistry.properties adds under "DriverClasses".
    TRACKPAD = {
        "Product": "Apple Internal Keyboard / Trackpad", "Transport": "FIFO", "VendorID": 0, "ProductID": 0,
        "DeviceUsagePairs": [{"DeviceUsagePage": 1, "DeviceUsage": 2}, {"DeviceUsagePage": 1, "DeviceUsage": 1},
                             {"DeviceUsagePage": 13, "DeviceUsage": 5}, {"DeviceUsagePage": 65280, "DeviceUsage": 12}],
        "PrimaryUsagePage": 1, "PrimaryUsage": 2,
        "DriverClasses": ["AppleHIDTransportHIDDevice", "IOHIDInterface", "AppleMultitouchTrackpadHIDEventDriver",
                          "AppleMultitouchDevice", "AppleMultitouchDeviceUserClient", "IOHIDLibUserClient"],
    }
    #: The keyboard half of the same machine: a device of its own.
    INTERNAL_KEYBOARD = {
        "Product": "Apple Internal Keyboard / Trackpad", "Transport": "FIFO", "VendorID": 0, "ProductID": 0,
        "DeviceUsagePairs": [{"DeviceUsagePage": 1, "DeviceUsage": 6}, {"DeviceUsagePage": 12, "DeviceUsage": 1},
                             {"DeviceUsagePage": 65280, "DeviceUsage": 6}],
        "PrimaryUsagePage": 1, "PrimaryUsage": 6,
        "DriverClasses": ["AppleHIDTransportHIDDevice", "IOHIDInterface", "AppleHIDKeyboardEventDriverV2",
                          "IOHIDEventServiceUserClient"],
    }
    MOUSE = {
        "Product": "HP 2.4G wireless and BT Mouse", "Transport": "Bluetooth Low Energy", "VendorID": 0x03F0,
        "ProductID": 0x0F4C, "SerialNumber": "a8-91-3d-00-11-22",
        "DeviceUsagePairs": [{"DeviceUsagePage": 1, "DeviceUsage": 2}],
        "PrimaryUsagePage": 1, "PrimaryUsage": 2,
        "DriverClasses": ["IOHIDUserDevice", "IOHIDInterface", "AppleUserHIDEventService"],
    }
    #: A keyboard whose usages include a pointer collection (the mouse keys
    #: and the media interface), first of all a keyboard.
    KEYBOARD = {
        "Product": "AULA-F75 5.0 KB", "Transport": "Bluetooth Low Energy", "VendorID": 0x3554, "ProductID": 0xFA07,
        "DeviceUsagePairs": [{"DeviceUsagePage": 1, "DeviceUsage": 6}, {"DeviceUsagePage": 65282, "DeviceUsage": 2},
                             {"DeviceUsagePage": 12, "DeviceUsage": 1}, {"DeviceUsagePage": 1, "DeviceUsage": 128},
                             {"DeviceUsagePage": 1, "DeviceUsage": 2}, {"DeviceUsagePage": 1, "DeviceUsage": 1}],
        "PrimaryUsagePage": 1, "PrimaryUsage": 6,
        "DriverClasses": ["IOHIDUserDevice", "IOHIDInterface", "AppleUserHIDEventService"],
    }

    def test_a_trackpad_by_its_driver(self) -> None:
        self.assertEqual(
            devices_mac.describe(self.TRACKPAD),
            MacDevice("fifo:0000:0000:Apple Internal Keyboard / Trackpad", "Apple Internal Keyboard / Trackpad", "trackpad"),
        )
        # An Apple trackpad lists the mouse usage first; its driver is what says what it is.
        without_touchpad = {**self.TRACKPAD, "DeviceUsagePairs": self.MOUSE["DeviceUsagePairs"]}
        self.assertEqual(devices_mac.describe(without_touchpad).kind, "trackpad")
        # A trackpad that is a keyboard's other half, as older MacBooks have it.
        combined = {**self.TRACKPAD, "PrimaryUsagePage": 1, "PrimaryUsage": 6}
        self.assertEqual(devices_mac.describe(combined).kind, "trackpad")
        # The Bluetooth Magic Trackpad's own device class.
        magic = {**self.MOUSE, "DriverClasses": ["BNBTrackpadDevice", "IOHIDInterface", "AppleUserHIDEventService"]}
        self.assertEqual(devices_mac.describe(magic).kind, "trackpad")

    def test_a_trackpad_by_its_usages_when_the_registry_gave_no_classes(self) -> None:
        # Primary usage "mouse" with a touchpad collection among its usages.
        without_classes = {key: value for key, value in self.TRACKPAD.items() if key != "DriverClasses"}
        self.assertEqual(devices_mac.describe(without_classes).kind, "trackpad")
        self.assertEqual(devices_mac.describe({**without_classes, "DriverClasses": None}).kind, "trackpad")
        self.assertEqual(devices_mac.describe({**without_classes, "DriverClasses": "garbage"}).kind, "trackpad")

    def test_a_touch_surface_alone_does_not_make_a_trackpad(self) -> None:
        # A Magic Mouse has a multitouch surface too; its button is a switch that bounces.
        mouse = {**self.MOUSE, "DriverClasses": ["BNBMouseDevice", "IOHIDInterface",
                                                 "AppleMultitouchMouseHIDEventDriver", "AppleMultitouchDevice"]}
        self.assertEqual(devices_mac.describe(mouse).kind, "mouse")

    def test_a_mouse_keyed_by_its_serial(self) -> None:
        self.assertEqual(devices_mac.describe(self.MOUSE).kind, "mouse")
        self.assertEqual(devices_mac.describe(self.MOUSE).key, "bt:03f0:0f4c:a8-91-3d-00-11-22")
        self.assertEqual(devices_mac.describe({**self.MOUSE, "SerialNumber": " "}).key,
                         "bt:03f0:0f4c:HP 2.4G wireless and BT Mouse")
        self.assertEqual(devices_mac.describe({**self.MOUSE, "Transport": "USB"}).key[:4], "usb:")

    def test_a_keyboard_that_lists_a_pointer_is_not_a_mouse(self) -> None:
        keyboard = devices_mac.describe(self.KEYBOARD)
        self.assertEqual(keyboard.kind, devices_mac.KEYBOARD_KIND)
        self.assertNotEqual(keyboard.kind, "mouse")
        self.assertEqual(keyboard.key, "bt:3554:fa07:AULA-F75 5.0 KB")
        self.assertEqual(devices_mac.describe(self.INTERNAL_KEYBOARD).kind, devices_mac.KEYBOARD_KIND)

    def test_the_primary_usage_comes_first_then_the_rest(self) -> None:
        kind = devices_mac.kind_from_usages
        pointer, keys = (0x01, 0x02), (0x01, 0x06)
        cases = [
            ("mouse", [pointer], (0x01, 0x02), "mouse"),
            ("the pointer usage", [], (0x01, 0x01), "mouse"),
            ("keyboard", [], keys, devices_mac.KEYBOARD_KIND),
            ("keyboard with a pointer collection", [keys, pointer, (0x01, 0x01)], keys, devices_mac.KEYBOARD_KIND),
            ("touchpad", [], (0x0D, 0x05), "trackpad"),
            ("touchpad that lists a pointer too", [pointer], (0x0D, 0x05), "trackpad"),
            ("touchscreen", [pointer], (0x0D, 0x04), "touchscreen"),
            ("pen", [pointer], (0x0D, 0x02), "pen"),
            ("digitizer", [pointer], (0x0D, 0x01), "pen"),
            ("mouse that lists a touchpad", [pointer, (0x0D, 0x05)], (0x01, 0x02), "trackpad"),
            ("consumer controls first: its pointer counts", [(0x0C, 0x01), pointer], (0x0C, 0x01), "mouse"),
            ("vendor page first: its touchscreen counts", [(0xFF00, 0x0C), (0x0D, 0x04)], (0xFF00, 0x0C), "touchscreen"),
            ("no primary: the touch surface outranks the pointer", [pointer, (0x0D, 0x04)], None, "touchscreen"),
            ("no primary, only a keyboard", [keys], None, "unknown"),
            ("nothing at all", [], None, "unknown"),
            ("vendor usages only", [(0xFF00, 0x0C)], (0xFF00, 0x0C), "unknown"),
        ]
        for name, pairs, primary, expected in cases:
            with self.subTest(name):
                self.assertEqual(kind(pairs, primary), expected)

    def test_what_little_some_devices_say(self) -> None:
        self.assertEqual(devices_mac.describe({}), MacDevice("hid:0000:0000:", "Unknown device", "unknown"))
        self.assertEqual(devices_mac.kind_from_usages([], (0x0D, 0x02)), "pen")
        self.assertEqual(devices_mac.kind_from_usages([(0x0D, 0x04), (0x01, 0x02)]), "touchscreen")
        self.assertEqual(devices_mac.bus_name("SPI"), "spi")
        self.assertEqual(devices_mac.bus_name("Bluetooth"), "bt")
        self.assertEqual(devices_mac.describe({"DeviceUsagePairs": [{"bad": 1}], "VendorID": "x"}).kind, "unknown")
        self.assertEqual(devices_mac.describe({"PrimaryUsagePage": "x", "PrimaryUsage": 2}).kind, "unknown")

    def test_every_kind_is_one_the_devices_page_names(self) -> None:
        from app.core import TOUCH_KINDS
        from app.ui.panes import KIND_NAMES

        self.assertLessEqual(devices_mac.KINDS, set(KIND_NAMES), "a kind the Devices page has no word for")
        self.assertIn(devices_mac.KEYBOARD_KIND, devices_mac.KINDS)
        # The touch kinds are the ones core never filters; the keyboard is not one of them.
        self.assertNotIn(devices_mac.KEYBOARD_KIND, TOUCH_KINDS)
        self.assertEqual(devices_mac.KINDS & TOUCH_KINDS, TOUCH_KINDS)
        for properties in (self.TRACKPAD, self.INTERNAL_KEYBOARD, self.MOUSE, self.KEYBOARD, {}):
            self.assertIn(devices_mac.describe(properties).kind, devices_mac.KINDS)

    def test_touch_subtypes(self) -> None:
        self.assertEqual(devices_mac.TOUCH_SUBTYPES, frozenset({1, 2, 3}))
        self.assertEqual((devices_mac.SENDER_ID_FIELD, devices_mac.SUBTYPE_FIELD), (87, 7))


class FakeRegistry:
    """IOKitRegistry's stand-in: devices with their properties and the IDs of
    the services below them, as connected_senders and resolve_sender ask."""

    def __init__(self, *devices) -> None:
        # (properties, service IDs below); an entry is its index plus one.
        self.devices = [(dict(properties), list(below)) for properties, below in devices]
        self.released: list = []

    def hid_devices(self) -> list:
        return list(range(1, len(self.devices) + 1))

    def properties(self, entry):
        return self.devices[entry - 1][0]

    def descendant_ids(self, entry):
        return self.devices[entry - 1][1]

    def entry_for_id(self, registry_id):
        for index, (_properties, below) in enumerate(self.devices, start=1):
            if registry_id in below:
                return index
        return 0

    def hid_device_above(self, entry):
        return entry

    def release(self, entry) -> None:
        self.released.append(entry)


class MacLookupTests(unittest.TestCase):
    """What a click's sender turns into, through the lookups the tap uses,
    over a registry like this MacBook's (a trackpad, a keyboard, a mouse)."""

    def setUp(self) -> None:
        self.registry = FakeRegistry(
            (MacDeviceTests.TRACKPAD, [0x100, 0x101, 0x102]),
            (MacDeviceTests.KEYBOARD, [0x200, 0x201]),
            (MacDeviceTests.MOUSE, [0x300, 0x301]),
            ({"Product": "BTM", "Transport": "SPMI", "PrimaryUsagePage": 65280, "PrimaryUsage": 72}, [0x400]),
        )

    def test_the_connected_pointing_devices_are_listed_ahead(self) -> None:
        found = devices_mac.connected_senders(self.registry)
        self.assertEqual({sender for sender, device in found.items() if device.kind == "trackpad"}, {0x100, 0x101, 0x102})
        self.assertEqual({sender for sender, device in found.items() if device.kind == "mouse"}, {0x300, 0x301})
        self.assertEqual(set(found) & {0x200, 0x201, 0x400}, set(), "a keyboard is not looked up ahead as a pointer")
        self.assertEqual(sorted(self.registry.released), [1, 2, 3, 4])

    def test_a_click_from_a_keyboards_pointer_is_still_a_pointing_device(self) -> None:
        # Looked up when it happens: filtered like any pointer, and not a mouse.
        device = devices_mac.resolve_sender(0x200, self.registry)
        self.assertEqual((device.key, device.kind), ("bt:3554:fa07:AULA-F75 5.0 KB", devices_mac.KEYBOARD_KIND))
        self.assertNotIn(device.kind, ("trackpad", "touchscreen", "pen"), "its clicks are filtered")

    def test_each_sender_resolves_to_its_own_device(self) -> None:
        for sender, kind in ((0x101, "trackpad"), (0x301, "mouse")):
            self.assertEqual(devices_mac.resolve_sender(sender, self.registry).kind, kind)
        self.assertIsNone(devices_mac.resolve_sender(0x999, self.registry))


class SenderCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.asked: list = []
        self.known = {7: MacDevice("usb:046d:c08b:G502", "G502", "mouse")}

        def resolve(sender):
            self.asked.append(sender)
            return self.known.get(sender)

        self.cache = SenderCache(resolve=resolve, connected=lambda: {9: MacDevice("bt:1:2:x", "x", "mouse")})

    def test_each_sender_is_looked_up_once_found_or_not(self) -> None:
        self.assertEqual(self.cache.lookup(7).name, "G502")
        self.assertIsNone(self.cache.lookup(8))
        self.cache.lookup(7)
        self.cache.lookup(8)
        self.assertIsNone(self.cache.lookup(0))
        self.assertEqual(self.asked, [7, 8])
        self.assertEqual(len(self.cache.lookup_seconds), 2)

    def test_warming_fills_the_cache_off_the_tap(self) -> None:
        self.cache.warm().join(2)
        self.assertEqual(self.cache.lookup(9).key, "bt:1:2:x")
        self.assertEqual(self.asked, [])

    def test_a_failing_lookup_is_an_unknown_device(self) -> None:
        def broken(_sender):
            raise OSError("IOKit said no")

        cache = SenderCache(resolve=broken, connected=dict)
        with self.assertLogs("app.devices_mac", "WARNING"):
            self.assertIsNone(cache.lookup(5))


@unittest.skipUnless(platform.system() == "Darwin", "IOKit")
class RealIOKitTests(unittest.TestCase):
    """Read-only registry lookups; nothing is posted."""

    def test_connected_devices_resolve_by_any_service_below_them(self) -> None:
        import time

        found = devices_mac.connected_senders()
        self.assertIsInstance(found, dict)
        self.assertIsNone(devices_mac.resolve_sender(0))
        self.assertIsNone(devices_mac.resolve_sender(1), "no such entry")
        if not found:
            self.skipTest("no pointing device here")
        sender, device = next(iter(found.items()))
        started = time.perf_counter()
        self.assertEqual(devices_mac.resolve_sender(sender), device)
        print(f"\n[iokit] {len(found)} senders; one lookup took {(time.perf_counter() - started) * 1000:.2f} ms",
              flush=True)


    def test_what_the_registry_says_each_device_is(self) -> None:
        registry = devices_mac._registry()
        if registry is None:
            self.skipTest("no IOKit")
        for entry in registry.hid_devices():
            try:
                properties = registry.properties(entry)
                device = devices_mac.describe(properties)
            finally:
                registry.release(entry)
            classes = properties["DriverClasses"]
            primary = (properties.get("PrimaryUsagePage"), properties.get("PrimaryUsage"))
            print(f"\n[iokit] {device.name!r} primary={primary[0]}/{primary[1]} -> {device.kind}", flush=True)
            self.assertIn(device.kind, devices_mac.KINDS)
            self.assertTrue(classes and all(isinstance(name, str) and name for name in classes), classes)
            if any("trackpad" in name.lower() for name in classes):
                self.assertEqual(device.kind, "trackpad")
            elif primary == (0x01, 0x06):
                self.assertEqual(device.kind, devices_mac.KEYBOARD_KIND, "a keyboard, whatever else it lists")
            elif primary == (0x01, 0x02):
                self.assertIn(device.kind, ("mouse", "trackpad"))


class FrontmostTests(unittest.TestCase):
    def test_windows_keys(self) -> None:
        self.assertEqual(frontmost.windows_key(r"C:\Program Files\Counter-Strike\CS2.EXE"), "cs2.exe")
        self.assertIsNone(frontmost.windows_key(""))

    def test_a_store_app_is_the_app_inside_its_frame(self) -> None:
        images = {1: "explorer.exe", 2: "applicationframehost.exe", 3: "calculatorapp.exe"}
        key = frontmost.foreground_key
        self.assertEqual(key(1, images.get, list), "explorer.exe")
        self.assertEqual(key(2, images.get, lambda: [2, 3]), "calculatorapp.exe")
        self.assertIsNone(key(2, images.get, list), "a frame with no app in it (suspended)")
        self.assertIsNone(key(99, images.get, list))

    def test_mac_keys(self) -> None:
        def app(identifier, executable):
            url = SimpleNamespace(lastPathComponent=lambda: executable) if executable else None
            return SimpleNamespace(bundleIdentifier=lambda: identifier, executableURL=lambda: url)

        self.assertEqual(frontmost.mac_app_key(app("com.valvesoftware.steam", "steam_osx")), "com.valvesoftware.steam")
        self.assertEqual(frontmost.mac_app_key(app(None, "unbundled-game")), "unbundled-game")
        self.assertIsNone(frontmost.mac_app_key(app(None, None)))
        self.assertIsNone(frontmost.mac_app_key(None))

    def test_a_watcher_stops_once(self) -> None:
        stops = []
        watcher = frontmost.Watcher(lambda: stops.append(1))
        watcher.stop()
        watcher.stop()
        self.assertEqual(stops, [1])

    @unittest.skipUnless(platform.system() in ("Darwin", "Windows"), "macOS or Windows")
    def test_the_real_app_in_front_can_be_read_and_watched(self) -> None:
        key = frontmost.current_app_key()
        self.assertTrue(key is None or (isinstance(key, str) and key), key)
        watcher = frontmost.watch(lambda _key: None)
        watcher.stop()
        print(f"\n[front] {key!r}", flush=True)


if __name__ == "__main__":
    unittest.main()
