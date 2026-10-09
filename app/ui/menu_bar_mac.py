"""The macOS menu bar item, built with AppKit rather than Qt.

Qt's own tray icon crashes the app on macOS 27: opening the status item's
menu makes Qt read `clickCount` from the current event, which is now a
gesture event, and AppKit raises. It is fixed after Qt 6.11.2, which is the
newest release available, so the menu bar item is built directly on
NSStatusItem here. That keeps Qt out of the path entirely.

The API matches `Tray` in tray.py, which is still used on Windows.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QBuffer, QIODevice

from ..controller import AppController
from . import icons

# The handler class is defined once, on first use; ObjC class names are global.
_HANDLER_CLASS = None


def _handler_class():
    global _HANDLER_CLASS
    if _HANDLER_CLASS is None:
        from Foundation import NSObject

        class DoubleClickFixerMenuTarget(NSObject):
            """Receives the menu item actions and forwards them to Python."""

            def initWithActions_(self, actions):
                self = self.init()
                if self is None:
                    return None
                self._actions = actions
                return self

            def open_(self, _sender):
                self._actions["open"]()

            def calibrate_(self, _sender):
                self._actions["calibrate"]()

            def toggleFilter_(self, _sender):
                self._actions["toggle"]()

            def checkUpdates_(self, _sender):
                self._actions["updates"]()

            def quit_(self, _sender):
                self._actions["quit"]()

        _HANDLER_CLASS = DoubleClickFixerMenuTarget
    return _HANDLER_CLASS


#: The status item's position is saved under this name.
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


def _status_image(active: bool, size: int = 18):
    """The menu bar glyph: an SF Symbol, falling back to the drawn icon."""
    from AppKit import NSFontWeightMedium, NSImage, NSImageSymbolConfiguration

    name = SYMBOL_ON if active else SYMBOL_OFF
    image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, "DoubleClick Fixer")
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
    """A native status item with the same menu as the Windows tray icon."""

    def __init__(
        self,
        controller: AppController,
        on_open: Callable[[], None],
        on_calibrate: Callable[[], None],
        on_quit: Callable[[], None],
        on_toggle: Optional[Callable[[bool], None]] = None,
        updater=None,
        on_check_updates: Optional[Callable[[], None]] = None,
        on_install_update: Optional[Callable[[], None]] = None,
        parent=None,
    ) -> None:
        from AppKit import NSMenu, NSMenuItem, NSStatusBar, NSVariableStatusItemLength

        self.controller = controller
        self.updater = updater
        self._on_toggle = on_toggle or controller.set_active
        self._on_check_updates = on_check_updates
        self._on_install_update = on_install_update

        self._target = _handler_class().alloc().initWithActions_(
            {
                "open": on_open,
                "calibrate": on_calibrate,
                # The user's choice, not the running state: choosing the
                # item while waiting for permission or paused turns it off.
                "toggle": lambda: self._on_toggle(not controller.wanted),
                "updates": self._check_updates,
                "quit": on_quit,
            }
        )

        self._item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        # macOS remembers where the user ⌘-dragged the item to, by this name.
        self._item.setAutosaveName_(AUTOSAVE_NAME)
        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)

        self._status_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("", None, "")
        self._status_item.setEnabled_(False)
        menu.addItem_(self._status_item)

        self._toggle_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Bounce Filter", b"toggleFilter:", ""
        )
        self._toggle_item.setTarget_(self._target)
        menu.addItem_(self._toggle_item)
        menu.addItem_(NSMenuItem.separatorItem())

        open_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Open DoubleClick Fixer", b"open:", ""
        )
        open_item.setTarget_(self._target)
        menu.addItem_(open_item)
        calibrate_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Calibrate…", b"calibrate:", ""
        )
        calibrate_item.setTarget_(self._target)
        menu.addItem_(calibrate_item)

        self._update_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Check for Updates…", b"checkUpdates:", ""
        )
        self._update_item.setTarget_(self._target)
        if updater is not None and updater.supported:
            menu.addItem_(self._update_item)
            updater.changed.connect(self.refresh)

        menu.addItem_(NSMenuItem.separatorItem())
        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit DoubleClick Fixer", b"quit:", "q"
        )
        quit_item.setTarget_(self._target)
        menu.addItem_(quit_item)

        self._item.setMenu_(menu)
        self.menu = menu
        controller.filter_state_changed.connect(lambda *_: self.refresh())
        controller.settings_changed.connect(self.refresh)
        # The blocked count changes with every bounce, not only with settings.
        controller.global_event.connect(lambda *_: self._refresh_count())
        self._shown_active: Optional[bool] = None
        self.refresh()

    # -- the Tray API ----------------------------------------------------------
    def show(self) -> None:
        self._item.setVisible_(True)

    def hide(self) -> None:
        self._item.setVisible_(False)

    def showMessage(self, *_args, **_kwargs) -> None:  # noqa: N802 - matches Qt
        """Windows shows a balloon here; macOS menu bar apps stay quiet."""

    def refresh(self) -> None:
        from AppKit import NSControlStateValueOff, NSControlStateValueOn

        active = self.controller.active
        button = self._item.button()
        if button is not None and active != self._shown_active:
            # Only two images exist; swap only when the state changes.
            self._shown_active = active
            button.setImage_(_status_image(active))
        status = self.controller.status_text()
        if button is not None:
            button.setToolTip_(self.controller.tooltip_text())
        self._status_item.setTitle_(status)
        self._toggle_item.setState_(NSControlStateValueOn if self.controller.wanted else NSControlStateValueOff)
        if self.updater is not None and self.updater.state in (self.updater.AVAILABLE, self.updater.READY) and self.updater.release:
            self._update_item.setTitle_(f"Update to {self.updater.release.version}")
        else:
            self._update_item.setTitle_("Check for Updates…")

    def _refresh_count(self) -> None:
        if self.controller.active:
            self._status_item.setTitle_(self.controller.status_text())

    # -- actions ---------------------------------------------------------------
    def _check_updates(self) -> None:
        if self.updater is not None and self.updater.state in (self.updater.AVAILABLE, self.updater.READY):
            # Opens General first, so the install shows its progress.
            (self._on_install_update or self.updater.install)()
        elif self._on_check_updates is not None:
            self._on_check_updates()
