"""The first run on macOS: one window that says why the app needs the
permission, one Continue button that brings up the system's own request,
then the live status ("Waiting for permission…", then "Ready") and an
optional offer to Test and Tune.

Apple's guidance for a screen before a system alert: one button, a word like
Continue (not Allow), and nothing that leads away from the alert. After the
alert, the window keeps saying what to do until access is there, and never
asks again by itself.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from .. import DISPLAY_NAME, permissions
from .base import label, make_button, set_primary
from .widgets import AppIconView, SymbolView, TextLabel

ASK = "ask"
WAITING = "waiting"
READY = "ready"


def needed() -> bool:
    """Whether a user-opened launch starts here: macOS, without the
    permission the filter needs."""
    return permissions.needs_accessibility() and not permissions.has_accessibility()


class FirstRunWindow(QWidget):
    """`filter_requested` asks for the filter on (the window that keeps it
    running starts it once access is there); `tune_requested` opens Test and
    Tune."""

    filter_requested = Signal()
    tune_requested = Signal()
    closed = Signal()

    def __init__(self, controller, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.state = ASK
        self.setWindowTitle(DISPLAY_NAME)
        self.setWindowFlags(
            Qt.WindowType.Window | Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowMinimizeButtonHint | Qt.WindowType.CustomizeWindowHint
        )
        self.setFixedWidth(480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 20)
        layout.setSpacing(0)
        icon = AppIconView(64)
        layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(14)
        self.title = TextLabel("", "title")
        self.title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.title.setWordWrap(True)
        layout.addWidget(self.title)
        layout.addSpacing(8)
        self.body = TextLabel("", "body", "secondary")
        self.body.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.body.setWordWrap(True)
        layout.addWidget(self.body)
        layout.addSpacing(14)

        self.status = QWidget()
        status_layout = QHBoxLayout(self.status)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(6)
        status_layout.addStretch(1)
        self.status_icon = SymbolView("warning", 16, "symbol")
        self.status_text = TextLabel("", "body")
        status_layout.addWidget(self.status_icon)
        status_layout.addWidget(self.status_text)
        status_layout.addStretch(1)
        layout.addWidget(self.status)
        layout.addSpacing(20)

        # Centred under the centred text, as the system's own permission
        # screens have them.
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        self.secondary_button = make_button("Open System Settings")
        self.secondary_button.clicked.connect(self._secondary)
        self.primary_button = make_button("Continue", default=True)
        self.primary_button.clicked.connect(self._primary)
        buttons.addWidget(self.secondary_button)
        buttons.addWidget(self.primary_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        controller.filter_state_changed.connect(lambda *_: self.refresh())
        controller.settings_changed.connect(self.refresh)
        self._render()

    # -- flow -----------------------------------------------------------------
    def _primary(self) -> None:
        if self.state == ASK:
            self.state = WAITING
            # The window that keeps the filter running waits for access, then
            # starts it; the system's request lists this app for the user.
            self.filter_requested.emit()
            permissions.request_accessibility()
            self.refresh()
        else:
            self.close()

    def _secondary(self) -> None:
        if self.state == WAITING:
            permissions.open_accessibility_settings()
        elif self.state == READY:
            self.tune_requested.emit()
            self.close()

    def refresh(self) -> None:
        if self.state == WAITING and self.controller.active:
            self.state = READY
        self._render()

    def _render(self) -> None:
        pane = permissions.pane_name()
        if self.state == ASK:
            self.title.setText(f"Let {DISPLAY_NAME} see your clicks")
            self.body.setText(
                "To ignore the extra click a worn mouse switch sends, the app has to see each click "
                "before other apps do. macOS asks you to allow this next. It looks only at the mouse "
                "(its buttons, wheel and movement), never at what you type, and nothing you do leaves this Mac."
            )
            self.status.setVisible(False)
            self.secondary_button.setVisible(False)
            self.primary_button.setText(label("Continue"))
        elif self.state == WAITING:
            self.title.setText(f"Allow {DISPLAY_NAME}")
            self.body.setText(
                f"In System Settings, open Privacy & Security › {pane} and turn on {DISPLAY_NAME}. "
                "This window moves on by itself once it’s allowed."
            )
            failure = self.controller.failure if not self.controller.waiting_for_permission else ""
            self._status("warning" if failure else "pause", failure or "Waiting for permission…")
            self.secondary_button.setVisible(True)
            self.secondary_button.setText(label("Open System Settings…"))
            self.primary_button.setVisible(False)
        else:
            self.title.setText("Bounce Filter is on")
            self.body.setText(
                "It runs from the menu bar: click the mouse icon there to pause it or open Settings. "
                "To check your mouse and set the right strength, try Test and Tune."
            )
            self._status("ok", "Ready")
            self.secondary_button.setVisible(True)
            self.secondary_button.setText(label("Test and Tune…"))
            self.primary_button.setVisible(True)
            self.primary_button.setText(label("Done"))
        set_primary(self.primary_button, not self.primary_button.isHidden())
        self.adjustSize()

    def _status(self, symbol: str, text: str) -> None:
        self.status.setVisible(True)
        self.status_icon.name = symbol
        self.status_icon.tone = "symbol" if symbol in ("ok", "warning") else "secondary"
        self.status_icon.update()
        self.status_text.setText(text)

    def apply_look(self) -> None:
        for text in self.findChildren(TextLabel):
            text.restyle()
        for button in self.findChildren(QPushButton):
            if button.isDefault():
                set_primary(button, True)
        self.update()

    def closeEvent(self, event) -> None:  # noqa: N802
        super().closeEvent(event)
        self.closed.emit()
