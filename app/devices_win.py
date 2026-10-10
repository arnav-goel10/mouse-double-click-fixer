"""Which device a Windows click came from (design section 1.5).

A low-level mouse hook doesn't say which device an event came from, so the
hook's thread also takes Raw Input (the method DoubleClickFix by nenning
uses, MIT): its hidden window registers, with RIDEV_INPUTSINK, for mice
(usage page 0x01, usage 0x02), precision touchpads (0x0D, 0x05) and
touchscreens (0x0D, 0x04), and each WM_INPUT names the device it came from.

Raw Input made by software (SendInput, and the mouse input Windows makes from
a touchpad's gestures) names no device (handle 0), and changes nothing.

Which device a click is taken to come from (RawInputDevices.attribute), in
this order, prefers a mouse's own evidence:

1. A mouse's report carrying this very transition (RI_MOUSE_LEFT_BUTTON_DOWN
   for a left press, RI_MOUSE_WHEEL for a wheel notch, ...) in the last
   MOUSE_EVIDENCE_S: that mouse. Each report counts for one event only. Raw
   Input reports buttons as the hand pressed them, the hook as the user's
   button swap makes them, so a left-handed user's left press is matched with
   the mouse's right button.
2. Otherwise a touch device (a precision touchpad or a touchscreen), but only
   if one reported in the last TOUCH_QUIET_S and no mouse sent any report,
   motion included, in the last MOUSE_QUIET_S.
3. Otherwise a mouse: the one that reported last, if it did within
   MOUSE_QUIET_S, or an unknown one. Unknown devices are filtered.

A wheel notch is judged by rule 1 alone: it is a mouse's only if a mouse's
report carries RI_MOUSE_WHEEL (vertical) or RI_MOUSE_HWHEEL (horizontal) for
that axis, within MOUSE_EVIDENCE_S. Otherwise attribute() says None, and the
wheel fix leaves the notch alone. Rules 2 and 3 would take it for the last
mouse's, and so judge a precision touchpad's two-finger scroll as that mouse's
wheel and drop its reversals: Windows makes that scroll from the gesture, as
wheel input that no mouse reported.

So a palm resting on a precision touchpad, which keeps it reporting, can't
take over an external mouse's clicks: the mouse's own reports win. A
touchpad tap, which carries no mouse report, passes as a touch when the
mice are still. A tap within MOUSE_QUIET_S of moving a mouse is filtered as
a mouse's click (rule 3): it reaches apps a window late, and of a
double-tap whose second tap comes within the window, the second is taken
for bounce. That is what rule 2's wait for the mice to be still costs.

Rule 1 rests on the order Windows delivers in, which CI measures
(tests/test_windows_hook.py, WindowsRawInputOrderTests): on the hook's own
thread, a click's WM_INPUT is posted before the hook is called, and is
normally handled tens of microseconds before the hook's callback runs; it
comes for a click the hook drops too. When the thread was busy, the WM_INPUT
can still be waiting in its queue as the callback runs (the hook's call is a
sent message, which the thread handles first), so the callback reads it out
first (Win32RawInput.drain). Nothing ever waits for evidence: a press is
decided at once.

The design rests on this order, not on rule 2: a click's WM_INPUT comes
before the hook's call, and what the thread had not yet handled is read out by
the drain. Rule 2's wait keeps a mouse click that came without a report of its
own from passing as a touch while a palm rests on the touchpad only if that
mouse reported in the previous second (MOUSE_QUIET_S; any report counts,
motion included): rule 3 then takes the click for that mouse's. A mouse that
has been still for longer, whose press report was missing, would pass as the
palm's touch.

A device's kind: Raw Input of type HID is a digitizer (a touchpad, a
touchscreen or a pen, by its usage); Raw Input of type mouse is a mouse,
whether it reports relative motion or absolute positions. A pointer with
absolute positions is no sign of a touch surface: virtual machines' pointers
(Hyper-V, VMware, VirtualBox, Parallels), the Remote Desktop mouse and IP-KVMs
report that way, and their buttons bounce like any other's. Touchscreens and
pens already pass on their own: their clicks carry the pen and touch
signature (MI_WP_SIGNATURE), and they report as digitizers (0x0D). Taken for
touchscreens, those mice would pass unfiltered with nothing to undo it, since
the user's list can only let a device through, never make one filtered. Its
key (see device_key) comes from its device path (vendor and product IDs, and
the bus) and its HID product and serial strings. Reading those strings asks
the device itself, which can block, so it is done on a thread of its own and
the result handed back to the hook's thread (see RawInputDevices.resolved);
until then the device has no key, and only its kind counts.

Windows hands out device handles per connection and reuses them, so the
window also registers with RIDEV_DEVNOTIFY: on WM_INPUT_DEVICE_CHANGE with
GIDC_REMOVAL or GIDC_ARRIVAL, what is known of that handle is forgotten, and
a device that later gets the same handle is looked at afresh. A removed
device's last WM_INPUT can still be read after its GIDC_REMOVAL; its handle
then names no path, and a handle with no path is never remembered.
"""

