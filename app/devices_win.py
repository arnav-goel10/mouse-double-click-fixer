"""Which device a Windows click came from (design section 1.5).

A low-level mouse hook doesn't say which device an event came from, so the
hook's thread also takes Raw Input (the method DoubleClickFix by nenning
uses, MIT): its hidden window registers, with RIDEV_INPUTSINK, for mice
(usage page 0x01, usage 0x02), precision touchpads (0x0D, 0x05) and
touchscreens (0x0D, 0x04), and each WM_INPUT names the device it came from.
Raw Input reaches the window as the device reports, so by the time a click
reaches the hook, the device that last reported is the one it came from: a
touchpad reports its touches before Windows turns a tap into a click.

Raw Input made by software (SendInput, and the mouse input Windows makes from
a touchpad's gestures) names no device (handle 0), and changes nothing.

A touch surface reports for as long as a finger is on it, so a click from it
always follows a report by moments. A touch device that has been quiet for
longer than TOUCH_QUIET_S is no longer taken for the one clicking: a mouse
clicked without being moved first may not have reported yet when its click
reaches the hook, and must not pass as a touch.

A device's kind: Raw Input of type HID is a digitizer (a touchpad, a
touchscreen or a pen, by its usage); of type mouse with absolute positions,
a touchscreen; otherwise a mouse. Its key (see device_key) comes from its
device path (vendor and product IDs, and the bus) and its HID product and
serial strings. Reading those strings asks the device itself, which can
block, so it is done on a thread of its own and the result handed back to
the hook's thread (see RawInputDevices.resolved); until then the device has
no key, and only its kind counts.
"""

from __future__ import annotations

import logging
import queue
import re
import threading
from dataclasses import dataclass
from time import monotonic
from typing import Callable, NamedTuple, Optional

from .core import TOUCH_KINDS

log = logging.getLogger(__name__)

WM_INPUT = 0x00FF
RIM_TYPEMOUSE, RIM_TYPEKEYBOARD, RIM_TYPEHID = 0, 1, 2
RID_HEADER, RID_INPUT = 0x10000005, 0x10000003
RIDI_DEVICENAME, RIDI_DEVICEINFO = 0x20000007, 0x2000000B
RIDEV_REMOVE, RIDEV_INPUTSINK = 0x00000001, 0x00000100
MOUSE_MOVE_ABSOLUTE = 0x0001
#: What the hook's window registers for: mice, touchpads, touchscreens.
USAGES = ((0x01, 0x02), (0x0D, 0x05), (0x0D, 0x04))
#: Digitizer usages (page 0x0D) and the kind of device they are.
DIGITIZER_KINDS = {0x05: "trackpad", 0x04: "touchscreen", 0x02: "pen", 0x01: "pen"}
#: How long after its last report a touch device still counts as the one
#: clicking (see the module notes).
TOUCH_QUIET_S = 1.0
#: Bluetooth's HID service class IDs, as they appear in a device path.
BLUETOOTH_SERVICES = ("{00001124-0000-1000-8000-00805F9B34FB}", "{00001812-0000-1000-8000-00805F9B34FB}")

_VID = re.compile(r"VID[_&]([0-9A-F]{4,8})", re.IGNORECASE)
_PID = re.compile(r"PID[_&]([0-9A-F]{4})", re.IGNORECASE)


class PathInfo(NamedTuple):
    bus: str
    vendor: int
    product: int
    #: The hardware ID part of the path ("VID_046D&PID_C52B&MI_01&Col01",
    #: "ACPI#PNP0F13"): what names a device with no IDs.
    hardware: str


@dataclass(frozen=True)
class HandleInfo:
    """What the hook knows of one Raw Input device handle."""

    kind: str
    key: Optional[str] = None
    name: str = ""


def parse_path(path: str) -> PathInfo:
    r"""Bus, vendor and product IDs from a device path such as
    \\?\HID#VID_046D&PID_C52B&MI_01&Col01#8&2f...#{378de44c-...} (USB),
    \\?\HID#{00001124-0000-1000-8000-00805f9b34fb}_VID&0002046d_PID&b023&Col01#...
    (Bluetooth) or \\?\ACPI#PNP0F13#4&...#{...} (a PS/2 mouse)."""
    text = path or ""
    upper = text.upper()
    parts = text.split("#")
    hardware = parts[1] if len(parts) > 1 else text
    if parts and parts[0].upper().endswith("ACPI") and len(parts) > 1:
        hardware = f"ACPI#{parts[1]}"
    vendor_match, product_match = _VID.search(text), _PID.search(text)
    vendor = int(vendor_match.group(1)[-4:], 16) if vendor_match else 0
    product = int(product_match.group(1), 16) if product_match else 0
    if any(service in upper for service in BLUETOOTH_SERVICES) or "BTHENUM" in upper or "BTHLE" in upper:
        bus = "bt"
    elif "ACPI#" in upper:
        bus = "acpi"
    elif vendor_match:
        bus = "usb"
    else:
        bus = "hid"
    return PathInfo(bus, vendor, product, hardware)


