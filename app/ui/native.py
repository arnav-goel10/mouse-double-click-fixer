"""Native window materials: macOS sidebar vibrancy and Windows 11 Mica.

Everything here is best effort. If a call is unavailable the window simply
keeps the solid colours it already paints, so a failure is cosmetic only.
"""

from __future__ import annotations

import platform
import sys
import warnings

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget

IS_MAC = platform.system() == "Darwin"
IS_WINDOWS = platform.system() == "Windows"


def _native_backend() -> bool:
    """True only with the real window system (not offscreen or minimal),
    where a window handle is an actual NSView or HWND."""
    return QGuiApplication.platformName() in ("cocoa", "windows")


def prepare(window: QWidget) -> bool:
    """Set window hints before the native window exists.

    Returns True when the window will be translucent, which means the caller
    must leave the areas that should show the material unpainted.
    """
    if not _native_backend():
        return False
    if IS_MAC:
        with warnings.catch_warnings():
            # PySide6 flags this enum because it shares a value with a
            # deprecated one; the hint itself is current (Qt 6.9+).
            warnings.simplefilter("ignore", DeprecationWarning)
            hint = getattr(Qt.WindowType, "ExpandedClientAreaHint", None)
            no_background = getattr(Qt.WindowType, "NoTitleBarBackgroundHint", None)
        if hint is None or no_background is None:
            return False
        window.setWindowFlag(hint, True)
        window.setWindowFlag(no_background, True)
        window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # The layout reserves the title bar itself (sidebar inset, pane title
        # row); Qt must not add the safe-area margin on top of that.
        respects = getattr(Qt.WidgetAttribute, "WA_ContentsMarginsRespectsSafeArea", None)
        if respects is not None:
            window.setAttribute(respects, False)
        return True
    if IS_WINDOWS and _windows_build() >= 22621:
        window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        return True
    return False


def apply(window: QWidget, sidebar_width: int, dark: bool) -> bool:
    """Attach the material once the native window exists. True on success."""
    if not _native_backend():
        return False
    try:
        if IS_MAC:
            return _mac_sidebar(window, sidebar_width)
        if IS_WINDOWS:
            return _windows_mica(window, dark)
    except Exception:  # noqa: BLE001 - cosmetic only
        return False
    return False


def set_dark_title_bar(window: QWidget, dark: bool) -> None:
    if not IS_WINDOWS or not _native_backend():
        return
    try:
        import ctypes

        value = ctypes.c_int(1 if dark else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            int(window.winId()), 20, ctypes.byref(value), ctypes.sizeof(value)
        )
    except Exception:  # noqa: BLE001
        pass


# -- macOS -------------------------------------------------------------------

def _mac_sidebar(window: QWidget, sidebar_width: int) -> bool:
    import AppKit
    import objc

    view = objc.objc_object(c_void_p=int(window.winId()))
    ns_window = view.window()
    frame_view = view.superview()
    if ns_window is None or frame_view is None:
        return False

    # A unified toolbar gives the title bar the height System Settings uses,
    # with the traffic lights centred in it. It holds no items.
    if ns_window.toolbar() is None:
        toolbar = AppKit.NSToolbar.alloc().initWithIdentifier_("DoubleClickFixer")
        toolbar.setShowsBaselineSeparator_(False)
        ns_window.setToolbar_(toolbar)
    if hasattr(ns_window, "setToolbarStyle_"):
        ns_window.setToolbarStyle_(AppKit.NSWindowToolbarStyleUnified)
    if hasattr(ns_window, "setTitlebarSeparatorStyle_"):
        ns_window.setTitlebarSeparatorStyle_(AppKit.NSTitlebarSeparatorStyleNone)
    ns_window.setTitleVisibility_(AppKit.NSWindowTitleHidden)
    ns_window.setTitlebarAppearsTransparent_(True)

    height = ns_window.frame().size.height
    effect = AppKit.NSVisualEffectView.alloc().initWithFrame_(((0, 0), (sidebar_width, height)))
    effect.setMaterial_(AppKit.NSVisualEffectMaterialSidebar)
    effect.setBlendingMode_(AppKit.NSVisualEffectBlendingModeBehindWindow)
    effect.setState_(AppKit.NSVisualEffectStateFollowsWindowActiveState)
    effect.setAutoresizingMask_(AppKit.NSViewHeightSizable | AppKit.NSViewMaxXMargin)
    frame_view.addSubview_positioned_relativeTo_(effect, AppKit.NSWindowBelow, view)
    return True


def title_bar_height(window: QWidget) -> int:
    """Height of the (transparent) title bar region content must clear."""
    if not IS_MAC:
        return 0
    try:
        import objc

        view = objc.objc_object(c_void_p=int(window.winId()))
        ns_window = view.window()
        content = ns_window.contentLayoutRect()
        return int(ns_window.frame().size.height - content.size.height)
    except Exception:  # noqa: BLE001
        return 52


# -- Windows -----------------------------------------------------------------

def _windows_build() -> int:
    if not IS_WINDOWS:
        return 0
    try:
        return int(sys.getwindowsversion().build)
    except Exception:  # noqa: BLE001
        return 0


def _windows_mica(window: QWidget, dark: bool) -> bool:
    import ctypes
    from ctypes import wintypes

    hwnd = int(window.winId())
    dwm = ctypes.windll.dwmapi

    class MARGINS(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_int),
            ("right", ctypes.c_int),
            ("top", ctypes.c_int),
            ("bottom", ctypes.c_int),
        ]

    set_dark_title_bar(window, dark)
    margins = MARGINS(-1, -1, -1, -1)
    dwm.DwmExtendFrameIntoClientArea(wintypes.HWND(hwnd), ctypes.byref(margins))
    backdrop = ctypes.c_int(2)  # DWMSBT_MAINWINDOW: Mica
    result = dwm.DwmSetWindowAttribute(wintypes.HWND(hwnd), 38, ctypes.byref(backdrop), ctypes.sizeof(backdrop))
    return result == 0
