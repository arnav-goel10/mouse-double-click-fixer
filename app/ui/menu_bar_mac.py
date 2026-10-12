"""The macOS menu bar item, built with AppKit rather than Qt, and the Dock
menu.

Clicking the menu bar icon opens a menu, as Apple asks of a menu bar extra:
a status line, the filter's checkmark, Pause, Test and Tune…, Settings… and
Quit. The Dock menu offers the same actions, because the user, or macOS's
own "Allow in the Menu Bar" switch, can hide the icon.

Qt's own tray icon crashes the app on macOS 27: opening the status item's
menu makes Qt read `clickCount` from the current event, which is now a
gesture event, and AppKit raises. It is fixed after Qt 6.11.2, which is the
newest release available, so the menu bar item is built directly on
NSStatusItem here. That keeps Qt out of the path entirely.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QBuffer, QIODevice, QObject
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import QMenu

from .. import DISPLAY_NAME
from ..controller import AppController
from . import icons, menus

# The handler class is defined once, on first use; ObjC class names are global.
_HANDLER_CLASS = None


def _handler_class():
    global _HANDLER_CLASS
    if _HANDLER_CLASS is None:
        from Foundation import NSObject

        class DoubleClickFixerMenuTarget(NSObject):
            """Receives the menu item actions and forwards them to Python."""

            def initWithActions_(self, actions):  # noqa: N802 - selector
                self = self.init()
                if self is None:
                    return None
                self._actions = actions
                return self

            def toggleFilter_(self, _sender):  # noqa: N802 - selector
                self._actions["toggle"]()

            def pause_(self, _sender):
                self._actions["pause"]()

            def tune_(self, _sender):
                self._actions["tune"]()

            def settings_(self, _sender):
                self._actions["settings"]()

            def quit_(self, _sender):
                self._actions["quit"]()

        _HANDLER_CLASS = DoubleClickFixerMenuTarget
    return _HANDLER_CLASS


#: The status item's position (and whether it shows) is saved under this name.
AUTOSAVE_NAME = "com.doubleclickfixer.app.status-item"

#: SF Symbols for the menu bar: filled while filtering, outline when off.
#: Apple's own menu extras are drawn this way, and the system renders them at
#: the right weight and size for the menu bar, which a shrunken bitmap cannot.
SYMBOL_ON = "computermouse.fill"
SYMBOL_OFF = "computermouse"
#: 14 pt at medium weight: about 18 pt tall with strokes as heavy as Apple's
#: own menu bar extras (Wi-Fi, Sound). At 13 pt regular the mouse came out
#: smaller and thinner than everything next to it.
SYMBOL_POINT_SIZE = 14.0

#: The menu's wording.
FILTER_TITLE = "Filter Bounce"
TUNE_TITLE = "Test and Tune…"
SETTINGS_TITLE = "Settings…"
QUIT_TITLE = f"Quit {DISPLAY_NAME}"


def _status_image(active: bool, size: int = 18):
    """The menu bar glyph: an SF Symbol, falling back to the drawn icon."""
    from AppKit import NSFontWeightMedium, NSImage, NSImageSymbolConfiguration

    name = SYMBOL_ON if active else SYMBOL_OFF
    image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, DISPLAY_NAME)
    if image is not None:
        configured = image.imageWithSymbolConfiguration_(
            NSImageSymbolConfiguration.configurationWithPointSize_weight_(SYMBOL_POINT_SIZE, NSFontWeightMedium)
        )
        image = configured or image
        image.setTemplate_(True)  # macOS tints it for light and dark menu bars
        return image
    return _drawn_image(icons.tray_icon(active), size)


def _drawn_image(icon, size: int = 18):
    """Convert the app's own icon into a template NSImage."""
    from AppKit import NSImage
    from Foundation import NSData

    pixmap = icon.pixmap(size * 2, size * 2)
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(buffer, "PNG")
    data = NSData.dataWithBytes_length_(buffer.data().data(), buffer.data().size())
    image = NSImage.alloc().initWithData_(data)
    image.setSize_((size, size))
    image.setTemplate_(True)
    return image


