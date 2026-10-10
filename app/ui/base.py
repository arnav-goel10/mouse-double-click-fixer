"""What every pane is made of: the page, its column, its buttons and wording."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
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
    first, *rest = text.split(" ")
    keep = {*DISPLAY_NAME.split(" "), "Accessibility"}
    return " ".join([first, *(word if word in keep else word.lower() for word in rest)])


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


class Page(QWidget):
    """A pane: stacked sections with headers and footnotes."""

    def __init__(self, title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.page_title = title
        self.column = centred_column(self)
        self.body = QVBoxLayout(self.column)
        self.body.setSpacing(0)
        if IS_MAC:
            self.body.setContentsMargins(20, 4, 20, 20)
        else:
            self.body.setContentsMargins(36, 0, 36, 28)
            heading = TextLabel(title, "title")
            heading.setContentsMargins(0, 24, 0, 20)
            self.body.addWidget(heading)

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


def link(text: str, href: str) -> str:
    """An inline link in the accent colour, for a rich-text label."""
    accent = look().accent.name()
    return f'<a href="{href}" style="color:{accent}; text-decoration:none">{text}</a>'


def make_link_label(label_widget: TextLabel) -> TextLabel:
    """Let `label_widget` show links that the mouse and the keyboard (Tab,
    then Return) can follow."""
    label_widget.setTextFormat(Qt.TextFormat.RichText)
    label_widget.setOpenExternalLinks(False)
    label_widget.setTextInteractionFlags(
        Qt.TextInteractionFlag.LinksAccessibleByMouse | Qt.TextInteractionFlag.LinksAccessibleByKeyboard
    )
    return label_widget


def controls(*widgets: QWidget, spacing: int = 10) -> QWidget:
    """Several controls side by side, as a row's trailing widget."""
    box = QWidget()
    layout = QHBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    for widget in widgets:
        layout.addWidget(widget)
    return box
