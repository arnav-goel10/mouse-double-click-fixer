"""Windows 11's own building blocks, as WinUI draws them: the left
NavigationView with Settings pinned at the bottom, the SettingsExpander and
the InfoBar.

Each is a few native controls (lists, buttons, labels) in a surface painted
from the WinUI theme colours (theme.Look); nothing here carries a style
sheet, so the controls inside keep the system's own style.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QToolButton, QVBoxLayout, QWidget

from . import symbols
from .theme import with_alpha
from .widgets import Row, Sidebar, TextLabel, look


def symbol_icon(name: str, size: int = 16) -> QIcon:
    """A Fluent (or SF) glyph as an icon, in the text colour."""
    return QIcon(symbols.icon_pixmap(name, size, look().text))


# -- NavigationView ----------------------------------------------------------------

class NavigationView(QWidget):
    """The left navigation pane: the pages in one list, the footer items
    (Settings) in a second list pinned to the bottom, one selection across
    both. Each list is a real list, so Narrator reads "Home, 1 of 3,
    selected"; the arrow keys move across both as in WinUI."""

    current_changed = Signal(int)

    def __init__(self, items: list[tuple[str, str]], footer: list[tuple[str, str]], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.items = list(items) + list(footer)
        self.top = Sidebar(list(items))
        self.top.setAccessibleName("Navigation")
        self.footer = Sidebar(list(footer))
        self.footer.setAccessibleName("Navigation footer")
        self.footer.setViewportMargins(0, 0, 0, 12)
        self.footer.setFixedHeight(len(footer) * (self.footer.item_height + self.footer.item_gap) + 14)
        self.footer.clear_current()
        self.setFixedWidth(self.top.width())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.top, 1)
        layout.addWidget(self.footer, 0)
        self.top.current_changed.connect(lambda row: self._chosen(row))
        self.footer.current_changed.connect(lambda row: self._chosen(len(self.top.items) + row))
        self.top.installEventFilter(self)
        self.footer.installEventFilter(self)

    @property
    def lists(self) -> tuple[Sidebar, Sidebar]:
        return self.top, self.footer

    def count(self) -> int:
        return len(self.items)

    @property
    def current(self) -> int:
        if self.top.currentRow() >= 0:
            return self.top.currentRow()
        if self.footer.currentRow() >= 0:
            return len(self.top.items) + self.footer.currentRow()
        return -1

    def set_current(self, index: int, emit: bool = True) -> None:
        if not 0 <= index < self.count() or index == self.current:
            return
        top = len(self.top.items)
        if index < top:
            self.footer.clear_current()
            self.top.set_current(index, emit=emit)
        else:
            self.top.clear_current()
            self.footer.set_current(index - top, emit=emit)

    def _chosen(self, index: int) -> None:
        # The other list lets go of its selection.
        if index < len(self.top.items):
            self.footer.clear_current()
        else:
            self.top.clear_current()
        self.current_changed.emit(index)

    def set_window_active(self, active: bool) -> None:
        for sidebar in self.lists:
            sidebar.set_window_active(active)

    def shows_focus(self) -> bool:
        return any(sidebar.shows_focus() for sidebar in self.lists)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        """Up and down cross from the pages to the footer and back, and
        Home and End reach the first and last item of both."""
        if event.type() != QEvent.Type.KeyPress:
            return False
        key = event.key()
        top, footer = self.top, self.footer
        if watched is top:
            last_row = top.currentRow() == top.count() - 1
            if (key == Qt.Key.Key_Down and last_row) or key == Qt.Key.Key_End:
                self._move_to(footer, footer.count() - 1 if key == Qt.Key.Key_End else 0)
                return True
        elif watched is footer:
            first_row = footer.currentRow() <= 0
            if (key == Qt.Key.Key_Up and first_row) or key == Qt.Key.Key_Home:
                self._move_to(top, 0 if key == Qt.Key.Key_Home else top.count() - 1)
                return True
        return False

    def _move_to(self, sidebar: Sidebar, row: int) -> None:
        sidebar._keyboard_focus = True
        sidebar.setFocus(Qt.FocusReason.OtherFocusReason)
        sidebar._keyboard_focus = True
        sidebar.set_current(row)
        sidebar.viewport().update()


# -- SettingsExpander --------------------------------------------------------------