from __future__ import annotations

import logging
import queue
import re
import threading
from dataclasses import dataclass
from time import monotonic
from typing import Callable, NamedTuple, Optional

from .core import TOUCH_KINDS, Button

log = logging.getLogger(__name__)

WM_INPUT, WM_INPUT_DEVICE_CHANGE = 0x00FF, 0x00FE
GIDC_ARRIVAL, GIDC_REMOVAL = 1, 2
RIM_TYPEMOUSE, RIM_TYPEKEYBOARD, RIM_TYPEHID = 0, 1, 2
RID_HEADER, RID_INPUT = 0x10000005, 0x10000003
RIDI_DEVICENAME, RIDI_DEVICEINFO = 0x20000007, 0x2000000B
RIDEV_REMOVE, RIDEV_INPUTSINK, RIDEV_DEVNOTIFY = 0x00000001, 0x00000100, 0x00002000
#: PeekMessage: take the message out, and look at input only (QS_INPUT), so
#: no sent message is handled meanwhile (see Win32RawInput.drain).
PM_REMOVE, PM_QS_INPUT = 0x0001, 0x1C07 << 16
#: What the hook's window registers for: mice, touchpads, touchscreens.
USAGES = ((0x01, 0x02), (0x0D, 0x05), (0x0D, 0x04))
#: Digitizer usages (page 0x0D) and the kind of device they are.
DIGITIZER_KINDS = {0x05: "trackpad", 0x04: "touchscreen", 0x02: "pen", 0x01: "pen"}
#: RAWMOUSE.usButtonFlags. Buttons are numbered as the hand presses them (1
#: left, 2 right, 3 middle, 4 and 5 the side buttons), before any swap.
RI_MOUSE_BUTTON_DOWN = {1: 0x0001, 2: 0x0004, 3: 0x0010, 4: 0x0040, 5: 0x0100}
RI_MOUSE_BUTTON_UP = {1: 0x0002, 2: 0x0008, 3: 0x0020, 4: 0x0080, 5: 0x0200}
RI_MOUSE_WHEEL, RI_MOUSE_HWHEEL = 0x0400, 0x0800
#: Each of them on its own: a report's evidence (see the module notes).
TRANSITION_FLAGS = tuple(1 << bit for bit in range(12))
#: The reports whose evidence alone makes a notch a mouse's (see the module notes).
WHEEL_FLAGS = (RI_MOUSE_WHEEL, RI_MOUSE_HWHEEL)
#: Which physical button each of the app's buttons is when nothing is swapped.
PHYSICAL_BUTTONS = {Button.LEFT: 1, Button.RIGHT: 2, Button.MIDDLE: 3, Button.BACK: 4, Button.FORWARD: 5}
#: How recent a mouse's report of a transition must be to say a click is that
#: mouse's (rule 1 in the module notes). Its report comes moments before the
#: hook's call (see there); this leaves room for the hook's thread being busy.
MOUSE_EVIDENCE_S = 0.25
#: How long after its last report a touch device still counts as the one
#: clicking (rule 2).
TOUCH_QUIET_S = 1.0
#: How long after a mouse's last report, motion included, a click without a
#: mouse's evidence still counts as a mouse's (rules 2 and 3).
MOUSE_QUIET_S = 1.0
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


