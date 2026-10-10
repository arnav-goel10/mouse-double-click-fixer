"""Which device a macOS click came from (design section 1.5).

A mouse event carries, in CGEvent field 87, the IORegistry entry ID of the
service that sent it: the HID event driver of a mouse, or the
AppleMultitouchDevice of a trackpad (verified on a MacBook's own trackpad).
The ID changes whenever the device reconnects, so it is only ever a cache
key: IOKit turns it into the IOHIDDevice above it, whose VendorID,
ProductID, Product, Transport and SerialNumber make the stable device key
(see device_key) and whose usages say what kind of device it is.

Field 7 (kCGMouseEventSubtype) says whether a click came from a tablet (1,
2) or a touch surface (3): those are never filtered, whatever the device.

IOKit is reached through ctypes (PyObjC has no IOKit bindings); property
values come back as CF objects and are read through PyObjC's toll-free
bridging. Looking one ID up costs about a millisecond (the registry match is
a kernel call), so senders are cached, and SenderCache.warm() looks up every
pointing device already connected on a thread of its own as the tap starts:
the tap callback then resolves only a device that arrives later, once.
"""

from __future__ import annotations

import ctypes
import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

log = logging.getLogger(__name__)

#: CGEvent field holding the sending service's IORegistry entry ID. Not in
#: Apple's headers; read from real events (scratchpad/hidstate).
SENDER_ID_FIELD = 87
#: CGEvent field kCGMouseEventSubtype, and its values that mean a tablet
#: point (1), tablet proximity (2) or a touch (3): never filtered.
SUBTYPE_FIELD = 7
TOUCH_SUBTYPES = frozenset({1, 2, 3})

#: How far up the IOService plane the IOHIDDevice may sit above the sender
#: (a trackpad's is three levels up: event driver, interface, device).
MAX_PARENT_STEPS = 8
#: Senders remembered at most; a reconnect makes a new one each time.
CACHE_LIMIT = 256

#: HID usages (page, usage) and the kind of device they make, in the order
#: they are looked for: a combined keyboard and trackpad is a trackpad.
KIND_BY_USAGE = (
    ((0x0D, 0x05), "trackpad"),
    ((0x0D, 0x04), "touchscreen"),
    ((0x0D, 0x02), "pen"),
    ((0x0D, 0x01), "pen"),
    ((0x01, 0x02), "mouse"),
    ((0x01, 0x01), "mouse"),
)

#: IOKit's Transport values and the bus name a key starts with.
BUS_BY_TRANSPORT = {"usb": "usb", "bluetooth": "bt", "bluetooth low energy": "bt", "bluetoothlowenergy": "bt"}


@dataclass(frozen=True)
class MacDevice:
    key: str
    name: str
    kind: str


def bus_name(transport: Optional[str]) -> str:
    text = (transport or "").strip().lower()
    if not text:
        return "hid"
    return BUS_BY_TRANSPORT.get(text, "".join(ch for ch in text if ch.isalnum()) or "hid")


def device_key(transport: Optional[str], vendor: Any, product_id: Any, serial: Optional[str], product: Optional[str]) -> str:
    """The stable key: "usb:046d:c52b:<serial or product>". The serial
    number when the device reports one, else its product name, so the key
    survives reconnects, other ports and reboots (design section 2.4)."""
    ident = (serial or "").strip() or (product or "").strip()
    return f"{bus_name(transport)}:{_hex4(vendor)}:{_hex4(product_id)}:{ident}"


def kind_from_usages(pairs: Iterable[tuple[int, int]], primary: Optional[tuple[int, int]] = None) -> str:
    """The kind of device that reports these HID usages."""
    found = {(int(page), int(usage)) for page, usage in pairs}
    if primary is not None:
        found.add((int(primary[0]), int(primary[1])))
    for usage, kind in KIND_BY_USAGE:
        if usage in found:
            return kind
    return "unknown"