class Expander(QWidget):
    """WinUI's SettingsExpander: a card whose header carries its control and
    a chevron; the rows inside show when it is expanded. Clicking the header
    (or the chevron, or Space on it) expands and collapses it."""

    toggled = Signal(bool)

    def __init__(
        self,
        title: str,
        detail: str,
        trailing: Optional[QWidget],
        icon: Optional[QWidget],
        accessible_name: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        # A plain glyph button, as WinUI's: not a toggle, which Windows 11's
        # style would fill with the accent colour while expanded.
        self._expanded = False
        self.chevron = QToolButton()
        self.chevron.setAutoRaise(True)
        self.chevron.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.chevron.setIconSize(QSize(12, 12))
        self.chevron.setFixedSize(32, 32)
        self.chevron.setAccessibleName(accessible_name)
        self.chevron.clicked.connect(lambda: self.set_expanded(not self._expanded))
        holder = QWidget()
        holder_layout = QHBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.setSpacing(8)
        if trailing is not None:
            holder_layout.addWidget(trailing)
        holder_layout.addWidget(self.chevron)
        self.header = Row(title, detail, holder, icon)
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header.installEventFilter(self)
        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(0)
        self.content.setVisible(False)
        self.rows: list[QWidget] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.header)
        layout.addWidget(self.content)
        self._sync_chevron()

    @property
    def expanded(self) -> bool:
        return self._expanded

    def add(self, row: QWidget) -> QWidget:
        """A row of the expanded content, indented to line up with the
        header's title, as WinUI's SettingsCard items are."""
        row.setContentsMargins(36, 0, 0, 0)
        self.rows.append(row)
        self.content_layout.addWidget(row)
        return row

    def set_expanded(self, expanded: bool) -> None:
        expanded = bool(expanded)
        if expanded == self._expanded:
            return
        self._expanded = expanded
        self.content.setVisible(expanded)
        self._sync_chevron()
        self.update()
        self.toggled.emit(expanded)

    def _sync_chevron(self) -> None:
        self.chevron.setIcon(symbol_icon("chevron_up" if self.expanded else "chevron_down", 12))
        self.chevron.setAccessibleDescription("Expanded" if self.expanded else "Collapsed")

    def restyle(self) -> None:
        self._sync_chevron()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.header and event.type() == QEvent.Type.MouseButtonRelease:
            if event.button() == Qt.MouseButton.LeftButton and self.header.rect().contains(event.position().toPoint()):
                self.set_expanded(not self.expanded)
                return True
        return False

    def paintEvent(self, _event) -> None:  # noqa: N802
        lk = look()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, lk.radius, lk.radius)
        painter.setPen(QPen(lk.section_border, 1))
        painter.setBrush(lk.section)
        painter.drawPath(path)
        if not self.expanded:
            return
        # The expanded rows sit on the card's secondary fill, under a line.
        top = self.content.geometry().top()
        lower = QPainterPath()
        lower.addRect(QRectF(rect.left(), top, rect.width(), rect.bottom() - top))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillPath(lower.intersected(path), with_alpha(lk.section, lk.section.alphaF() * 0.6))
        painter.setPen(QPen(lk.separator, 1))
        painter.drawLine(QPointF(rect.left() + 1, top + 0.5), QPointF(rect.right() - 1, top + 0.5))
        visible = [row for row in self.rows if row.isVisible()]
        for row in visible[:-1]:
            y = self.content.geometry().top() + row.geometry().bottom() + 0.5
            painter.drawLine(QPointF(rect.left() + 52, y), QPointF(rect.right() - 16, y))


# -- InfoBar -----------------------------------------------------------------------

class InfoBar(QWidget):
    """WinUI's InfoBar, informational: an accent "i", a title and a
    message, an optional action, and a close button. Inline and persistent
    until closed, for something the user should see once."""

    closed = Signal()

    def __init__(self, title: str, message: str, action: Optional[QWidget] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAccessibleName(title)
        self.setAccessibleDescription(message)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(48, 12, 8, 12)
        layout.setSpacing(12)
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        self.title = TextLabel(title, "headline")
        self.title.setWordWrap(True)
        self.message = TextLabel(message, "body", "text")
        self.message.setWordWrap(True)
        text.addWidget(self.title)
        text.addWidget(self.message)
        if action is not None:
            row = QHBoxLayout()
            row.setContentsMargins(0, 8, 0, 0)
            row.addWidget(action)
            row.addStretch(1)
            text.addLayout(row)
        layout.addLayout(text, 1)
        self.close_button = QToolButton()
        self.close_button.setAutoRaise(True)
        self.close_button.setIcon(symbol_icon("close", 12))
        self.close_button.setIconSize(QSize(12, 12))
        self.close_button.setFixedSize(32, 32)
        self.close_button.setAccessibleName("Close")
        self.close_button.setToolTip("Close")
        self.close_button.clicked.connect(self._close)
        layout.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignTop)

    def _close(self) -> None:
        self.hide()
        self.closed.emit()

    def restyle(self) -> None:
        self.close_button.setIcon(symbol_icon("close", 12))

    def paintEvent(self, _event) -> None:  # noqa: N802
        lk = look()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(lk.section_border, 1))
        fill = with_alpha(lk.text, 0.03) if lk.dark else with_alpha(lk.pane.lighter(102), 0.9)
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, 4, 4)
        # The severity icon: a white "i" on an accent disc.
        disc = QRectF(16, 15, 16, 16)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(lk.accent)
        painter.drawEllipse(disc)
        ink = lk.pane if lk.dark else Qt.GlobalColor.white
        painter.setBrush(ink)
        centre = disc.center().x()
        painter.drawEllipse(QPointF(centre, disc.top() + 4.5), 1.1, 1.1)
        painter.drawRoundedRect(QRectF(centre - 1.0, disc.top() + 6.6, 2.0, 6.0), 1.0, 1.0)