def kind_of(raw_type: int, usage_page: int = 0, usage: int = 0) -> str:
    """A device's kind from its Raw Input type and, for HID, its usage (see
    the module notes: a mouse is a mouse, absolute positions or not)."""
    if raw_type == RIM_TYPEHID:
        if usage_page == 0x0D:
            return DIGITIZER_KINDS.get(usage, "trackpad")
        return "unknown"
    if raw_type == RIM_TYPEMOUSE:
        return "mouse"
    return "unknown"


def fallback_name(kind: str, info: PathInfo) -> str:
    noun = {"mouse": "Mouse", "trackpad": "Touchpad", "touchscreen": "Touchscreen", "pen": "Pen"}.get(kind, "Device")
    if info.vendor or info.product:
        return f"{noun} {info.vendor:04X}:{info.product:04X}"
    return f"{noun} ({info.hardware})"


def _button_flags() -> dict:
    flags = {}
    for button, number in PHYSICAL_BUTTONS.items():
        for swapped in (False, True):
            hand = 3 - number if swapped and number in (1, 2) else number
            for pressed in (False, True):
                flags[(button, pressed, swapped)] = (RI_MOUSE_BUTTON_DOWN if pressed else RI_MOUSE_BUTTON_UP)[hand]
    return flags


_BUTTON_FLAGS = _button_flags()


def button_flag(button: Button, pressed: bool, swapped: bool = False) -> int:
    """The RAWMOUSE.usButtonFlags bit a mouse reports for this press or
    release, as the hook sees it: with the buttons swapped, the hook's left
    is the hand's right."""
    return _BUTTON_FLAGS[(button, pressed, bool(swapped))]


def wheel_flag(axis: int) -> int:
    """The bit a mouse reports for a notch of its wheel: 1 vertical, 2 horizontal."""
    return RI_MOUSE_HWHEEL if axis == 2 else RI_MOUSE_WHEEL