def describe(properties: dict) -> Optional[MacDevice]:
    """A MacDevice from an IOHIDDevice's properties (VendorID, ProductID,
    Product, Transport, SerialNumber, DeviceUsagePairs, PrimaryUsagePage,
    PrimaryUsage), as Python values."""
    pairs = []
    for pair in properties.get("DeviceUsagePairs") or ():
        try:
            pairs.append((int(pair["DeviceUsagePage"]), int(pair["DeviceUsage"])))
        except (KeyError, TypeError, ValueError):
            continue
    primary = None
    if properties.get("PrimaryUsagePage") is not None and properties.get("PrimaryUsage") is not None:
        try:
            primary = (int(properties["PrimaryUsagePage"]), int(properties["PrimaryUsage"]))
        except (TypeError, ValueError):
            primary = None
    product = _text(properties.get("Product"))
    serial = _text(properties.get("SerialNumber"))
    transport = _text(properties.get("Transport"))
    key = device_key(transport, properties.get("VendorID"), properties.get("ProductID"), serial, product)
    return MacDevice(key, product or "Unknown device", kind_from_usages(pairs, primary))


def _hex4(value: Any) -> str:
    try:
        return f"{int(value) & 0xFFFF:04x}"
    except (TypeError, ValueError):
        return "0000"


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


# -- IOKit ----------------------------------------------------------------------

_PROPERTIES = (
    "VendorID", "ProductID", "Product", "Transport", "SerialNumber",
    "DeviceUsagePairs", "PrimaryUsagePage", "PrimaryUsage",
)


