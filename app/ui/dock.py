"""The macOS Dock icon, shown only while the window is open.

DoubleClick Fixer is a menu bar app: the bundle is marked as an agent
(LSUIElement), so it launches with no Dock icon and lives in the menu bar.
While the window is open it switches to a regular app, so it gets a Dock
icon, an app menu, ⌘Tab and ⌘Q like any other window; closing the window
switches it back.
"""

from __future__ import annotations

from PySide6.QtGui import QGuiApplication

from .theme import IS_MAC


def _available() -> bool:
    return IS_MAC and QGuiApplication.platformName() == "cocoa"


def set_visible(visible: bool) -> None:
    """Show or hide the Dock icon (and the app menu that comes with it)."""
    if not _available():
        return
    try:
        import AppKit

        app = AppKit.NSApplication.sharedApplication()
        policy = (
            AppKit.NSApplicationActivationPolicyRegular
            if visible
            else AppKit.NSApplicationActivationPolicyAccessory
        )
        if app.activationPolicy() != policy:
            app.setActivationPolicy_(policy)
        if visible:
            # A newly regular app has to be activated to bring its window forward.
            if hasattr(app, "activate"):
                app.activate()
            else:  # macOS 13 and earlier
                app.activateIgnoringOtherApps_(True)
    except Exception:  # noqa: BLE001 - cosmetic; the window still works
        pass
