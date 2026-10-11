"""The notification area icon on Windows (and the factory that gives macOS
its menu bar item instead).

As Windows asks of a tray icon: a left click opens a small flyout with what
people most want (the status, the switch, a way into the app) that closes
when they click elsewhere; a right click opens a short menu, Open first and
bold, with no About and nothing greyed out; the tooltip is the name and a
word of status.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QRect, QTimer, Qt
from PySide6.QtGui import QAction, QGuiApplication, QPainter
from PySide6.QtWidgets import QHBoxLayout, QMenu, QMessageBox, QSystemTrayIcon, QVBoxLayout, QWidget

from .. import DISPLAY_NAME
from ..controller import AppController
from . import icons, menus, native
from .base import label, link_button, restyle_link
from .theme import IS_MAC
from .widgets import AppIconView, Switch, TextLabel, look

#: Room between the flyout and the taskbar or the screen's edge, as Windows
#: leaves around its own flyouts.
FLYOUT_MARGIN = 12
FLYOUT_WIDTH = 340


def create(controller: AppController, **actions):
    """The menu bar item on macOS, the notification area icon on Windows.

    macOS gets a native status item because Qt's own crashes the app there
    (see menu_bar_mac.py); everywhere else Qt's tray icon is used.
    """
    if IS_MAC:
        from .menu_bar_mac import MacMenuBarItem

        return MacMenuBarItem(controller, **actions)
    return Tray(controller, **actions)


class TrayFlyout(QWidget):
    """The left-click flyout: the status sentence, the switch and Open. A
    popup, so a click anywhere else, or Escape, closes it."""

    def __init__(self, controller: AppController, on_toggle: Callable[[bool], None], on_open: Callable[[], None]) -> None:
        super().__init__(None, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.controller = controller
        self._on_toggle = on_toggle
        self._on_open = on_open
        self.setAccessibleName(DISPLAY_NAME)
        self.setFixedWidth(FLYOUT_WIDTH)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(16, 14, 16, 16)
        body.setSpacing(10)
        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(AppIconView(16))
        header.addWidget(TextLabel(DISPLAY_NAME, "caption", "secondary"))
        header.addStretch(1)
        body.addLayout(header)
        row = QHBoxLayout()
        row.setSpacing(12)
        self.title = TextLabel("Bounce filter", "headline")
        row.addWidget(self.title)
        row.addStretch(1)
        self.switch = Switch(accessible_name="Bounce filter")
        self.switch.clicked.connect(self._toggled)
        row.addWidget(self.switch)
        body.addLayout(row)
        self.status = TextLabel("", "body", "secondary")
        self.status.setWordWrap(True)
        body.addWidget(self.status)
        layout.addWidget(content)

        self.footer = QWidget()
        footer = QHBoxLayout(self.footer)
        footer.setContentsMargins(16, 10, 16, 12)
        self.open_link = link_button(label("Open"))
        self.open_link.setAccessibleName(f"Open {DISPLAY_NAME}")
        self.open_link.clicked.connect(self._open)
        footer.addWidget(self.open_link)
        footer.addStretch(1)
        layout.addWidget(self.footer)
        self.restyle()

    def restyle(self) -> None:
        for text in self.findChildren(TextLabel):
            text.restyle()
        restyle_link(self.open_link)
        self.update()

    def refresh(self) -> None:
        self.switch.setChecked(bool(self.controller.wanted), animate=self.isVisible())
        self.status.setText(self.controller.status_sentence())

    def _toggled(self, checked: bool) -> None:
        self._on_toggle(bool(checked))
        self.refresh()

    def _open(self) -> None:
        self.close()
        self._on_open()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def show_near(self, icon: QRect) -> None:
        """Open beside the notification area: above a bottom taskbar (or
        beside one elsewhere), lined up with the icon, inside the screen."""
        self.refresh()
        self.adjustSize()
        screen = QGuiApplication.screenAt(icon.center()) if icon.isValid() else None
        screen = screen or QGuiApplication.primaryScreen()
        available, full = screen.availableGeometry(), screen.geometry()
        width, height = self.width(), self.height()
        margin = FLYOUT_MARGIN
        anchor_x = icon.center().x() - width // 2 if icon.isValid() else available.right() - width - margin
        x = max(available.left() + margin, min(anchor_x, available.right() - width - margin))
        if available.top() > full.top():  # taskbar at the top
            y = available.top() + margin
        elif available.left() > full.left():  # on the left
            x, y = available.left() + margin, available.bottom() - height - margin
        elif available.right() < full.right():  # on the right
            x, y = available.right() - width - margin, available.bottom() - height - margin
        else:
            y = available.bottom() - height - margin
        self.move(x, y)
        # The switch first, ready for Space: Qt hands a new popup's focus to
        # the widget it already names, and only otherwise to the first one.
        self.switch.setFocus(Qt.FocusReason.PopupFocusReason)
        self.show()
        native.round_corners(self)
        self.raise_()
        self.activateWindow()

    def paintEvent(self, _event) -> None:  # noqa: N802
        lk = look()
        painter = QPainter(self)
        # Windows' flyout surface where acrylic can't be had, and its footer.
        surface = lk.pane.lighter(103) if not lk.dark else lk.pane.lighter(140)
        painter.fillRect(self.rect(), surface)
        painter.fillRect(self.footer.geometry(), lk.pane)
        painter.setPen(lk.section_border)
        painter.drawLine(0, self.footer.geometry().top(), self.width(), self.footer.geometry().top())
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))


class Tray(QSystemTrayIcon):
    def __init__(
        self,
        controller: AppController,
        on_open: Callable[[], None],
        on_tune: Optional[Callable[[], None]] = None,
        on_quit: Optional[Callable[[], None]] = None,
        on_toggle: Optional[Callable[[bool], None]] = None,
        parent: Optional[QWidget] = None,
        **_unused,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self._on_open = on_open
        self._on_toggle = on_toggle or controller.set_active

        menu = QMenu()
        self.open_action = QAction(label("Open"), menu)
        self.open_action.triggered.connect(lambda: on_open())
        menu.addAction(self.open_action)
        # The default command, in bold, as Windows draws it.
        menu.setDefaultAction(self.open_action)
        menu.addSeparator()
        self.pause_action = QAction(menus.pause_title(), menu)
        self.pause_action.triggered.connect(self._pause)
        menu.addAction(self.pause_action)
        self.login_action = QAction("Start with Windows", menu)
        self.login_action.setCheckable(True)
        self.login_action.triggered.connect(self._on_login)
        menu.addAction(self.login_action)
        menu.addSeparator()
        self.quit_action = QAction("Exit", menu)
        self.quit_action.triggered.connect(lambda: on_quit() if on_quit else None)
        menu.addAction(self.quit_action)
        menu.aboutToShow.connect(self._before_menu)
        self.setContextMenu(menu)
        self._menu = menu

        self.flyout = TrayFlyout(controller, self._on_toggle, on_open)
        self.activated.connect(self._on_activated)
        controller.filter_state_changed.connect(lambda *_: self.refresh())
        controller.settings_changed.connect(self.refresh)
        controller.global_event.connect(lambda *_: self.refresh())
        controller.pause_changed.connect(self.refresh)
        self.refresh()
        # Windows sends no theme signal Qt passes on when only the taskbar
        # switches between light and dark, so look now and then.
        self._theme_timer = QTimer(self)
        self._theme_timer.setInterval(5000)
        self._theme_timer.timeout.connect(self.refresh)
        self._theme_timer.start()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.flyout.isVisible():
                self.flyout.close()
            else:
                self.show_flyout()
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            # The default command, as Windows runs it on a double click.
            self.flyout.close()
            self._on_open()

    def show_flyout(self) -> None:
        self.flyout.show_near(self.geometry())

    def _pause(self) -> None:
        menus.toggle_pause(self.controller)
        self.refresh()

    def _on_login(self, checked: bool) -> None:
        error = self.controller.set_start_at_login(bool(checked))
        if error:
            QMessageBox.warning(None, "Couldn’t change the login item", error)
        self.refresh()

    def _before_menu(self) -> None:
        # Task Manager may have switched the login item since.
        self.controller.refresh_login_item()
        self.refresh()

    def refresh(self) -> None:
        controller = self.controller
        active = controller.active
        state = (active, icons.taskbar_is_light())
        if state != getattr(self, "_icon_state", None):
            # Refreshed on every blocked bounce; the icon only changes with the
            # filter state or the taskbar theme.
            self._icon_state = state
            self.setIcon(icons.tray_icon(active))
        title = menus.pause_item_title(controller)
        shortcut = menus.shortcut_shown(controller)
        # The shortcut on the right, as Windows menus show one.
        self.pause_action.setText(f"{title}\t{shortcut}" if shortcut else title)
        # Removed, not greyed out, when there is nothing to pause.
        self.pause_action.setVisible(menus.pause_item_shown(controller))
        self.login_action.setChecked(bool(controller.settings["start_at_login"]))
        self.setToolTip(menus.tooltip(controller))
        if self.flyout.isVisible():
            self.flyout.refresh()

    def restyle(self) -> None:
        self.flyout.restyle()
        self.refresh()
