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
    hands each to `dispatch`, as DispatchMessage hands it to the window."""

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
        return self.devices[handle][2]

    def device_path(self, handle):
        return self.devices[handle][3]

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
        self.devices.device_changed(devices_win.GIDC_ARRIVAL, 0x10)
        self.assertEqual(self.devices.current().kind, "mouse", "an arrival changes nothing")
        self.devices.device_changed(devices_win.GIDC_REMOVAL, 0x10)
        self.assertIsNone(self.devices.current())
        self.assertIsNone(self.devices.attribute(self.LEFT_DOWN), "its evidence went with it")
        self.api.devices[0x10] = self.api.devices[0x20]              # the touchpad now has handle 0x10
        self.devices.on_input(0x10)
        self.assertEqual(self.devices.current(), HandleInfo("trackpad", "hid:0000:0000:ELAN Touchpad", "ELAN Touchpad"))
        self.assertEqual(self.api.string_reads, [USB_MOUSE, I2C_TOUCHPAD], "named afresh")
        self.assertEqual((self.devices.arrivals, self.devices.removals), (1, 1))

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
    TRACKPAD = {
        "Product": "Apple Internal Keyboard / Trackpad", "Transport": "FIFO", "VendorID": 0, "ProductID": 0,
        "DeviceUsagePairs": [{"DeviceUsagePage": 1, "DeviceUsage": 2}, {"DeviceUsagePage": 1, "DeviceUsage": 1},
                             {"DeviceUsagePage": 13, "DeviceUsage": 5}, {"DeviceUsagePage": 65280, "DeviceUsage": 12}],
        "PrimaryUsagePage": 1, "PrimaryUsage": 2,
    }
    MOUSE = {
        "Product": "HP 2.4G wireless and BT Mouse", "Transport": "Bluetooth Low Energy", "VendorID": 0x03F0,
        "ProductID": 0x0F4C, "SerialNumber": "a8-91-3d-00-11-22",
        "DeviceUsagePairs": [{"DeviceUsagePage": 1, "DeviceUsage": 2}],
    }

    def test_a_trackpad_by_its_usages(self) -> None:
        self.assertEqual(
            devices_mac.describe(self.TRACKPAD),
            MacDevice("fifo:0000:0000:Apple Internal Keyboard / Trackpad", "Apple Internal Keyboard / Trackpad", "trackpad"),
        )

    def test_a_mouse_keyed_by_its_serial(self) -> None:
        self.assertEqual(devices_mac.describe(self.MOUSE).key, "bt:03f0:0f4c:a8-91-3d-00-11-22")
        self.assertEqual(devices_mac.describe({**self.MOUSE, "SerialNumber": " "}).key,
                         "bt:03f0:0f4c:HP 2.4G wireless and BT Mouse")
        self.assertEqual(devices_mac.describe({**self.MOUSE, "Transport": "USB"}).key[:4], "usb:")

    def test_what_little_some_devices_say(self) -> None:
        self.assertEqual(devices_mac.describe({}), MacDevice("hid:0000:0000:", "Unknown device", "unknown"))
        self.assertEqual(devices_mac.kind_from_usages([], (0x0D, 0x02)), "pen")
        self.assertEqual(devices_mac.kind_from_usages([(0x0D, 0x04), (0x01, 0x02)]), "touchscreen")
        self.assertEqual(devices_mac.bus_name("SPI"), "spi")
        self.assertEqual(devices_mac.bus_name("Bluetooth"), "bt")
        self.assertEqual(devices_mac.describe({"DeviceUsagePairs": [{"bad": 1}], "VendorID": "x"}).kind, "unknown")

    def test_touch_subtypes(self) -> None:
        self.assertEqual(devices_mac.TOUCH_SUBTYPES, frozenset({1, 2, 3}))
        self.assertEqual((devices_mac.SENDER_ID_FIELD, devices_mac.SUBTYPE_FIELD), (87, 7))


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
