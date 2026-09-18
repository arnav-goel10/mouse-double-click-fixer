"""macOS Accessibility permission checks.

A filtering event tap is only allowed for a trusted process, so the UI needs to
know the answer before it offers to turn the filter on, and needs a way to get
the user to the right switch.
"""

from __future__ import annotations

import ctypes
import platform
import subprocess
from functools import lru_cache

ACCESSIBILITY_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
_SERVICES = "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"


def needs_accessibility() -> bool:
    """True on macOS, where the system-wide filter requires permission."""
    return platform.system() == "Darwin"


@lru_cache(maxsize=1)
def _services():
    library = ctypes.cdll.LoadLibrary(_SERVICES)
    library.AXIsProcessTrusted.restype = ctypes.c_bool
    library.AXIsProcessTrustedWithOptions.argtypes = [ctypes.c_void_p]
    library.AXIsProcessTrustedWithOptions.restype = ctypes.c_bool
    return library


def has_accessibility() -> bool:
    """Whether this process may install a filtering event tap."""
    if not needs_accessibility():
        return True
    try:
        return bool(_services().AXIsProcessTrusted())
    except Exception:  # pragma: no cover - treat an unknown answer as "try it"
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
    """Register this app with macOS, then open the Accessibility list."""
    if not needs_accessibility():
        return
    if request_accessibility():
        return
    subprocess.Popen(["open", ACCESSIBILITY_PANE])
