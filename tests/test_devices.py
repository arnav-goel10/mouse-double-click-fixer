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

from app import devices_mac, devices_win, frontmost
from app.devices_mac import MacDevice, SenderCache
from app.devices_win import RIM_TYPEHID, RIM_TYPEMOUSE, HandleInfo, PathInfo, RawInputDevices

USB_MOUSE = r"\\?\HID#VID_046D&PID_C08B&MI_01&Col01#8&2f1c3a5&0&0000#{378de44c-56ef-11d1-bc8c-00a0c91405dd}"
BT_MOUSE = r"\\?\HID#{00001124-0000-1000-8000-00805f9b34fb}_VID&0002046d_PID&b014&Col01#8&1a2b3c&0&0000#{378de44c-56ef-11d1-bc8c-00a0c91405dd}"
BLE_MOUSE = r"\\?\HID#{00001812-0000-1000-8000-00805f9b34fb}&Dev&VID_045e&PID_0b13&REV_0517&e1a0c1b2c3d4&Col01#9&1&0000#{378de44c-56ef-11d1-bc8c-00a0c91405dd}"
PS2_MOUSE = r"\\?\ACPI#PNP0F13#4&1d4a2f&0#{378de44c-56ef-11d1-bc8c-00a0c91405dd}"
I2C_TOUCHPAD = r"\\?\HID#ELAN0001&Col02#5&3a1b2c&0&0001#{4d1e55b2-f16f-11cf-88cb-001111000030}"


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
        self.assertEqual(kind_of(RIM_TYPEHID, 0, 0x0D, 0x05), "trackpad")
        self.assertEqual(kind_of(RIM_TYPEHID, 0, 0x0D, 0x04), "touchscreen")
        self.assertEqual(kind_of(RIM_TYPEHID, 0, 0x0D, 0x02), "pen")
        self.assertEqual(kind_of(RIM_TYPEHID, 0, 0x01, 0x05), "unknown")
        self.assertEqual(kind_of(RIM_TYPEMOUSE, 0), "mouse")
        self.assertEqual(kind_of(RIM_TYPEMOUSE, devices_win.MOUSE_MOVE_ABSOLUTE), "touchscreen")

    def test_registers_for_mice_touchpads_and_touchscreens(self) -> None:
        self.assertEqual(devices_win.USAGES, ((0x01, 0x02), (0x0D, 0x05), (0x0D, 0x04)))
        self.assertEqual(devices_win.RIDEV_INPUTSINK, 0x100)


class FakeRawInput:
    """Win32RawInput's stand-in: a WM_INPUT's lparam is the device handle."""

    def __init__(self) -> None:
        self.devices = {
            0x10: (RIM_TYPEMOUSE, 0, (0, 0), USB_MOUSE, ("G502 HERO", "")),
            0x20: (RIM_TYPEHID, 0, (0x0D, 0x05), I2C_TOUCHPAD, ("ELAN Touchpad", "")),
            0x30: (RIM_TYPEMOUSE, devices_win.MOUSE_MOVE_ABSOLUTE, (0, 0), "", ("", "")),
        }
        self.registered: list = []
        self.string_reads: list = []
        self.read_flags: list = []

    def register(self, hwnd, remove=False):
        self.registered.append((hwnd, remove))
        return True

    def handle_of(self, lparam):
        if lparam == 0:
            return 0, RIM_TYPEMOUSE  # SendInput, or a touchpad's gestures turned into mouse input
        return lparam, self.devices[lparam][0]

    def mouse_flags(self, lparam):
        self.read_flags.append(lparam)
        return self.devices[lparam][1]

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

    def test_each_device_is_classified_and_named_once(self) -> None:
        for handle in (0x10, 0x10, 0x20, 0x10, 0x20):
            self.devices.on_input(handle)
        self.assertEqual(self.api.string_reads, [USB_MOUSE, I2C_TOUCHPAD])
        self.assertEqual(self.api.read_flags, [0x10], "a mouse's flags are read once")
        self.assertEqual([info.key for info in self.seen], ["usb:046d:c08b:G502 HERO", "hid:0000:0000:ELAN Touchpad"])

    def test_a_device_with_no_path_keeps_its_kind_and_no_key(self) -> None:
        self.devices.on_input(0x30)
        self.assertEqual(self.devices.current().kind, "touchscreen")
        self.assertIsNone(self.devices.current().key)
        self.assertEqual(self.seen, [])

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
        devices = RawInputDevices(None)
        self.assertFalse(devices.register(0xABC))
        devices.on_input(0x10)
        self.assertIsNone(devices.current())


@unittest.skipUnless(platform.system() == "Windows", "Windows Raw Input")
class RealRawInputTests(unittest.TestCase):
    def test_the_structures_are_the_sizes_windows_expects(self) -> None:
        import ctypes

        api = devices_win.Win32RawInput()
        self.assertEqual(ctypes.sizeof(api.RID_DEVICE_INFO), 32)
        self.assertEqual(ctypes.sizeof(api.RAWINPUTHEADER), 24 if ctypes.sizeof(ctypes.c_void_p) == 8 else 16)

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