class MacMenuBarItem:
    """The native status item and its menu."""

    def __init__(
        self,
        controller: AppController,
        on_settings: Callable[[], None],
        on_tune: Callable[[], None],
        on_quit: Callable[[], None],
        on_toggle: Optional[Callable[[bool], None]] = None,
        parent=None,
        **_unused,
    ) -> None:
        import AppKit
        from AppKit import NSMenu, NSMenuItem, NSStatusBar, NSVariableStatusItemLength

        self.controller = controller
        self._on_toggle = on_toggle or controller.set_active

        self._target = _handler_class().alloc().initWithActions_(
            {
                # The user's choice, not the running state: choosing the
                # item while waiting for permission or paused turns it off.
                "toggle": lambda: self._on_toggle(not controller.wanted),
                "pause": self._pause,
                "tune": on_tune,
                "settings": on_settings,
                "quit": on_quit,
            }
        )

        self._item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        # macOS remembers where the user ⌘-dragged the item to, and whether
        # it shows, by this name.
        self._item.setAutosaveName_(AUTOSAVE_NAME)
        removal = getattr(AppKit, "NSStatusItemBehaviorRemovalAllowed", None)
        if isinstance(removal, int) and hasattr(self._item, "setBehavior_"):
            self._item.setBehavior_(removal)
        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)

        def item(title: str, action: Optional[bytes], key: str = ""):
            entry = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
            if action is not None:
                entry.setTarget_(self._target)
            menu.addItem_(entry)
            return entry

        self._status_item = item("", None)
        self._status_item.setEnabled_(False)
        self._toggle_item = item(FILTER_TITLE, b"toggleFilter:")
        self._pause_item = item(menus.pause_title(), b"pause:")
        menu.addItem_(NSMenuItem.separatorItem())
        self._tune_item = item(TUNE_TITLE, b"tune:")
        self._settings_item = item(SETTINGS_TITLE, b"settings:", ",")
        menu.addItem_(NSMenuItem.separatorItem())
        self._quit_item = item(QUIT_TITLE, b"quit:", "q")

        self._item.setMenu_(menu)
        self.menu = menu
        controller.filter_state_changed.connect(lambda *_: self.refresh())
        controller.settings_changed.connect(self.refresh)
        controller.pause_changed.connect(self.refresh)
        # The blocked count changes with every bounce, not only with settings.
        controller.global_event.connect(lambda *_: self._refresh_count())
        self._shown_active: Optional[bool] = None
        self._shortcut_shown: Optional[str] = None
        self.refresh()

    # -- the Tray API ----------------------------------------------------------
    def show(self) -> None:
        self._item.setVisible_(True)

    def hide(self) -> None:
        self._item.setVisible_(False)

    def set_shown(self, shown: bool) -> None:
        """Settings' "Show in menu bar". macOS saves it under AUTOSAVE_NAME."""
        self._item.setVisible_(bool(shown))

    def is_shown(self) -> bool:
        try:
            return bool(self._item.isVisible())
        except Exception:  # noqa: BLE001 - an older AppKit: always shown
            return True

    def showMessage(self, *_args, **_kwargs) -> None:  # noqa: N802 - matches Qt
        """Windows shows a balloon here; macOS menu bar apps stay quiet."""

    def refresh(self) -> None:
        from AppKit import NSControlStateValueOff, NSControlStateValueOn

        controller = self.controller
        active = controller.active
        button = self._item.button()
        if button is not None and active != self._shown_active:
            # Only two images exist; swap only when the state changes.
            self._shown_active = active
            button.setImage_(_status_image(active))
        if button is not None:
            button.setToolTip_(menus.tooltip(controller))
        self._status_item.setTitle_(menus.status_line(controller))
        self._toggle_item.setState_(NSControlStateValueOn if controller.wanted else NSControlStateValueOff)
        self._pause_item.setTitle_(menus.pause_item_title(controller))
        # Nothing to pause while the filter is off. The Mac greys an item
        # out rather than taking it away, so the menu keeps its shape.
        self._pause_item.setEnabled_(menus.pause_item_shown(controller))
        self._show_shortcut(menus.shortcut_shown(controller))

    def _show_shortcut(self, shortcut: str) -> None:
        """The pause shortcut beside Pause (⌃⌥⌘P), while it is on. The
        system's hot key does the work: it never reaches the menu."""
        if shortcut == self._shortcut_shown:
            return
        self._shortcut_shown = shortcut
        try:
            import AppKit

            if shortcut:
                self._pause_item.setKeyEquivalent_("p")
                self._pause_item.setKeyEquivalentModifierMask_(
                    AppKit.NSEventModifierFlagControl | AppKit.NSEventModifierFlagOption | AppKit.NSEventModifierFlagCommand
                )
            else:
                self._pause_item.setKeyEquivalent_("")
        except Exception:  # noqa: BLE001 - cosmetic
            pass

    def _refresh_count(self) -> None:
        if self.controller.active:
            self._status_item.setTitle_(menus.status_line(self.controller))

    # -- actions ---------------------------------------------------------------
    def _pause(self) -> None:
        menus.toggle_pause(self.controller)
        self.refresh()

    def open_menu(self) -> bool:
        """Open the menu as a click on the icon does (for pictures of it).
        Returns once it closes."""
        button = self._item.button()
        if button is None:
            return False
        button.performClick_(None)
        return True


