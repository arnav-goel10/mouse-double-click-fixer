"""Menu bar (macOS) and notification area (Windows) presence."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from ..controller import AppController
from . import icons


class Tray(QSystemTrayIcon):
    def __init__(
        self,
        controller: AppController,
        on_open: Callable[[], None],
        on_calibrate: Callable[[], None],
        on_quit: Callable[[], None],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self._on_open = on_open

        menu = QMenu()
        self.status_action = QAction("Filter off", menu)
        self.status_action.setEnabled(False)
        menu.addAction(self.status_action)
        menu.addSeparator()

        self.toggle_action = QAction("Turn filter on", menu)
        self.toggle_action.setCheckable(True)
        self.toggle_action.triggered.connect(self._toggle)
        menu.addAction(self.toggle_action)

        open_action = QAction("Open DoubleClick Fixer", menu)
        open_action.triggered.connect(lambda: on_open())
        menu.addAction(open_action)

        calibrate_action = QAction("Calibrate...", menu)
        calibrate_action.triggered.connect(lambda: on_calibrate())
        menu.addAction(calibrate_action)

        menu.addSeparator()
        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(lambda: on_quit())
        menu.addAction(quit_action)

        self.setContextMenu(menu)
        self._menu = menu
        self.activated.connect(self._on_activated)
        controller.filter_state_changed.connect(lambda *_: self.refresh())
        controller.settings_changed.connect(self.refresh)
        self.refresh()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._on_open()

    def _toggle(self, checked: bool) -> None:
        self.controller.set_active(checked)
        self.refresh()

    def refresh(self) -> None:
        active = self.controller.active
        self.setIcon(icons.tray_icon(active))
        self.toggle_action.setChecked(active)
        self.toggle_action.setText("Turn filter off" if active else "Turn filter on")
        if active:
            blocked = self.controller.filtered_total
            self.status_action.setText(
                f"Filtering below {self.controller.threshold_ms} ms · {blocked:,} blocked"
            )
            self.setToolTip(f"DoubleClick Fixer — filtering below {self.controller.threshold_ms} ms")
        else:
            self.status_action.setText("Filter off")
            self.setToolTip("DoubleClick Fixer — filter off")