def device_key(info: PathInfo, serial: str = "", product: str = "") -> str:
    """The stable key: "usb:046d:c52b:<serial or product>". With neither,
    the hardware ID stands in, so a PS/2 mouse still has one."""
    ident = (serial or "").strip() or (product or "").strip() or info.hardware
    return f"{info.bus}:{info.vendor:04x}:{info.product:04x}:{ident}"


def kind_of(raw_type: int, mouse_flags: int = 0, usage_page: int = 0, usage: int = 0) -> str:
    if raw_type == RIM_TYPEHID:
        if usage_page == 0x0D:
            return DIGITIZER_KINDS.get(usage, "trackpad")
        return "unknown"
    if raw_type == RIM_TYPEMOUSE:
        return "touchscreen" if mouse_flags & MOUSE_MOVE_ABSOLUTE else "mouse"
    return "unknown"


def fallback_name(kind: str, info: PathInfo) -> str:
    noun = {"mouse": "Mouse", "trackpad": "Touchpad", "touchscreen": "Touchscreen", "pen": "Pen"}.get(kind, "Device")
    if info.vendor or info.product:
        return f"{noun} {info.vendor:04X}:{info.product:04X}"
    return f"{noun} ({info.hardware})"


class Win32RawInput:
    """The Raw Input and HID calls, set up once (on the hook's thread)."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._wintypes = wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class RAWINPUTDEVICE(ctypes.Structure):
            _fields_ = [
                ("usUsagePage", wintypes.USHORT),
                ("usUsage", wintypes.USHORT),
                ("dwFlags", wintypes.DWORD),
                ("hwndTarget", wintypes.HWND),
            ]

        class RAWINPUTHEADER(ctypes.Structure):
            _fields_ = [
                ("dwType", wintypes.DWORD),
                ("dwSize", wintypes.DWORD),
                ("hDevice", wintypes.HANDLE),
                ("wParam", wintypes.WPARAM),
            ]

        class RID_DEVICE_INFO_HID(ctypes.Structure):
            _fields_ = [
                ("dwVendorId", wintypes.DWORD),
                ("dwProductId", wintypes.DWORD),
                ("dwVersionNumber", wintypes.DWORD),
                ("usUsagePage", wintypes.USHORT),
                ("usUsage", wintypes.USHORT),
            ]

        class RID_DEVICE_INFO(ctypes.Structure):
            # The union's largest member is the keyboard's six DWORDs (24
            # bytes); the HID member is the first 16 of them.
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("dwType", wintypes.DWORD),
                ("hid", RID_DEVICE_INFO_HID),
                ("_rest", wintypes.DWORD * 2),
            ]

        self.RAWINPUTDEVICE, self.RAWINPUTHEADER, self.RID_DEVICE_INFO = RAWINPUTDEVICE, RAWINPUTHEADER, RID_DEVICE_INFO
        user32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT]
        user32.RegisterRawInputDevices.restype = wintypes.BOOL
        user32.GetRawInputData.argtypes = [
            wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p, ctypes.POINTER(wintypes.UINT), wintypes.UINT
        ]
        user32.GetRawInputData.restype = wintypes.UINT
        user32.GetRawInputDeviceInfoW.argtypes = [
            wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p, ctypes.POINTER(wintypes.UINT)
        ]
        user32.GetRawInputDeviceInfoW.restype = wintypes.UINT
        self.user32 = user32
        self._header = RAWINPUTHEADER()
        self._header_size = ctypes.sizeof(RAWINPUTHEADER)
        self._size = wintypes.UINT()
        self._get_data = user32.GetRawInputData
        self._hid = None

    # -- registration --------------------------------------------------------
    def register(self, hwnd, remove: bool = False) -> bool:
        devices = (self.RAWINPUTDEVICE * len(USAGES))()
        for index, (page, usage) in enumerate(USAGES):
            devices[index].usUsagePage = page
            devices[index].usUsage = usage
            devices[index].dwFlags = RIDEV_REMOVE if remove else RIDEV_INPUTSINK
            devices[index].hwndTarget = None if remove else hwnd
        ok = bool(self.user32.RegisterRawInputDevices(devices, len(USAGES), self._ctypes.sizeof(self.RAWINPUTDEVICE)))
        if not ok:
            log.warning(
                "Couldn't %s Raw Input (error %d)", "unregister" if remove else "register for", self._ctypes.get_last_error()
            )
        return ok

    # -- WM_INPUT --------------------------------------------------------------
    def handle_of(self, lparam) -> tuple[int, int]:
        """(device handle, Raw Input type) of a WM_INPUT; handle 0 when it
        names none or can't be read. The hot path: every report comes here."""
        size = self._size
        size.value = self._header_size
        header = self._header
        if self._get_data(lparam, RID_HEADER, self._ctypes.byref(header), self._ctypes.byref(size), self._header_size) == 0xFFFFFFFF:
            return 0, -1
        return int(header.hDevice or 0), int(header.dwType)

    def mouse_flags(self, lparam) -> int:
        """RAWMOUSE.usFlags of a WM_INPUT of type mouse: it follows the header."""
        ctypes = self._ctypes
        size = self._wintypes.UINT(0)
        self._get_data(lparam, RID_INPUT, None, ctypes.byref(size), self._header_size)
        if not size.value or size.value > 4096:
            return 0
        buffer = ctypes.create_string_buffer(size.value)
        if self._get_data(lparam, RID_INPUT, buffer, ctypes.byref(size), self._header_size) == 0xFFFFFFFF:
            return 0
        if size.value < self._header_size + 2:
            return 0
        return int.from_bytes(buffer.raw[self._header_size:self._header_size + 2], "little")

    # -- what a device is ------------------------------------------------------
    def device_path(self, handle: int) -> str:
        ctypes = self._ctypes
        size = self._wintypes.UINT(0)
        self.user32.GetRawInputDeviceInfoW(handle, RIDI_DEVICENAME, None, ctypes.byref(size))
        if not size.value or size.value > 4096:
            return ""
        buffer = ctypes.create_unicode_buffer(size.value + 1)
        if self.user32.GetRawInputDeviceInfoW(handle, RIDI_DEVICENAME, buffer, ctypes.byref(size)) in (0, 0xFFFFFFFF):
            return ""
        return buffer.value

    def hid_usage(self, handle: int) -> tuple[int, int]:
        info = self.RID_DEVICE_INFO()
        info.cbSize = self._ctypes.sizeof(info)
        size = self._wintypes.UINT(info.cbSize)
        if self.user32.GetRawInputDeviceInfoW(handle, RIDI_DEVICEINFO, self._ctypes.byref(info), self._ctypes.byref(size)) in (0, 0xFFFFFFFF):
            return 0, 0
        if info.dwType != RIM_TYPEHID:
            return 0, 0
        return int(info.hid.usUsagePage), int(info.hid.usUsage)

    def strings(self, path: str) -> tuple[str, str]:
        """(product, serial) from the HID device itself. Asks the device, so
        it may block: never call it on the hook's thread."""
        ctypes, wintypes = self._ctypes, self._wintypes
        if self._hid is None:
            hid = ctypes.WinDLL("hid", use_last_error=True)
            for name in ("HidD_GetProductString", "HidD_GetSerialNumberString"):
                function = getattr(hid, name)
                function.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
                function.restype = wintypes.BOOLEAN
            self.kernel32.CreateFileW.argtypes = [
                wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                wintypes.DWORD, wintypes.HANDLE,
            ]
            self.kernel32.CreateFileW.restype = wintypes.HANDLE
            self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            self._hid = hid
        share = 0x1 | 0x2  # FILE_SHARE_READ | FILE_SHARE_WRITE
        handle = self.kernel32.CreateFileW(path, 0, share, None, 3, 0, None)  # OPEN_EXISTING, no access needed
        if not handle or handle == wintypes.HANDLE(-1).value:
            return "", ""
        try:
            found = []
            for function in (self._hid.HidD_GetProductString, self._hid.HidD_GetSerialNumberString):
                buffer = ctypes.create_unicode_buffer(256)
                found.append(buffer.value.strip() if function(handle, buffer, ctypes.sizeof(buffer)) else "")
            return found[0], found[1]
        finally:
            self.kernel32.CloseHandle(handle)