class DockMenu(QObject):
    """The Dock icon's menu: the menu bar menu's actions, so they are there
    when the menu bar icon isn't. The Dock adds Quit itself."""

    def __init__(
        self,
        controller: AppController,
        on_settings: Callable[[], None],
        on_tune: Callable[[], None],
        on_toggle: Optional[Callable[[bool], None]] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self._on_toggle = on_toggle or controller.set_active
        self.menu = QMenu()

        def action(title: str, run=None) -> QAction:
            entry = QAction(title, self.menu)
            # Not moved to the app menu for its wording ("Settings…").
            entry.setMenuRole(QAction.MenuRole.NoRole)
            if run is not None:
                entry.triggered.connect(lambda _checked=False: run())
            self.menu.addAction(entry)
            return entry

        self.status_action = action("")
        self.status_action.setEnabled(False)
        self.toggle_action = action(FILTER_TITLE, lambda: self._on_toggle(not controller.wanted))
        self.toggle_action.setCheckable(True)
        self.pause_action = action(menus.pause_title(), self._pause)
        self.menu.addSeparator()
        self.tune_action = action(TUNE_TITLE, on_tune)
        self.settings_action = action(SETTINGS_TITLE, on_settings)
        self.menu.aboutToShow.connect(self.refresh)
        controller.filter_state_changed.connect(lambda *_: self.refresh())
        controller.settings_changed.connect(self.refresh)
        controller.pause_changed.connect(self.refresh)
        self.refresh()
        if QGuiApplication.platformName() == "cocoa" and hasattr(self.menu, "setAsDockMenu"):
            self.menu.setAsDockMenu()

    def _pause(self) -> None:
        menus.toggle_pause(self.controller)
        self.refresh()

    def refresh(self) -> None:
        controller = self.controller
        self.status_action.setText(menus.status_line(controller))
        self.toggle_action.setChecked(bool(controller.wanted))
        self.pause_action.setText(menus.pause_item_title(controller))
        self.pause_action.setEnabled(menus.pause_item_shown(controller))

    def titles(self) -> list[str]:
        return [entry.text() for entry in self.menu.actions() if not entry.isSeparator()]
