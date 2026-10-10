"""macOS Accessibility permission checks.

A filtering event tap is only allowed for a trusted process, so the UI needs to
know the answer before it offers to turn the filter on, and needs a way to get
the user to the right switch.
"""

from __future__ import annotations

import ctypes
import os
import platform
import subprocess
from functools import lru_cache

ACCESSIBILITY_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
_SERVICES = "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
_GRAPHICS = "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
_FOUNDATION = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"

#: macOS 27 renamed the Accessibility list in Privacy & Security.
PANE_NAME = "Accessibility"
PANE_NAME_27 = "Device Control and Data Access"

# CGEventTapCreate arguments for the probe (CGEventTypes.h).
_SESSION_EVENT_TAP = 1
_TAIL_APPEND_EVENT_TAP = 1
_TAP_OPTION_DEFAULT = 0
_TABLET_PROXIMITY = 24

_TAP_CALLBACK = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p)


@_TAP_CALLBACK
def _pass_through(_proxy, _event_type, event, _refcon):
    return event


def needs_accessibility() -> bool:
    """True on macOS, where the system-wide filter requires permission."""
    return platform.system() == "Darwin"


def macos_major() -> int:
    """The running macOS major version, or 0 elsewhere.

    An app built against an older SDK is told 10.16 or 16 instead of the
    real number, so that answer falls back to the kernel, whose version is
    never disguised: Darwin 20-24 are macOS 11-15, Darwin 25 is macOS 26,
    and from macOS 27 on the two share their number.
    """
    if not needs_accessibility():
        return 0
    try:
        major = int(platform.mac_ver()[0].split(".")[0])
    except ValueError:
        major = 0
    if major not in (0, 10, 16):
        return major
    try:
        darwin = int(os.uname().release.split(".")[0])
    except (ValueError, AttributeError):  # AttributeError: no uname (Windows, under tests)
        return major
    if darwin >= 26:
        return max(darwin, 27)
    if darwin == 25:
        return 26
    return darwin - 9 if darwin >= 20 else major


def pane_name() -> str:
    """What System Settings calls the list this app has to be allowed in."""
    return PANE_NAME_27 if macos_major() >= 27 else PANE_NAME


@lru_cache(maxsize=1)
def _services():
    library = ctypes.cdll.LoadLibrary(_SERVICES)
    library.AXIsProcessTrusted.restype = ctypes.c_bool
    library.AXIsProcessTrustedWithOptions.argtypes = [ctypes.c_void_p]
    library.AXIsProcessTrustedWithOptions.restype = ctypes.c_bool
    return library


@lru_cache(maxsize=1)
def _tap_functions():
    graphics = ctypes.cdll.LoadLibrary(_GRAPHICS)
    graphics.CGEventTapCreate.restype = ctypes.c_void_p
    graphics.CGEventTapCreate.argtypes = [
        ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint64, _TAP_CALLBACK, ctypes.c_void_p,
    ]
    foundation = ctypes.cdll.LoadLibrary(_FOUNDATION)
    foundation.CFMachPortInvalidate.argtypes = [ctypes.c_void_p]
    foundation.CFRelease.argtypes = [ctypes.c_void_p]
    return graphics, foundation


def has_accessibility() -> bool:
    """Whether this process may install a filtering event tap."""
    if not needs_accessibility():
        return True
    try:
        return bool(_services().AXIsProcessTrusted())
    except Exception:  # pragma: no cover - treat an unknown answer as "try it"
        return True


def event_tap_allowed() -> bool:
    """Whether macOS would still give this process a filtering event tap.

    AXIsProcessTrusted can keep saying yes after the app is removed from the
    list with the minus button, and a filtering tap left running on a dead
    grant can stall input system-wide. So this asks the question that can't
    be fooled: it creates a throwaway tap for an event that never matters
    here (tablet proximity) and closes it again at once. That costs about
    0.05 ms.

    It goes through ctypes with one C callback made at import: PyObjC keeps
    every callback it is handed alive for good, which would leak a little on
    every probe.
    """
    if not needs_accessibility():
        return True
    try:
        graphics, foundation = _tap_functions()
        port = graphics.CGEventTapCreate(
            _SESSION_EVENT_TAP,
            _TAIL_APPEND_EVENT_TAP,
            _TAP_OPTION_DEFAULT,
            1 << _TABLET_PROXIMITY,
            _pass_through,
            None,
        )
    except Exception:  # pragma: no cover - unknown answer: leave the filter be
        return True
    if not port:
        return False
    foundation.CFMachPortInvalidate(port)
    foundation.CFRelease(port)
    return True


def request_accessibility() -> bool:
    """Ask macOS to list this exact app under Accessibility.

    This is the system's own request (AXIsProcessTrustedWithOptions with the
    prompt option). macOS adds the running app to the Accessibility list, so
    the user only has to turn its switch on, and the entry always matches
    this copy of the app. Adding the app by hand with "+" can pick up an older
    copy, which macOS then treats as a different app.
    """
    if not needs_accessibility():
        return True
    try:
        import objc
        from Foundation import NSDictionary

        options = NSDictionary.dictionaryWithObject_forKey_(True, "AXTrustedCheckOptionPrompt")
        return bool(_services().AXIsProcessTrustedWithOptions(objc.pyobjc_id(options)))
    except Exception:  # pragma: no cover - fall back to opening the pane
        return has_accessibility()


def open_accessibility_settings() -> None:
    """Open the Accessibility list in System Settings.

    An app that isn't allowed yet is registered first, so its entry is
    already in the list and the user only has to switch it on. One that is
    allowed gets the list as it is, to review or remove the grant.
    """
    if not needs_accessibility():
        return
    if not has_accessibility():
        request_accessibility()
    subprocess.Popen(["/usr/bin/open", ACCESSIBILITY_PANE])
