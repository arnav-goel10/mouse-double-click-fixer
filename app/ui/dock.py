"""The macOS Dock icon, shown only while the window is open.

DoubleClick Fixer is a menu bar app: the bundle is marked as an agent
(LSUIElement), so it launches with no Dock icon and lives in the menu bar.
While the window is open it switches to a regular app, so it gets a Dock
icon, an app menu, ⌘Tab and ⌘Q like any other window; closing the window
switches it back.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtGui import QGuiApplication

from .theme import IS_MAC

# Keeps the Apple Event handler alive; AppKit holds it only weakly.
_REOPEN_HANDLER: list = []


def _available() -> bool:
    return IS_MAC and QGuiApplication.platformName() == "cocoa"


def _four_char(code: str) -> int:
    return int.from_bytes(code.encode("ascii"), "big")


def on_reopen(callback: Callable[[], None]) -> None:
    """Call `callback` when the user opens the app again while it runs.

    macOS sends the "reopen" Apple Event when a running app is opened from
    Launchpad, Spotlight, Finder or the Dock. Clicking the menu bar icon does
    not send it, which is why this, and not app activation, is the signal to
    show the window: activation also happens when the menu bar icon's menu
    opens, and reacting to that would snatch the menu away.

    Install once the event loop is running; AppKit registers its own reopen
    handler while the app finishes launching.
    """
    if not _available() or _REOPEN_HANDLER:
        return
    try:
        from Foundation import NSAppleEventManager, NSObject

        class ReopenHandler(NSObject):
            def handleReopen_withReplyEvent_(self, _event, _reply):  # noqa: N802 - selector
                callback()

        handler = ReopenHandler.alloc().init()
        NSAppleEventManager.sharedAppleEventManager().setEventHandler_andSelector_forEventClass_andEventID_(
            handler,
            b"handleReopen:withReplyEvent:",
            _four_char("aevt"),
            _four_char("rapp"),
        )
        _REOPEN_HANDLER.append(handler)
    except Exception:  # noqa: BLE001 - the menu bar icon still opens the window
        pass


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
