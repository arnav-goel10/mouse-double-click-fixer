"""The macOS Dock icon, shown only while the window is open, and the other
app-wide events AppKit delivers: reopen, wake, and session switches.

Mouse Double-Click Fixer is a menu bar app: the bundle is marked as an agent
(LSUIElement), so it launches with no Dock icon and lives in the menu bar.
While the window is open it switches to a regular app, so it gets a Dock
icon, an app menu, ⌘Tab and ⌘Q like any other window; closing the window
switches it back.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication

from .theme import IS_MAC

# Keeps the Apple Event handler alive; AppKit holds it only weakly.
_REOPEN_HANDLER: list = []
# Likewise for the notification observer.
_SYSTEM_OBSERVER: list = []


def _available() -> bool:
    return IS_MAC and QGuiApplication.platformName() == "cocoa"


def _four_char(code: str) -> int:
    return int.from_bytes(code.encode("ascii"), "big")


def _deferred(callback: Callable[[], None]) -> Callable[[], None]:
    """`callback`, run from Qt's event loop just after AppKit calls in,
    rather than inside AppKit's own dispatch. An exception in it then
    reaches sys.excepthook and the log instead of crossing into
    Objective-C, and the work never runs halfway through a notification."""

    def run() -> None:
        QTimer.singleShot(0, callback)

    return run


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
    callback = _deferred(callback)
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


def observe_system(
    on_wake: Callable[[], None],
    on_session_active: Callable[[], None],
    on_session_inactive: Callable[[], None],
    on_permission_change: Callable[[], None],
) -> None:
    """Call back when the Mac wakes, when the user switches into or out of
    this login session (fast user switching), and when the Accessibility
    list changes.

    Event taps can be left dead by sleep, and a tap running in a session
    the user switched away from can stall the one in front, so the filter is
    rebuilt or stopped on these. The Accessibility notification only comes
    when a switch in the list is flipped, not when the app is removed from
    it, so it speeds up the permission poll rather than replacing it.
    """
    if not _available() or _SYSTEM_OBSERVER:
        return
    on_wake, on_session_active, on_session_inactive, on_permission_change = (
        _deferred(callback) for callback in (on_wake, on_session_active, on_session_inactive, on_permission_change)
    )
    try:
        import AppKit
        from Foundation import NSDistributedNotificationCenter, NSObject

        class DoubleClickFixerSystemObserver(NSObject):
            def woke_(self, _notification):
                on_wake()

            def sessionActive_(self, _notification):  # noqa: N802 - selector
                on_session_active()

            def sessionInactive_(self, _notification):  # noqa: N802 - selector
                on_session_inactive()

            def permissionChanged_(self, _notification):  # noqa: N802 - selector
                on_permission_change()

        observer = DoubleClickFixerSystemObserver.alloc().init()
        workspace = AppKit.NSWorkspace.sharedWorkspace().notificationCenter()
        for selector, name in (
            (b"woke:", AppKit.NSWorkspaceDidWakeNotification),
            (b"sessionActive:", AppKit.NSWorkspaceSessionDidBecomeActiveNotification),
            (b"sessionInactive:", AppKit.NSWorkspaceSessionDidResignActiveNotification),
        ):
            workspace.addObserver_selector_name_object_(observer, selector, name, None)
        NSDistributedNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            observer, b"permissionChanged:", "com.apple.accessibility.api", None
        )
        _SYSTEM_OBSERVER.append(observer)
    except Exception:  # noqa: BLE001 - the timers in the window still cover it
        pass