class IOKitRegistry:
    """The few IOKit calls a lookup makes. Thread-safe: IOKit and CF are."""

    def __init__(self) -> None:
        from ctypes import POINTER, c_char_p, c_int, c_uint32, c_uint64, c_void_p

        import objc

        self._objc = objc
        iokit = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        iokit.IORegistryEntryIDMatching.argtypes = [c_uint64]
        iokit.IORegistryEntryIDMatching.restype = c_void_p
        iokit.IOServiceGetMatchingService.argtypes = [c_uint32, c_void_p]
        iokit.IOServiceGetMatchingService.restype = c_uint32
        iokit.IOServiceMatching.argtypes = [c_char_p]
        iokit.IOServiceMatching.restype = c_void_p
        iokit.IOServiceGetMatchingServices.argtypes = [c_uint32, c_void_p, POINTER(c_uint32)]
        iokit.IOServiceGetMatchingServices.restype = c_int
        iokit.IOIteratorNext.argtypes = [c_uint32]
        iokit.IOIteratorNext.restype = c_uint32
        iokit.IORegistryEntryCreateIterator.argtypes = [c_uint32, c_char_p, c_uint32, POINTER(c_uint32)]
        iokit.IORegistryEntryCreateIterator.restype = c_int
        iokit.IORegistryEntryGetParentEntry.argtypes = [c_uint32, c_char_p, POINTER(c_uint32)]
        iokit.IORegistryEntryGetParentEntry.restype = c_int
        iokit.IORegistryEntryGetRegistryEntryID.argtypes = [c_uint32, POINTER(c_uint64)]
        iokit.IORegistryEntryGetRegistryEntryID.restype = c_int
        iokit.IOObjectConformsTo.argtypes = [c_uint32, c_char_p]
        iokit.IOObjectConformsTo.restype = c_uint32
        iokit.IORegistryEntryCreateCFProperty.argtypes = [c_uint32, c_void_p, c_void_p, c_uint32]
        iokit.IORegistryEntryCreateCFProperty.restype = c_void_p
        iokit.IOObjectRelease.argtypes = [c_uint32]
        iokit.IOObjectRelease.restype = c_int
        cf.CFStringCreateWithCString.argtypes = [c_void_p, c_char_p, c_uint32]
        cf.CFStringCreateWithCString.restype = c_void_p
        cf.CFRelease.argtypes = [c_void_p]
        self._iokit, self._cf = iokit, cf
        self._c_uint32, self._c_uint64 = c_uint32, c_uint64
        utf8 = 0x08000100  # kCFStringEncodingUTF8
        # Made once and kept for the life of the process.
        self._keys = {name: cf.CFStringCreateWithCString(None, name.encode(), utf8) for name in _PROPERTIES}

    # -- entries ---------------------------------------------------------------
    def entry_for_id(self, registry_id: int) -> int:
        """The registry entry with this ID (to release), or 0."""
        matching = self._iokit.IORegistryEntryIDMatching(registry_id & 0xFFFFFFFFFFFFFFFF)
        if not matching:
            return 0
        return int(self._iokit.IOServiceGetMatchingService(0, matching))  # consumes `matching`

    def hid_device_above(self, entry: int) -> int:
        """The IOHIDDevice at or above `entry` (to release), or 0. Releases
        `entry`."""
        for _ in range(MAX_PARENT_STEPS + 1):
            if self._iokit.IOObjectConformsTo(entry, b"IOHIDDevice"):
                return entry
            parent = self._c_uint32()
            status = self._iokit.IORegistryEntryGetParentEntry(entry, b"IOService", ctypes.byref(parent))
            self._iokit.IOObjectRelease(entry)
            if status != 0 or not parent.value:
                return 0
            entry = parent.value
        self._iokit.IOObjectRelease(entry)
        return 0

    def properties(self, entry: int) -> dict:
        found = {}
        for name, key in self._keys.items():
            ref = self._iokit.IORegistryEntryCreateCFProperty(entry, key, None, 0)
            if not ref:
                continue
            try:
                found[name] = _python_value(self._objc.objc_object(c_void_p=ref))
            finally:
                self._cf.CFRelease(ref)
        return found

    def release(self, entry: int) -> None:
        if entry:
            self._iokit.IOObjectRelease(entry)

    # -- every pointing device now ---------------------------------------------
    def hid_devices(self) -> list[int]:
        """Every IOHIDDevice entry (each to release)."""
        iterator = self._c_uint32()
        matching = self._iokit.IOServiceMatching(b"IOHIDDevice")
        if not matching or self._iokit.IOServiceGetMatchingServices(0, matching, ctypes.byref(iterator)) != 0:
            return []
        return self._drain(iterator.value)

    def descendant_ids(self, entry: int) -> list[int]:
        """The registry IDs of every service below `entry`."""
        iterator = self._c_uint32()
        recursive = 0x1  # kIORegistryIterateRecursively
        if self._iokit.IORegistryEntryCreateIterator(entry, b"IOService", recursive, ctypes.byref(iterator)) != 0:
            return []
        ids = []
        for child in self._drain(iterator.value):
            number = self._c_uint64()
            if self._iokit.IORegistryEntryGetRegistryEntryID(child, ctypes.byref(number)) == 0:
                ids.append(int(number.value))
            self._iokit.IOObjectRelease(child)
        return ids

    def _drain(self, iterator: int) -> list[int]:
        entries = []
        try:
            while True:
                entry = self._iokit.IOIteratorNext(iterator)
                if not entry:
                    return entries
                entries.append(int(entry))
        finally:
            self._iokit.IOObjectRelease(iterator)


def _python_value(value: Any) -> Any:
    """A bridged CF value as plain Python (NSNumber, NSString, NSArray and
    NSDictionary of them)."""
    if value is None:
        return None
    if isinstance(value, (bool, int, float, str)):
        return value
    try:
        from Foundation import NSArray, NSDictionary, NSNumber, NSString
    except ImportError:  # pragma: no cover - PyObjC without Foundation
        return value
    if isinstance(value, NSString):
        return str(value)
    if isinstance(value, NSNumber):
        return int(value) if float(value).is_integer() else float(value)
    if isinstance(value, NSDictionary):
        return {str(key): _python_value(value[key]) for key in value}
    if isinstance(value, NSArray):
        return [_python_value(item) for item in value]
    return value