class RawInputDevices:
    """The device each click comes from, kept on the hook's thread.

    on_input() takes every WM_INPUT; current() is the device that reported
    last. A handle seen for the first time is classified at once; its
    product and serial strings are read by a thread of its own, which hands
    them back through `wake` (a message to the hook's thread, which then
    calls resolved()). `seen(handle, info)` hears, on the hook's thread, of
    each device once its key is known."""

    def __init__(
        self,
        api: Optional[Win32RawInput] = None,
        wake: Callable[[], object] = lambda: None,
        seen: Callable[[HandleInfo], object] = lambda _info: None,
        threaded: bool = True,
    ) -> None:
        self._api = api
        self._wake = wake
        self._seen = seen
        self._threaded = threaded
        self._handles: dict[int, HandleInfo] = {}
        self._paths: dict[int, PathInfo] = {}
        self._current: Optional[HandleInfo] = None
        self._current_handle = 0
        # When the current device last reported (monotonic()).
        self._current_at = 0.0
        self._results: "queue.SimpleQueue" = queue.SimpleQueue()
        self._jobs: "queue.SimpleQueue" = queue.SimpleQueue()
        self._worker: Optional[threading.Thread] = None
        self.registered = False
        self._hwnd = None
        # Seconds each WM_INPUT took, when set to a list (end-to-end test).
        self.timings: Optional[list] = None

    def register(self, hwnd) -> bool:
        if self._api is None or not hwnd:
            return False
        self.registered = self._api.register(hwnd)
        self._hwnd = hwnd if self.registered else None
        return self.registered

    def close(self) -> None:
        if self.registered and self._api is not None:
            self._api.register(None, remove=True)
        self.registered = False
        if self._worker is not None:
            self._jobs.put(None)

    def current(self, now: Optional[float] = None) -> Optional[HandleInfo]:
        """The device that reported last, or None: none has, or it is a
        touch device that has been quiet too long to be the one clicking."""
        info = self._current
        if info is not None and info.kind in TOUCH_KINDS:
            if (monotonic() if now is None else now) - self._current_at > TOUCH_QUIET_S:
                return None
        return info

    def on_input(self, lparam) -> None:
        if self._api is None:
            return
        handle, raw_type = self._api.handle_of(lparam)
        if not handle:
            return
        self._current_at = monotonic()
        if handle == self._current_handle:
            return
        info = self._handles.get(handle)
        path = None
        if info is None:
            info, path = self._first_sight(handle, raw_type, lparam)
        self._current_handle = handle
        self._current = info
        if path:
            self._ask_names(handle, path)

    def _first_sight(self, handle: int, raw_type: int, lparam) -> tuple[HandleInfo, str]:
        api = self._api
        flags = api.mouse_flags(lparam) if raw_type == RIM_TYPEMOUSE else 0
        page, usage = api.hid_usage(handle) if raw_type == RIM_TYPEHID else (0, 0)
        kind = kind_of(raw_type, flags, page, usage)
        path = api.device_path(handle)
        path_info = parse_path(path)
        info = HandleInfo(kind, None, fallback_name(kind, path_info))
        if len(self._handles) > 128:  # handles of devices long gone
            self._handles.clear()
            self._paths.clear()
        self._handles[handle] = info
        self._paths[handle] = path_info
        return info, path

    def _ask_names(self, handle: int, path: str) -> None:
        if not self._threaded:
            self._results.put((handle, *self._read_strings(path)))
            self.resolved()
            return
        if self._worker is None:
            self._worker = threading.Thread(target=self._work, name="dcf-device-names", daemon=True)
            self._worker.start()
        self._jobs.put((handle, path))

    def _work(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            handle, path = job
            self._results.put((handle, *self._read_strings(path)))
            try:
                self._wake()
            except Exception:  # noqa: BLE001 - the next one wakes it
                log.warning("Couldn't hand a device's name back", exc_info=True)

    def _read_strings(self, path: str) -> tuple[str, str]:
        try:
            return self._api.strings(path)
        except Exception:  # noqa: BLE001 - the device keeps its fallback name
            log.warning("Couldn't read a device's name", exc_info=True)
            return "", ""

    def resolved(self) -> None:
        """On the hook's thread: take the names the worker has read."""
        while True:
            try:
                handle, product, serial = self._results.get_nowait()
            except queue.Empty:
                return
            info = self._handles.get(handle)
            path_info = self._paths.get(handle)
            if info is None or path_info is None:
                continue
            named = HandleInfo(info.kind, device_key(path_info, serial, product), product or info.name)
            self._handles[handle] = named
            if self._current_handle == handle:
                self._current = named
            try:
                self._seen(named)
            except Exception:  # noqa: BLE001 - never break the hook's thread
                log.warning("The device callback failed", exc_info=True)
