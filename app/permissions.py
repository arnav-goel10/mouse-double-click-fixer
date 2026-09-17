"""macOS Accessibility permission checks.

A filtering event tap is only allowed for a trusted process, so the UI needs to
know the answer before it offers to turn the filter on, and needs a way to send
the user to the right settings pane.
"""

from __future__ import annotations

import platform
import subprocess

ACCESSIBILITY_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"


def needs_accessibility() -> bool:
    """True on macOS, where the system-wide filter requires permission."""
    return platform.system() == "Darwin"


def has_accessibility() -> bool:
    """Whether this process may install a filtering event tap."""
    if not needs_accessibility():
        return True
    try:
        import ctypes

        services = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
        )
        services.AXIsProcessTrusted.restype = ctypes.c_bool
        return bool(services.AXIsProcessTrusted())
    except Exception:  # pragma: no cover - treat an unknown answer as "try it"
        return True


def open_accessibility_settings() -> None:
    """Open System Settings on the Accessibility list."""
    if not needs_accessibility():
        return
    subprocess.Popen(["open", ACCESSIBILITY_PANE])
