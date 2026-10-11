"""Windows 11's window materials and frame: Mica, the dark title bar and the
rounded corners, through DWM.

macOS needs none of this: the Settings window is an ordinary AppKit window
with a toolbar (toolbar_mac.py), whose background AppKit draws.

Everything here is best effort. If a call is unavailable the window simply
keeps the solid colours it already paints, so a failure is cosmetic only.
"""

from __future__ import annotations

import platform
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget

IS_WINDOWS = platform.system() == "Windows"

#: DwmSetWindowAttribute's attributes (dwmapi.h).
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_CAPTION_COLOR = 35
DWMWA_SYSTEMBACKDROP_TYPE = 38
#: DWM_SYSTEMBACKDROP_TYPE: Mica, for a long-lived main window.
DWMSBT_MAINWINDOW = 2
#: DWM_WINDOW_CORNER_PREFERENCE: 8 px (a window, flyout or dialog), 4 px.
DWMWCP_ROUND, DWMWCP_ROUNDSMALL = 2, 3
#: The first Windows 11 build, and the first with DWMWA_SYSTEMBACKDROP_TYPE.
WINDOWS_11, BACKDROP_BUILD = 22000, 22621


def _native_backend() -> bool:
    """True only with the real window system (not offscreen or minimal),
    where a window handle is an actual HWND."""
    return QGuiApplication.platformName() == "windows"


def _windows_build() -> int:
    if not IS_WINDOWS:
        return 0
    try:
        return int(sys.getwindowsversion().build)
    except Exception:  # noqa: BLE001
        return 0


def prepare(window: QWidget) -> bool:
    """Set window hints before the native window exists. Returns True when
    Mica can be had (Windows 11 22H2 and later), which means the caller must
    leave the areas that should show it unpainted; otherwise the window
    paints its own solid colour, Mica's fallback."""
    if not IS_WINDOWS or not _native_backend() or _windows_build() < BACKDROP_BUILD:
        return False
    window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    return True


def apply(window: QWidget, dark: bool) -> bool:
    """Attach Mica once the native window exists. True on success."""
    if not IS_WINDOWS or not _native_backend():
        return False
    try:
        return _windows_mica(window, dark)
    except Exception:  # noqa: BLE001 - cosmetic only
        return False


def _set_attribute(window: QWidget, attribute: int, value) -> int:
    import ctypes
    from ctypes import wintypes

    return ctypes.windll.dwmapi.DwmSetWindowAttribute(
        wintypes.HWND(int(window.winId())), attribute, ctypes.byref(value), ctypes.sizeof(value)
    )


def set_dark_title_bar(window: QWidget, dark: bool, caption=None) -> None:
    """Match the Windows title bar to the theme. Without Mica, `caption`
    (the window colour) also tints it, so bar and window read as one surface."""
    if not IS_WINDOWS or not _native_backend():
        return
    try:
        import ctypes

        _set_attribute(window, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.c_int(1 if dark else 0))
        if caption is not None and _windows_build() >= WINDOWS_11:
            # A COLORREF (0x00BBGGRR).
            colour = ctypes.c_uint(caption.red() | caption.green() << 8 | caption.blue() << 16)
            _set_attribute(window, DWMWA_CAPTION_COLOR, colour)
    except Exception:  # noqa: BLE001
        pass


def round_corners(window: QWidget, small: bool = False) -> None:
    """Windows 11: rounded corners, 8 px for a window, flyout or dialog, or
    `small` (4 px). Windows 10 keeps square ones."""
    if not IS_WINDOWS or not _native_backend() or _windows_build() < WINDOWS_11:
        return
    try:
        import ctypes

        preference = ctypes.c_int(DWMWCP_ROUNDSMALL if small else DWMWCP_ROUND)
        _set_attribute(window, DWMWA_WINDOW_CORNER_PREFERENCE, preference)
    except Exception:  # noqa: BLE001 - cosmetic
        pass


def _windows_mica(window: QWidget, dark: bool) -> bool:
    import ctypes
    from ctypes import wintypes

    class MARGINS(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_int),
            ("right", ctypes.c_int),
            ("top", ctypes.c_int),
            ("bottom", ctypes.c_int),
        ]

    set_dark_title_bar(window, dark)
    round_corners(window)
    # Mica runs through the title bar and behind the whole client area.
    margins = MARGINS(-1, -1, -1, -1)
    ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(wintypes.HWND(int(window.winId())), ctypes.byref(margins))
    return _set_attribute(window, DWMWA_SYSTEMBACKDROP_TYPE, ctypes.c_int(DWMSBT_MAINWINDOW)) == 0
