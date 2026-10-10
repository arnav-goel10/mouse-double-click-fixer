"""A button's menu as AppKit draws it, on macOS.

Qt draws a QMenu itself on macOS: square, with a hard edge, unlike every other
menu on the Mac. Qt only hands its menu bar and a pop-up button's list to
AppKit (see widgets.native_popup), so the menu a push button opens is built
here as an NSMenu, as the menu bar item's is (menu_bar_mac.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from PySide6.QtCore import QPoint, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget

from .theme import IS_MAC

#: The gap between a button and the menu it opens, as AppKit leaves it.
GAP = 4


@dataclass(frozen=True)
class Entry:
    """One line of a menu: an item that runs `action`, a section heading
    (no action), or a separator (no title)."""

    title: str = ""
    action: Optional[Callable[[], None]] = None
    tooltip: str = ""

    @property
    def separator(self) -> bool:
        return not self.title


# The target class is defined once, on first use; ObjC class names are global.
_TARGET_CLASS = None


def _target_class():
    global _TARGET_CLASS
    if _TARGET_CLASS is None:
        from Foundation import NSObject

        class DoubleClickFixerPopUpTarget(NSObject):
            """Receives a menu item's action and hands it to Qt."""

            def initWithActions_(self, actions):
                self = self.init()
                if self is None:
                    return None
                self._actions = actions
                return self

            def choose_(self, sender):
                action = self._actions.get(int(sender.tag()))
                if action is not None:
                    # After the menu has closed, in Qt's own loop: an action
                    # may open a dialog.
                    QTimer.singleShot(0, action)

        _TARGET_CLASS = DoubleClickFixerPopUpTarget
    return _TARGET_CLASS


def available() -> bool:
    """Whether a native menu can be shown: macOS, on the real window server."""
    return IS_MAC and QGuiApplication.platformName() == "cocoa"


def build(entries: list[Entry]):
    """The NSMenu for `entries`, and the target that must outlive it."""
    import AppKit

    actions: dict[int, Callable[[], None]] = {}
    target = _target_class().alloc().initWithActions_(actions)
    menu = AppKit.NSMenu.alloc().initWithTitle_("")
    menu.setAutoenablesItems_(False)
    for entry in entries:
        if entry.separator:
            menu.addItem_(AppKit.NSMenuItem.separatorItem())
            continue
        if entry.action is None:
            if hasattr(AppKit.NSMenuItem, "sectionHeaderWithTitle_"):  # macOS 14
                item = AppKit.NSMenuItem.sectionHeaderWithTitle_(entry.title)
            else:
                item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(entry.title, None, "")
                item.setEnabled_(False)
            menu.addItem_(item)
            continue
        item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(entry.title, b"choose:", "")
        item.setTarget_(target)
        item.setTag_(len(actions))
        actions[len(actions)] = entry.action
        if entry.tooltip:
            item.setToolTip_(entry.tooltip)
        menu.addItem_(item)
    return menu, target


def pop_up(button: QWidget, entries: list[Entry]) -> bool:
    """Open `entries` as a native menu under `button`, lined up with its
    leading edge. Returns once the menu closes, after scheduling the chosen
    item's action; False if no native menu could be shown."""
    if not available():
        return False
    try:
        import AppKit
        import objc

        window = button.window()
        view = objc.objc_object(c_void_p=int(window.winId()))
        menu, target = build(entries)
        menu.setMinimumWidth_(button.width())
        menu.setFont_(AppKit.NSFont.menuFontOfSize_(0))
        # The window's view is flipped (top-left origin), as Qt's coordinates are.
        corner = button.mapTo(window, QPoint(0, button.height() + GAP))
        menu.popUpMenuPositioningItem_atLocation_inView_(None, (corner.x(), corner.y()), view)
        del target  # held until here: the menu's items don't retain it
        return True
    except Exception:  # noqa: BLE001 - Qt's menu still works
        import logging

        logging.getLogger(__name__).warning("Couldn't show a native menu", exc_info=True)
        return False
