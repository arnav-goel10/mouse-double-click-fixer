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


class FakeRawInput:
    """Win32RawInput's stand-in: a WM_INPUT's lparam is the device handle.

    Each device is (Raw Input type, whether its reports carry absolute
    positions, HID usage, path, (product, serial)). Whether a mouse reports
    absolute positions is what Windows would say of it; nothing may take it
    for a sign of a touch surface (see devices_win's notes), so no call here
    gives it out: a test only says what the device is."""

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

    def register(self, hwnd, remove=False):
        self.registered.append((hwnd, remove))
        return True

    def handle_of(self, lparam):
        if lparam == 0:
            return 0, RIM_TYPEMOUSE  # SendInput, or a touchpad's gestures turned into mouse input
        return lparam, self.devices[lparam][0]

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
