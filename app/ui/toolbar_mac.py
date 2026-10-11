"""The Settings window's toolbar of panes on macOS.

A Mac app's Settings window switches panes from a toolbar: an NSToolbar in
the Preference style, each pane an SF Symbol over its name, the current one
highlighted, the window titled with the pane's name. Qt 6 has no native
toolbar of its own on macOS, so the real one is built here with AppKit, as
the menu bar item is (menu_bar_mac.py). `PaneBar` is the same row drawn with
Qt's tool buttons, used where there is no window server (tests, offscreen
pictures) or should AppKit refuse.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from PySide6.QtCore import QSize, QTimer, Qt, Signal
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QToolButton, QWidget

from . import symbols
from .widgets import look

log = logging.getLogger(__name__)

#: The toolbar's identifier (NSToolbar wants one; nothing is saved under it).
TOOLBAR_ID = "com.doubleclickfixer.app.settings"

#: The SF Symbol each pane's toolbar item shows.
PANE_SYMBOLS = {
    "general": "gearshape",
    "exceptions": "hand.raised",
    "history": "chart.xyaxis.line",
    "about": "info.circle",
}


class PaneBar(QWidget):
    """The pane switcher drawn by Qt: a row of checkable tool buttons, icon
    over label, one checked. Keyboard: Tab reaches the row, the arrows move
    within it, Space chooses."""

    current_changed = Signal(int)

    def __init__(self, items: list[tuple[str, str]], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.items = items
        self.setAccessibleName("Settings panes")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)
        layout.addStretch(1)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: list[QToolButton] = []
        for index, (key, title) in enumerate(items):
            button = QToolButton()
            button.setText(title)
            button.setAccessibleName(title)
            button.setCheckable(True)
            button.setAutoRaise(True)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setIconSize(QSize(22, 22))
            button.setMinimumWidth(68)
            button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            self.group.addButton(button, index)
            layout.addWidget(button)
            self.buttons.append(button)
        layout.addStretch(1)
        self._emit = True
        self.group.idClicked.connect(self._clicked)
        self.restyle()

    def restyle(self) -> None:
        colour = look().text
        for button, (key, _title) in zip(self.buttons, self.items):
            name = key if key in symbols.SYMBOLS else "general"
            button.setIcon(QIcon(symbols.icon_pixmap(name, 22, colour)))

    @property
    def current(self) -> int:
        return self.group.checkedId()

    def set_current(self, index: int, emit: bool = True) -> None:
        if not 0 <= index < len(self.buttons):
            return
        self._emit = emit
        try:
            self.buttons[index].setChecked(True)
            if emit:
                self.current_changed.emit(index)
        finally:
            self._emit = True

    def _clicked(self, index: int) -> None:
        if self._emit:
            self.current_changed.emit(index)


# -- the native toolbar ---------------------------------------------------------------

_DELEGATE_CLASS = None


def _delegate_class():
    global _DELEGATE_CLASS
    if _DELEGATE_CLASS is None:
        import objc
        from Foundation import NSObject

        protocol = objc.protocolNamed("NSToolbarDelegate")

        class DoubleClickFixerToolbarDelegate(NSObject, protocols=[protocol]):
            """Supplies the toolbar's items and hands a click to Qt."""

            def initWithItems_choose_(self, items, choose):  # noqa: N802 - selector
                self = self.init()
                if self is None:
                    return None
                self._items = items  # [(identifier, title, symbol)]
                self._choose = choose
                return self

            def _identifiers(self):
                return [identifier for identifier, _title, _symbol in self._items]

            def toolbarAllowedItemIdentifiers_(self, _toolbar):  # noqa: N802 - selector
                return self._identifiers()

            def toolbarDefaultItemIdentifiers_(self, _toolbar):  # noqa: N802 - selector
                return self._identifiers()

            def toolbarSelectableItemIdentifiers_(self, _toolbar):  # noqa: N802 - selector
                return self._identifiers()

            def toolbar_itemForItemIdentifier_willBeInsertedIntoToolbar_(self, _toolbar, identifier, _flag):  # noqa: N802
                import AppKit

                for known, title, symbol in self._items:
                    if known != identifier:
                        continue
                    item = AppKit.NSToolbarItem.alloc().initWithItemIdentifier_(identifier)
                    item.setLabel_(title)
                    item.setPaletteLabel_(title)
                    image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, title)
                    if image is not None:
                        item.setImage_(image)
                    item.setTarget_(self)
                    item.setAction_(b"choose:")
                    return item
                return None

            def choose_(self, sender):  # noqa: N802 - selector
                identifier = str(sender.itemIdentifier())
                # After AppKit's click handling, in Qt's own loop.
                QTimer.singleShot(0, lambda: self._choose(identifier))

        _DELEGATE_CLASS = DoubleClickFixerToolbarDelegate
    return _DELEGATE_CLASS


class NativeToolbar:
    """An NSToolbar of panes on a Qt window, in the Preference style."""

    def __init__(self, items: list[tuple[str, str]], on_choose: Callable[[int], None]) -> None:
        self.items = items
        self._on_choose = on_choose
        self._toolbar = None
        self._delegate = None

    @staticmethod
    def available() -> bool:
        from .theme import IS_MAC

        return IS_MAC and QGuiApplication.platformName() == "cocoa"

    def install(self, window: QWidget, index: int) -> bool:
        """Put the toolbar on `window` (which must have its native window),
        showing pane `index`. False if AppKit refused."""
        if not self.available():
            return False
        try:
            import AppKit
            import objc

            view = objc.objc_object(c_void_p=int(window.winId()))
            ns_window = view.window()
            if ns_window is None:
                return False
            entries = [(key, title, PANE_SYMBOLS.get(key, "gearshape")) for key, title in self.items]
            keys = [key for key, _title in self.items]

            def choose(identifier: str) -> None:
                if identifier in keys:
                    self._on_choose(keys.index(identifier))

            self._delegate = _delegate_class().alloc().initWithItems_choose_(entries, choose)
            toolbar = AppKit.NSToolbar.alloc().initWithIdentifier_(TOOLBAR_ID)
            toolbar.setDelegate_(self._delegate)
            toolbar.setAllowsUserCustomization_(False)
            toolbar.setAutosavesConfiguration_(False)
            if hasattr(toolbar, "setAllowsDisplayModeCustomization_"):  # macOS 15
                toolbar.setAllowsDisplayModeCustomization_(False)
            toolbar.setDisplayMode_(AppKit.NSToolbarDisplayModeIconAndLabel)
            ns_window.setToolbar_(toolbar)
            if hasattr(ns_window, "setToolbarStyle_"):
                ns_window.setToolbarStyle_(AppKit.NSWindowToolbarStylePreference)
            ns_window.setTitleVisibility_(AppKit.NSWindowTitleVisible)
            self._toolbar = toolbar
            self.select(index)
            return True
        except Exception:  # noqa: BLE001 - the Qt row still switches panes
            log.warning("Couldn't build the Settings toolbar", exc_info=True)
            self._toolbar = None
            return False

    @property
    def installed(self) -> bool:
        return self._toolbar is not None

    def select(self, index: int) -> None:
        if self._toolbar is None or not 0 <= index < len(self.items):
            return
        try:
            self._toolbar.setSelectedItemIdentifier_(self.items[index][0])
        except Exception:  # noqa: BLE001 - cosmetic
            pass