class Win32RawInput:
    """The Raw Input and HID calls, set up once (on the hook's thread)."""

    #: Bytes of each report read: a mouse's is 48 (40 in a 32-bit process); a
    #: precision touchpad's a few dozen more per finger.
    READ_SIZE = 512

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

        class RAWINPUTMOUSE(ctypes.Structure):
            # A RAWINPUT holding a mouse's report, flat: RAWINPUTHEADER, then
            # RAWMOUSE, whose button union is ULONG-aligned (hence the pad).
            _fields_ = [
                ("dwType", wintypes.DWORD),
                ("dwSize", wintypes.DWORD),
                ("hDevice", wintypes.HANDLE),
                ("wParam", wintypes.WPARAM),
                ("usFlags", wintypes.USHORT),
                ("_pad", wintypes.USHORT),
                ("usButtonFlags", wintypes.USHORT),
                ("usButtonData", wintypes.USHORT),
                ("ulRawButtons", wintypes.ULONG),
                ("lLastX", wintypes.LONG),
                ("lLastY", wintypes.LONG),
                ("ulExtraInformation", wintypes.ULONG),
            ]

        self.RAWINPUTDEVICE, self.RAWINPUTHEADER, self.RID_DEVICE_INFO = RAWINPUTDEVICE, RAWINPUTHEADER, RID_DEVICE_INFO
        self.RAWINPUTMOUSE = RAWINPUTMOUSE
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
        user32.PeekMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT
        ]
        user32.PeekMessageW.restype = wintypes.BOOL
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        self.user32 = user32
        self._header = RAWINPUTHEADER()
        self._header_size = ctypes.sizeof(RAWINPUTHEADER)
        self._size = wintypes.UINT()
        self._get_data = user32.GetRawInputData
        # One report at a time is read into this buffer: a mouse's whole
        # (RAWINPUTMOUSE), or the start of a touchpad's, seen through
        # RAWINPUTMOUSE for its header only.
        self._buffer = (ctypes.c_ubyte * self.READ_SIZE)()
        self._buffer_address = ctypes.addressof(self._buffer)
        self._report = RAWINPUTMOUSE.from_buffer(self._buffer)
        self._message = wintypes.MSG()
        self._peek = user32.PeekMessageW
        self._dispatch = user32.DispatchMessageW
        # The error RegisterRawInputDevices last gave, for the diagnostics.
        self.error = 0
        self._hid = None

    # -- registration --------------------------------------------------------
    def register(self, hwnd, remove: bool = False) -> bool:
        devices = (self.RAWINPUTDEVICE * len(USAGES))()
        for index, (page, usage) in enumerate(USAGES):
            devices[index].usUsagePage = page
            devices[index].usUsage = usage
            # With RIDEV_DEVNOTIFY, the window also hears devices come and
            # go (WM_INPUT_DEVICE_CHANGE): their handles are reused.
            devices[index].dwFlags = RIDEV_REMOVE if remove else RIDEV_INPUTSINK | RIDEV_DEVNOTIFY
            devices[index].hwndTarget = None if remove else hwnd
        ok = bool(self.user32.RegisterRawInputDevices(devices, len(USAGES), self._ctypes.sizeof(self.RAWINPUTDEVICE)))
        if not ok:
            self.error = self._ctypes.get_last_error()
            log.warning("Couldn't %s Raw Input (error %d)", "unregister" if remove else "register for", self.error)
        return ok

    # -- WM_INPUT --------------------------------------------------------------
    def read(self, lparam) -> tuple[int, int, int]:
        """(device handle, Raw Input type, RAWMOUSE.usButtonFlags) of a
        WM_INPUT; handle 0 when it names none or can't be read, and no
        button flags but a mouse's. The hot path: every report comes here."""
        size = self._size
        size.value = self.READ_SIZE
        if self._get_data(lparam, RID_INPUT, self._buffer_address, self._ctypes.byref(size), self._header_size) == 0xFFFFFFFF:
            # Bigger than the buffer (a touchpad with many fingers down):
            # its header is all that is needed of it.
            handle, raw_type = self.handle_of(lparam)
            return handle, raw_type, 0
        report = self._report
        raw_type = report.dwType
        return report.hDevice or 0, raw_type, report.usButtonFlags if raw_type == RIM_TYPEMOUSE else 0

    def handle_of(self, lparam) -> tuple[int, int]:
        """(device handle, Raw Input type) of a WM_INPUT; handle 0 when it
        names none or can't be read."""
        size = self._size
        size.value = self._header_size
        header = self._header
        if self._get_data(lparam, RID_HEADER, self._ctypes.byref(header), self._ctypes.byref(size), self._header_size) == 0xFFFFFFFF:
            return 0, -1
        return int(header.hDevice or 0), int(header.dwType)

    def drain(self, hwnd) -> int:
        """Handle, now, every WM_INPUT for `hwnd` still waiting in this
        thread's queue, as the thread's message loop would have (it goes to
        the window, which reads it and lets DefWindowProc free it). The hook
        calls this as it decides a click, from inside its callback: Windows
        posts a click's WM_INPUT before it calls the hook, but the hook's
        call, a sent message, is handled first when both are waiting.
        PM_QS_INPUT looks at input only, so no sent message (and so no call
        of the hook) is handled in here. Returns how many there were."""
        count = 0
        message = self._message
        reference = self._ctypes.byref(message)
        while self._peek(reference, hwnd, WM_INPUT, WM_INPUT, PM_REMOVE | PM_QS_INPUT):
            self._dispatch(reference)
            count += 1
        return count

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

    on_input() takes every WM_INPUT, and attribute() says which device a
    click or wheel notch the hook is deciding came from (see the module
    notes); current() is the device that reported last. A handle seen for
    the first time is classified at once; its product and serial strings are
    read by a thread of its own, which hands them back through `wake` (a
    message to the hook's thread, which then calls resolved()). `seen(info)`
    hears, on the hook's thread, of each device once its key is known.
    device_changed() takes WM_INPUT_DEVICE_CHANGE. `status` says whether Raw
    Input works, for the diagnostics."""

    def __init__(
        self,
        api: Optional[Win32RawInput] = None,
        wake: Callable[[], object] = lambda: None,
        seen: Callable[[HandleInfo], object] = lambda _info: None,
        threaded: bool = True,
        unavailable: str = "",
    ) -> None:
        self._api = api
        self._wake = wake
        self._seen = seen
        self._threaded = threaded
        self._handles: dict[int, HandleInfo] = {}
        # Per handle: its device path, and what it says.
        self._paths: dict[int, tuple[str, PathInfo]] = {}
        self._current: Optional[HandleInfo] = None
        self._current_handle = 0
        # When the current device last reported (monotonic()).
        self._current_at = 0.0
        # The evidence attribute() weighs, all on monotonic()'s clock: when
        # a mouse and when a touch device last reported, and which; and per
        # transition bit (TRANSITION_FLAGS), when a mouse last reported it,
        # and which mouse, until a click or notch takes it.
        self._mouse_at = self._touch_at = float("-inf")
        self._mouse_handle = self._touch_handle = 0
        self._evidence: dict[int, tuple[float, int]] = {}
        self._results: "queue.SimpleQueue" = queue.SimpleQueue()
        self._jobs: "queue.SimpleQueue" = queue.SimpleQueue()
        self._worker: Optional[threading.Thread] = None
        self.registered = False
        self._hwnd = None
        #: "raw input ok", or "raw input unavailable: <why>".
        self.status = f"raw input unavailable: {unavailable or 'not registered yet'}"
        #: WM_INPUT read out of the queue while the hook decided (see drain).
        self.drained = 0
        #: Devices Windows said were connected and removed (WM_INPUT_DEVICE_CHANGE).
        self.arrivals = self.removals = 0

    def register(self, hwnd) -> bool:
        if self._api is None:
            return False
        if not hwnd:
            self.status = "raw input unavailable: the hook's window couldn't be created"
            return False
        self.registered = self._api.register(hwnd)
        self._hwnd = hwnd if self.registered else None
        if self.registered:
            self.status = "raw input ok"
        else:
            self.status = f"raw input unavailable: RegisterRawInputDevices failed (error {getattr(self._api, 'error', 0)})"
        return self.registered

    def close(self) -> None:
        if self.registered and self._api is not None:
            self._api.register(None, remove=True)
        self.registered = False
        self._hwnd = None
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

    def attribute(self, flag: int, now: Optional[float] = None) -> Optional[HandleInfo]:
        """The device a press, release or wheel notch the hook is deciding
        now came from, by the rules in the module notes; `flag` is the bit
        a mouse reports for it (button_flag, wheel_flag). None for an
        unknown device, which is filtered; for a wheel notch, None is a notch
        no mouse reported, which is not judged at all. Never waits."""
        self.drain()
        now = monotonic() if now is None else now
        evidence = self._evidence.pop(flag, None)
        if evidence is not None and now - evidence[0] <= MOUSE_EVIDENCE_S:
            info = self._handles.get(evidence[1])
            if info is not None:
                return info
        if flag in WHEEL_FLAGS:
            return None
        mouse_recent = now - self._mouse_at <= MOUSE_QUIET_S
        if not mouse_recent and now - self._touch_at <= TOUCH_QUIET_S:
            info = self._handles.get(self._touch_handle)
            if info is not None:
                return info
        return self._handles.get(self._mouse_handle) if mouse_recent else None

    def drain(self) -> None:
        """Read the reports still waiting in the hook thread's queue (see
        Win32RawInput.drain): on the hook's thread, before attributing."""
        if self._hwnd is None:
            return
        try:
            self.drained += self._api.drain(self._hwnd)
        except Exception:  # noqa: BLE001 - attribute on what has been read
            log.warning("Couldn't read the waiting Raw Input", exc_info=True)

    def on_input(self, lparam) -> None:
        if self._api is None:
            return
        handle, raw_type, buttons = self._api.read(lparam)
        if not handle:
            return
        now = self._current_at = monotonic()
        # A handle with no path isn't remembered (see _first_sight), so it
        # is looked at again at each of its reports until it has one.
        if handle == self._current_handle and handle in self._handles:
            info = self._current
        else:
            info = self._handles.get(handle)
            path = None
            if info is None:
                info, path = self._first_sight(handle, raw_type)
            self._current_handle = handle
            self._current = info
            if path:
                self._ask_names(handle, path)
        if info.kind in TOUCH_KINDS:
            self._touch_at, self._touch_handle = now, handle
        elif raw_type == RIM_TYPEMOUSE:
            self._mouse_at, self._mouse_handle = now, handle
            if buttons:
                for flag in TRANSITION_FLAGS:
                    if buttons & flag:
                        self._evidence[flag] = (now, handle)

    def device_changed(self, change: int, handle: int) -> None:
        """WM_INPUT_DEVICE_CHANGE, on the hook's thread. A removed device's
        handle may be given to the next device connected: everything known
        of it is forgotten when the device goes and again when one arrives,
        so that one is looked at afresh. (The removed device's last report
        can be read after its removal; the arrival is what ends whatever
        that left behind.)"""
        if change == GIDC_ARRIVAL:
            self.arrivals += 1
        elif change == GIDC_REMOVAL and handle:
            self.removals += 1
        else:
            return
        if handle:
            self._forget(int(handle))

    def _forget(self, handle: int) -> None:
        self._handles.pop(handle, None)
        self._paths.pop(handle, None)
        if self._current_handle == handle:
            self._current_handle, self._current = 0, None
        if self._mouse_handle == handle:
            self._mouse_handle, self._mouse_at = 0, float("-inf")
        if self._touch_handle == handle:
            self._touch_handle, self._touch_at = 0, float("-inf")
        for flag in [flag for flag, (_at, owner) in self._evidence.items() if owner == handle]:
            del self._evidence[flag]

    def _first_sight(self, handle: int, raw_type: int) -> tuple[HandleInfo, str]:
        api = self._api
        page, usage = api.hid_usage(handle) if raw_type == RIM_TYPEHID else (0, 0)
        kind = kind_of(raw_type, page, usage)
        path = api.device_path(handle)
        path_info = parse_path(path)
        info = HandleInfo(kind, None, fallback_name(kind, path_info))
        if not path:
            # A removed device's last report can still be read after its
            # GIDC_REMOVAL, and its handle then names no path (nor anything
            # else): it must not be remembered, or the next device to be
            # given the handle would be taken for it. (A read that failed
            # for a moment is asked again at the next report.)
            return info, ""
        if len(self._handles) > 128:  # handles of devices long gone
            self._handles.clear()
            self._paths.clear()
        self._handles[handle] = info
        self._paths[handle] = (path, path_info)
        return info, path

    def _ask_names(self, handle: int, path: str) -> None:
        if not self._threaded:
            self._results.put((handle, path, *self._read_strings(path)))
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
            self._results.put((handle, path, *self._read_strings(path)))
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
        """On the hook's thread: take the names the worker has read. Names
        read for a handle that has since gone to another device are not
        that device's, and are dropped (it is being read afresh)."""
        while True:
            try:
                handle, path, product, serial = self._results.get_nowait()
            except queue.Empty:
                return
            info = self._handles.get(handle)
            known = self._paths.get(handle)
            if info is None or known is None or known[0] != path:
                continue
            named = HandleInfo(info.kind, device_key(known[1], serial, product), product or info.name)
            self._handles[handle] = named
            if self._current_handle == handle:
                self._current = named
            try:
                self._seen(named)
            except Exception:  # noqa: BLE001 - never break the hook's thread
                log.warning("The device callback failed", exc_info=True)
