"""Menu bar extra (macOS) and notification area icon (Windows)."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from ..controller import AppController
from . import icons
from .theme import IS_MAC


def create(controller: AppController, **actions):
    """The menu bar item on macOS, the notification area icon on Windows.

    macOS gets a native status item because Qt's own crashes the app there
    (see menu_bar_mac.py); everywhere else Qt's tray icon is used.
    """
    if IS_MAC:
        from .menu_bar_mac import MacMenuBarItem

        return MacMenuBarItem(controller, **actions)
    return Tray(controller, **actions)


class Tray(QSystemTrayIcon):
    def __init__(
        self,
        controller: AppController,
        on_open: Callable[[], None],
        on_calibrate: Callable[[], None],
        on_quit: Callable[[], None],
        on_toggle: Optional[Callable[[bool], None]] = None,
        updater=None,
        on_check_updates: Optional[Callable[[], None]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self._on_open = on_open
        self._on_toggle = on_toggle or controller.set_active

        menu = QMenu()
        self.status_action = QAction("", menu)
        self.status_action.setEnabled(False)
        menu.addAction(self.status_action)

        self.toggle_action = QAction("Bounce Filter", menu)
        self.toggle_action.setCheckable(True)
        self.toggle_action.triggered.connect(lambda checked: self._on_toggle(checked))
        menu.addAction(self.toggle_action)
        menu.addSeparator()

        open_action = QAction("Open DoubleClick Fixer", menu)
        open_action.triggered.connect(lambda: on_open())
        menu.addAction(open_action)
        calibrate_action = QAction("Calibrate…", menu)
        calibrate_action.triggered.connect(lambda: on_calibrate())
        menu.addAction(calibrate_action)
        self.updater = updater
        self.update_action = QAction("Check for Updates…", menu)
        self.update_action.triggered.connect(self._on_update_action)
        self._on_check_updates = on_check_updates
        if updater is not None and updater.supported:
            menu.addAction(self.update_action)
            updater.changed.connect(self.refresh)
        menu.addSeparator()

        quit_action = QAction("Quit DoubleClick Fixer" if IS_MAC else "Exit", menu)
        quit_action.triggered.connect(lambda: on_quit())
        menu.addAction(quit_action)

        self.setContextMenu(menu)
        self._menu = menu
        self.activated.connect(self._on_activated)
        controller.filter_state_changed.connect(lambda *_: self.refresh())
        controller.settings_changed.connect(self.refresh)
        controller.global_event.connect(lambda *_: self.refresh())
        self.refresh()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        # macOS opens the menu on click; Windows opens the app, as tray apps do.
        if IS_MAC:
            return
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._on_open()

    def _on_update_action(self) -> None:
        if self.updater is not None and self.updater.state == self.updater.AVAILABLE:
            self.updater.install()
        elif self._on_check_updates is not None:
            self._on_check_updates()

    def refresh(self) -> None:
        if self.updater is not None and self.updater.state == self.updater.AVAILABLE and self.updater.release:
            self.update_action.setText(f"Update to {self.updater.release.version}")
        else:
            self.update_action.setText("Check for Updates…")
        active = self.controller.active
        self.setIcon(icons.tray_icon(active))
        self.toggle_action.setChecked(active)
        if active:
            self.status_action.setText(
                f"On · {self.controller.filtered_total:,} blocked"
            )
            self.setToolTip(f"DoubleClick Fixer: on, {self.controller.threshold_ms} ms")
        else:
            self.status_action.setText("Off")
            self.setToolTip("DoubleClick Fixer: off")