def resolve_sender(registry_id: int, registry: Optional[IOKitRegistry] = None) -> Optional[MacDevice]:
    """The device whose service has this registry ID, or None if there is
    no such entry (it went away) or no IOHIDDevice above it."""
    if not registry_id:
        return None
    registry = registry or _registry()
    if registry is None:
        return None
    entry = registry.entry_for_id(int(registry_id))
    if not entry:
        return None
    device = registry.hid_device_above(entry)
    if not device:
        return None
    try:
        return describe(registry.properties(device))
    finally:
        registry.release(device)


def connected_senders(registry: Optional[IOKitRegistry] = None) -> dict[int, MacDevice]:
    """Every service below a pointing device connected now, by registry ID:
    whichever of them sends a click, its device is known."""
    registry = registry or _registry()
    if registry is None:
        return {}
    found: dict[int, MacDevice] = {}
    for entry in registry.hid_devices():
        try:
            device = describe(registry.properties(entry))
            if device is None or device.kind == "unknown":
                continue
            for registry_id in registry.descendant_ids(entry):
                found[registry_id] = device
        finally:
            registry.release(entry)
    return found


_registry_lock = threading.Lock()
_shared_registry: list = []
# Why IOKit couldn't be set up, if it couldn't (see lookup_status).
_registry_failure: list = []


def _registry() -> Optional[IOKitRegistry]:
    with _registry_lock:
        if not _shared_registry:
            try:
                _shared_registry.append(IOKitRegistry())
            except (OSError, ImportError, AttributeError) as error:
                log.warning("IOKit is unavailable: devices can't be told apart", exc_info=True)
                _shared_registry.append(None)
                _registry_failure.append(f"{type(error).__name__}: {error}")
        return _shared_registry[0]


def lookup_status() -> str:
    """Whether devices can be looked up, for the diagnostics: "iokit ok",
    or "iokit unavailable: <why>"."""
    if _registry() is not None:
        return "iokit ok"
    return f"iokit unavailable: {_registry_failure[0] if _registry_failure else 'unknown'}"


class SenderCache:
    """Sender ID to device, for the tap callback.

    lookup() answers from the cache; a sender it has never seen is looked up
    there and then (about a millisecond, once per device and connection),
    and a sender with no device is remembered as such. warm() fills the cache
    with every pointing device connected now, from a thread of its own, so
    the tap rarely has to.
    """

    def __init__(
        self,
        resolve: Callable[[int], Optional[MacDevice]] = resolve_sender,
        connected: Callable[[], dict] = connected_senders,
    ) -> None:
        self._resolve = resolve
        self._connected = connected
        self._known: dict[int, Optional[MacDevice]] = {}
        # The cost of each lookup the tap callback had to make, in seconds
        # (for the end-to-end test's report).
        self.lookup_seconds: list[float] = []

    def lookup(self, sender: int) -> Optional[MacDevice]:
        if not sender:
            return None
        try:
            return self._known[sender]
        except KeyError:
            pass
        from time import perf_counter

        started = perf_counter()
        try:
            device = self._resolve(sender)
        except Exception:  # noqa: BLE001 - an unknown device is filtered as before
            log.warning("Couldn't look up the device that sent a click", exc_info=True)
            device = None
        if len(self.lookup_seconds) < 64:
            self.lookup_seconds.append(perf_counter() - started)
        self._remember(sender, device)
        return device

    def warm(self) -> threading.Thread:
        thread = threading.Thread(target=self._warm, name="dcf-devices", daemon=True)
        thread.start()
        return thread

    def _warm(self) -> None:
        try:
            for sender, device in self._connected().items():
                self._known.setdefault(sender, device)
        except Exception:  # noqa: BLE001 - lookups still happen one by one
            log.warning("Couldn't list the connected pointing devices", exc_info=True)

    def _remember(self, sender: int, device: Optional[MacDevice]) -> None:
        if len(self._known) >= CACHE_LIMIT:
            self._known.clear()
        self._known[sender] = device
