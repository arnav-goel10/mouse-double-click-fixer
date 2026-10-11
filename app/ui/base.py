"""What every pane is made of: the page, its column, its buttons and wording."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from .. import DISPLAY_NAME
from .theme import IS_MAC
from .widgets import Section, SymbolView, TextLabel, look

#: Content never stretches wider than this; extra window width becomes margin,
#: so a label always stays within reach of its control.
COLUMN_MAX = 640 if IS_MAC else 1000
#: The narrowest the content column may get before the window stops shrinking.
COLUMN_MIN = 440


def label(text: str) -> str:
    """Button and menu wording in the platform's own case: Title Case on
    macOS ("Check Now"), sentence case on Windows ("Check now")."""
    if IS_MAC:
        return text
    # Names keep their capitals: the app's ("Open Mouse Double-Click Fixer")
    # and the system's ("Accessibility"), but not words that merely share
    # one ("Try a double-click").
    parts = text.split(DISPLAY_NAME)
    cased = []
    for index, part in enumerate(parts):
        words = part.split(" ")
        cased.append(" ".join(
            word if (index == 0 and position == 0) or word == "Accessibility" else word.lower()
            for position, word in enumerate(words)
        ))
    return DISPLAY_NAME.join(cased)


def make_button(text: str, default: bool = False) -> QPushButton:
    button = QPushButton(label(text))
    set_primary(button, default)
    return button


def set_primary(button: QPushButton, primary: bool) -> None:
    """Make `button` the default one. Windows 11 fills it with the accent
    colour (WinUI's AccentButton); macOS draws its own default button."""
    button.setDefault(primary)
    button.setAutoDefault(primary)
    if IS_MAC:
        return
    if not primary:
        button.setStyleSheet("")
        return
    lk = look()
    accent = lk.accent
    text = "#000000" if lk.dark else "#ffffff"
    hover = accent.lighter(110) if lk.dark else accent.darker(110)
    button.setStyleSheet(
        "QPushButton {"
        f" background: {accent.name()}; color: {text};"
        " border: 1px solid rgba(0, 0, 0, 20); border-radius: 4px;"
        " padding: 5px 16px; min-height: 20px; }"
        f"QPushButton:hover {{ background: {hover.name()}; }}"
        f"QPushButton:pressed {{ background: {accent.name()}; color: rgba({'0,0,0' if lk.dark else '255,255,255'},180); }}"
        "QPushButton:disabled { background: rgba(128,128,128,70); color: rgba(128,128,128,200); border: none; }"
    )


def link_button(text: str) -> QPushButton:
    """WinUI's HyperlinkButton: a flat button with its text in the accent
    colour. A real button, so Tab reaches it and Space or Return press it,
    and Narrator says "button"; coloured through its palette, never a
    style sheet (see restyle_link)."""
    button = QPushButton(text)
    button.setFlat(True)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setAutoDefault(False)
    restyle_link(button)
    return button


def restyle_link(button: QPushButton) -> None:
    palette = button.palette()
    for role in (QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        palette.setColor(role, look().accent)
    button.setPalette(palette)


def card_icon(name: str) -> Optional[SymbolView]:
    """Windows 11 Settings leads every card with a Fluent icon; macOS rows
    inside a grouped box carry none."""
    return None if IS_MAC else SymbolView(name, 20, "text")


def centred_column(parent: QWidget, maximum: int = COLUMN_MAX) -> QWidget:
    """Give `parent` a column capped at `maximum` wide, centred in any extra space."""
    outer = QHBoxLayout(parent)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)
    column = QWidget()
    column.setMaximumWidth(maximum)
    outer.addStretch(1)
    outer.addWidget(column, 100)
    outer.addStretch(1)
    return column


class Sections:
    """Stacked sections with headers and footnotes, added to `self.body`."""

    body: QVBoxLayout

    def header(self, text: str) -> TextLabel:
        label = TextLabel(text, "headline")
        label.setContentsMargins(2 if IS_MAC else 0, 18, 0, 6 if IS_MAC else 8)
        self.body.addWidget(label)
        return label

    def section(self) -> Section:
        section = Section()
        self.body.addWidget(section)
        return section

    def footnote(self, text: str) -> TextLabel:
        label = TextLabel(text, "caption", "secondary")
        label.setWordWrap(True)
        label.setContentsMargins(2 if IS_MAC else 0, 6, 0, 0)
        self.body.addWidget(label)
        return label

    def gap(self, height: int = 20) -> None:
        self.body.addSpacing(height)


class Page(Sections, QWidget):
    """A pane: stacked sections with headers and footnotes, in a column.
    macOS titles the window with the pane's name; Windows heads the page
    with it, as Windows Settings does."""

    def __init__(self, title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.page_title = title
        self.column = centred_column(self)
        self.body = QVBoxLayout(self.column)
        self.body.setSpacing(0)
        if IS_MAC:
            self.body.setContentsMargins(20, 14, 20, 20)
        else:
            self.body.setContentsMargins(36, 0, 36, 28)
            self.heading = TextLabel(title, "title")
            self.heading.setContentsMargins(0, 24, 0, 20)
            self.body.addWidget(self.heading)


class Block(Sections, QWidget):
    """Sections that belong to a pane, as one widget: the Apps and Devices
    halves of Exceptions."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(0)


def controls(*widgets: QWidget, spacing: int = 10) -> QWidget:
    """Several controls side by side, as a row's trailing widget."""
    box = QWidget()
    layout = QHBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    for widget in widgets:
        layout.addWidget(widget)
    return box
