"""Custom-painted pieces. Everything else is a native control.

These follow each platform's own components: grouped rows and NSSwitch on
macOS, settings cards and the ToggleSwitch on Windows 11.
"""

from __future__ import annotations

from time import monotonic, perf_counter
from typing import Optional

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QItemSelectionModel,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from ..contracts import Button
from . import symbols
from .theme import IS_MAC, IS_WINDOWS, Look, current_look, font, with_alpha

# The active look, shared by every painted widget and swapped when the system
# switches between light and dark.
_LOOK: list[Look] = []


def look() -> Look:
    if not _LOOK:
        _LOOK.append(current_look())
    return _LOOK[0]


def set_look(value: Look) -> None:
    _LOOK[:] = [value]


def _rgba(colour: QColor) -> str:
    return f"rgba({colour.red()}, {colour.green()}, {colour.blue()}, {colour.alphaF():.3f})"


class TextLabel(QLabel):
    """A label with a type role and a colour role that follow the look."""

    def __init__(self, text: str = "", role: str = "body", tone: str = "text", parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.role = role
        self.tone = tone
        self.setFont(font(role))
        self.restyle()

    def restyle(self) -> None:
        colour = getattr(look(), self.tone)
        self.setStyleSheet(f"color: {_rgba(colour)}; background: transparent;")


# -- switch --------------------------------------------------------------------

#: Room for the "On"/"Off" text before a Windows toggle, gap included.
WIN_STATE_WIDTH = 40

#: Focus that leaves and comes back with the window or a menu, not by the
#: user moving it.
_RESTORED_FOCUS = (Qt.FocusReason.ActiveWindowFocusReason, Qt.FocusReason.PopupFocusReason)


class Switch(QAbstractButton):
    """NSSwitch on macOS, the WinUI ToggleSwitch on Windows.

    A checkable button underneath, so VoiceOver and Narrator hear a toggle
    with its state ("Bounce filter, on"), can flip it, and are told when it
    changes. Listen to `clicked(bool)` for the user's own changes: `toggled`
    also fires when code sets the state, which would echo a refresh back
    into the handler.
    """

    def __init__(self, parent: Optional[QWidget] = None, accessible_name: str = "") -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self._position = 0.0
        self._hovered = False
        self._keyboard_focus = False
        self._animate_next = True
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Windows puts the state ("On"/"Off") before the toggle, as WinUI does.
        self.setFixedSize(QSize(38, 22) if IS_MAC else QSize(40 + WIN_STATE_WIDTH, 20))
        self.setAccessibleName(accessible_name)
        self._animation = QPropertyAnimation(self, b"position", self)
        self._animation.setDuration(150)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._follow)

    def setChecked(self, value: bool, animate: bool = True) -> None:  # noqa: N802 - Qt naming
        """Set the state from code. Emits `toggled`, never `clicked`."""
        self._animate_next = animate
        try:
            super().setChecked(bool(value))
        finally:
            self._animate_next = True

    def _follow(self, checked: bool) -> None:
        # The knob slides for a click, and jumps for a state loaded quietly.
        self._animation.stop()
        if self._animate_next and self.isVisible():
            self._animation.setStartValue(self._position)
            self._animation.setEndValue(1.0 if checked else 0.0)
            self._animation.start()
        else:
            self._position = 1.0 if checked else 0.0
            self.update()

    def get_position(self) -> float:
        return self._position

    def set_position(self, value: float) -> None:
        self._position = float(value)
        self.update()

    position = Property(float, get_position, set_position)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        # Space is the button's own; Return flips it too, as it always has.
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.isAutoRepeat():
            self.click()
            return
        super().keyPressEvent(event)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        lk = look()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(0.4)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if not IS_MAC:
            painter.setFont(font("body"))
            painter.setPen(lk.text)
            state = QRectF(0, 0, WIN_STATE_WIDTH - 12, self.height())
            painter.drawText(state, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "On" if self.isChecked() else "Off")
            rect.setLeft(rect.left() + WIN_STATE_WIDTH)
        radius = rect.height() / 2
        p = self._position

        if IS_MAC:
            off, on = lk.track_off, lk.accent
            track = QColor(
                int(off.red() + (on.red() - off.red()) * p),
                int(off.green() + (on.green() - off.green()) * p),
                int(off.blue() + (on.blue() - off.blue()) * p),
                int(off.alpha() + (on.alpha() - off.alpha()) * p),
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(track)
            painter.drawRoundedRect(rect, radius, radius)
            knob = rect.height() - 4
            x = rect.left() + 2 + (rect.width() - knob - 4) * p
            knob_rect = QRectF(x, rect.top() + 2, knob, knob)
            painter.setBrush(QColor(0, 0, 0, 40))
            painter.drawEllipse(knob_rect.translated(0, 0.6))
            painter.setBrush(QColor("#ffffff"))
            painter.drawEllipse(knob_rect)
        else:
            if p > 0.5:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(lk.accent)
            else:
                painter.setPen(QPen(lk.track_off, 1))
                painter.setBrush(lk.hover if self._hovered else Qt.GlobalColor.transparent)
            painter.drawRoundedRect(rect, radius, radius)
            size = 14 if self._hovered else 12
            x = rect.left() + 4 + (rect.width() - 8 - size) * p + (0 if p > 0.5 else (-1 if self._hovered else 0))
            knob_rect = QRectF(x, rect.center().y() - size / 2, size, size)
            painter.setPen(Qt.PenStyle.NoPen)
            if p > 0.5:
                painter.setBrush(QColor("#000000") if lk.dark else QColor("#ffffff"))
            else:
                painter.setBrush(lk.track_off)
            painter.drawEllipse(knob_rect)

        if self.hasFocus() and self._keyboard_focus:
            painter.setOpacity(1.0)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(with_alpha(lk.accent, 0.5), 3))
            painter.drawRoundedRect(rect.adjusted(-1, -1, 1, 1), radius + 1, radius + 1)

    def focusInEvent(self, event) -> None:  # noqa: N802
        # Only keyboard focus earns a ring, as with native controls. Coming
        # back to the window (or out of a menu) restores focus without a key
        # press, but the ring the user had stays: Space still acts on this.
        reason = event.reason()
        if reason in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason):
            self._keyboard_focus = True
        elif reason not in _RESTORED_FOCUS:
            self._keyboard_focus = False
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event) -> None:  # noqa: N802
        if event.reason() not in _RESTORED_FOCUS:
            self._keyboard_focus = False
        super().focusOutEvent(event)
        self.update()


# -- sections and rows -----------------------------------------------------------

class Section(QWidget):
    """A group of rows.

    macOS: one rounded box with hairline separators, as in System Settings.
    Windows: each row is its own card with a small gap, as in Windows Settings.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.rows: list[QWidget] = []
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0 if IS_MAC else 3)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

    def add(self, row: QWidget) -> QWidget:
        self.rows.append(row)
        self._layout.addWidget(row)
        return row

    def clear(self) -> None:
        """Remove every row, for a list that is built again."""
        for row in self.rows:
            self._layout.removeWidget(row)
            row.hide()
            row.deleteLater()
        self.rows = []
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        lk = look()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        visible = [row for row in self.rows if row.isVisible()]
        if IS_MAC:
            if not visible:
                return
            rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            painter.setPen(QPen(lk.section_border, 1))
            painter.setBrush(lk.section)
            painter.drawRoundedRect(rect, lk.radius, lk.radius)
            painter.setPen(QPen(lk.separator, 1))
            for row in visible[:-1]:
                y = row.geometry().bottom() + 0.5
                inset = getattr(row, "separator_inset", 12)
                painter.drawLine(QPointF(inset, y), QPointF(self.width() - 12, y))
        else:
            for row in visible:
                rect = QRectF(row.geometry()).adjusted(0.5, 0.5, -0.5, -0.5)
                painter.setPen(QPen(lk.section_border, 1))
                painter.setBrush(lk.section)
                painter.drawRoundedRect(rect, lk.radius, lk.radius)


class Row(QWidget):
    """Title and optional detail on the leading side, a control trailing."""

    def __init__(
        self,
        title: str,
        detail: str = "",
        trailing: Optional[QWidget] = None,
        icon: Optional[QWidget] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        lk = look()
        # Single-line rows are compact; rows with a description get the
        # platform's full row height.
        self.setMinimumHeight(lk.row_height if detail else (36 if IS_MAC else 52))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8) if IS_MAC else layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10 if IS_MAC else 14)
        self.separator_inset = 12
        if icon is not None:
            layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)
            self.separator_inset = 12 + icon.sizeHint().width() + layout.spacing()

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(1 if IS_MAC else 2)
        self.title = TextLabel(title, "body", "text")
        self.title.setWordWrap(True)  # wrap rather than push the control off-screen
        self.detail = TextLabel(detail, "caption", "secondary")
        self.detail.setWordWrap(True)
        self.detail.setVisible(bool(detail))
        text.addWidget(self.title)
        text.addWidget(self.detail)
        layout.addLayout(text, 1)
        self.trailing = trailing
        if trailing is not None:
            layout.addWidget(trailing, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_detail(self, text: str) -> None:
        self.detail.setText(text)
        self.detail.setVisible(bool(text))
        lk = look()
        self.setMinimumHeight(lk.row_height if text else (36 if IS_MAC else 52))


class ValueLabel(TextLabel):
    """Right-aligned secondary text, as used for read-only values in a row."""

    def __init__(self, text: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(text, "body", "secondary", parent)
        self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)


class SymbolView(QWidget):
    """A fixed-size system symbol."""

    def __init__(self, name: str, size: int = 20, tone: str = "accent", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.name = name
        self.tone = tone
        self.setFixedSize(size, size)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colour = QColor(symbols.SYMBOLS[self.name][2]) if self.tone == "symbol" else getattr(look(), self.tone)
        symbols.paint_glyph(painter, self.name, QRectF(self.rect()), colour)


class AppIconView(QWidget):
    def __init__(self, size: int = 40, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)

    def paintEvent(self, _event) -> None:  # noqa: N802
        from .icons import render_app_icon

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        ratio = self.devicePixelRatioF() or 2.0
        image = render_app_icon(int(self.width() * ratio * 1.24))
        # The icon art is drawn on Apple's grid with a margin; crop to the tile.
        margin = image.width() * 100 / 1024
        source = QRectF(margin, margin, image.width() - 2 * margin, image.height() - 2 * margin)
        painter.drawImage(QRectF(self.rect()), image, source)


# -- sidebar ---------------------------------------------------------------------

class _SidebarDelegate(QStyledItemDelegate):
    """Paints each sidebar entry: a rounded selection pill, the system icon
    and the title, as System Settings and Windows Settings draw theirs."""

    def __init__(self, sidebar: "Sidebar") -> None:
        super().__init__(sidebar)
        self.sidebar = sidebar

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return QSize(self.sidebar.width(), self.sidebar.item_height + self.sidebar.item_gap)

    def paint(self, painter: QPainter, option, index) -> None:
        sidebar = self.sidebar
        lk = look()
        name, title = sidebar.items[index.row()]
        inset = 10 if IS_MAC else 6
        row = QRectF(option.rect)
        rect = QRectF(row.left() + inset, row.top(), row.width() - inset * 2, sidebar.item_height)
        radius = 7 if IS_MAC else 4
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        focused = selected and sidebar.shows_focus()
        ink = lk.text

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        if selected:
            if focused and IS_MAC:
                # macOS marks the focused list by drawing its selection in
                # the accent colour.
                painter.setBrush(lk.accent)
                ink = QColor("#ffffff")
            else:
                painter.setBrush(lk.selection)
            painter.drawRoundedRect(rect, radius, radius)
            if not IS_MAC:
                bar = QRectF(rect.left(), rect.center().y() - 8, 3, 16)
                painter.setBrush(lk.accent)
                painter.drawRoundedRect(bar, 1.5, 1.5)
        elif hovered and not IS_MAC:
            painter.setBrush(lk.hover)
            painter.drawRoundedRect(rect, radius, radius)
        if focused and not IS_MAC:
            # Windows draws a 2 px focus rectangle around the focused entry.
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(lk.text, 2))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), radius, radius)

        icon_size = 20 if IS_MAC else 16
        icon_left = rect.left() + (6 if IS_MAC else 14)
        icon = QRectF(icon_left, rect.center().y() - icon_size / 2, icon_size, icon_size)
        symbols.paint_sidebar_icon(painter, name, icon, ink, sidebar.window_active)
        painter.setFont(font("body"))
        painter.setPen(ink)
        text_rect = QRectF(icon.right() + (8 if IS_MAC else 14), rect.top(), rect.width(), rect.height())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, title)
        painter.restore()


class Sidebar(QListWidget):
    """The navigation list: System Settings on macOS, Settings on Windows.

    A real list underneath, so VoiceOver and Narrator find its entries
    ("Calibrate, 3 of 4, selected") and announce each move; a delegate keeps
    the platform look.
    """

    current_changed = Signal(int)

    def __init__(self, items: list[tuple[str, str]], top_inset: int = 0, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.items = items  # (symbol name, title)
        self.top_inset = top_inset
        self.window_active = True
        self.paint_background = False
        self._keyboard_focus = False
        self._emit = True
        self.setFixedWidth(look().sidebar_width)
        self.setAccessibleName("Sidebar")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # The selection shows focus; macOS's ring around the whole list would
        # draw over the sidebar material.
        self.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setUniformItemSizes(True)
        self.setMouseTracking(True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        # The window paints the sidebar (or the system material shows through).
        self.setAutoFillBackground(False)
        self.viewport().setAutoFillBackground(False)
        self.setViewportMargins(0, top_inset + (8 if IS_MAC else 12), 0, 0)
        self.setItemDelegate(_SidebarDelegate(self))
        for _name, title in items:
            item = QListWidgetItem(title, self)
            # Selectable entries, nothing more: no check box or drag for a
            # screen reader to offer.
            item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
        self.setCurrentRow(0)
        self.currentRowChanged.connect(self._on_row)

    @property
    def item_height(self) -> int:
        return 32 if IS_MAC else 38

    @property
    def item_gap(self) -> int:
        return 2 if IS_MAC else 4

    @property
    def current(self) -> int:
        return self.currentRow()

    def set_current(self, index: int, emit: bool = True) -> None:
        if 0 <= index < self.count() and index != self.currentRow():
            self._emit = emit
            try:
                self.setCurrentRow(index)
            finally:
                self._emit = True

    def set_window_active(self, active: bool) -> None:
        """macOS greys the icons of a window in the background."""
        self.window_active = active
        self.viewport().update()

    def shows_focus(self) -> bool:
        return self.hasFocus() and self._keyboard_focus

    def _on_row(self, row: int) -> None:
        self.viewport().update()
        if self._emit and row >= 0:
            self.current_changed.emit(row)

    def selectionCommand(self, index, event=None):  # noqa: N802
        # One entry is always selected: nothing deselects it (a Cmd or Ctrl
        # click would), and a click between entries changes nothing.
        if not index.isValid():
            return QItemSelectionModel.SelectionFlag.NoUpdate
        return QItemSelectionModel.SelectionFlag.ClearAndSelect

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._keyboard_focus = False
        if self.indexAt(event.position().toPoint()).isValid():
            super().mousePressEvent(event)
        else:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.viewport().update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        # Dragging across the list doesn't flip through the panes.
        if not event.buttons():
            super().mouseMoveEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        self._keyboard_focus = True
        super().keyPressEvent(event)
        self.viewport().update()

    def focusInEvent(self, event) -> None:  # noqa: N802
        reason = event.reason()
        if reason in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason):
            self._keyboard_focus = True
        elif reason not in _RESTORED_FOCUS:
            self._keyboard_focus = False
        super().focusInEvent(event)
        self.viewport().update()

    def focusOutEvent(self, event) -> None:  # noqa: N802
        if event.reason() not in _RESTORED_FOCUS:
            self._keyboard_focus = False
        super().focusOutEvent(event)
        self.viewport().update()

    def paintEvent(self, event) -> None:  # noqa: N802
        if self.paint_background:
            painter = QPainter(self.viewport())
            painter.fillRect(self.viewport().rect(), look().sidebar)
            painter.end()
        super().paintEvent(event)


# -- click pad and timeline ------------------------------------------------------

#: The mouse buttons the pad measures, as the filter names them.
PAD_BUTTONS = {
    Qt.MouseButton.LeftButton: Button.LEFT,
    Qt.MouseButton.RightButton: Button.RIGHT,
    Qt.MouseButton.MiddleButton: Button.MIDDLE,
    # Qt's names for the side buttons (X1 and X2; buttons 3 and 4 on macOS).
    Qt.MouseButton.BackButton: Button.BACK,
    Qt.MouseButton.ForwardButton: Button.FORWARD,
}


class ClickPad(QWidget):
    """A surface that measures the user's own clicks.

    It reports the release-to-press gap, the same measurement the system-wide
    filter uses, for every button the filter knows (left, right, middle, back
    and forward), each timed against its own last release.
    """

    pressed_with_gap = Signal(object, object, object)  # gap_ms, interval_ms, Button

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(170)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("Click test area")
        # A right-click here is a measurement, not a request for a menu.
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
        self._last_release: dict[Button, float] = {}
        self._last_press: dict[Button, float] = {}
        self._flash = 0.0
        self._flash_tone = "good"
        self._headline = "Click Here"
        self._caption = ""

    def set_text(self, headline: str, caption: str) -> None:
        self._headline = headline
        self._caption = caption
        self.update()

    def reset(self) -> None:
        self._last_release.clear()
        self._last_press.clear()
        self.update()

    def flash(self, bounce: bool, neutral: bool = False) -> None:
        """Tint the pad briefly: red for a bounce, the accent colour for a
        click that counted, grey for one that only started something (the
        first press of a double-click)."""
        self._flash_tone = "bounce" if bounce else "neutral" if neutral else "good"
        animation = QPropertyAnimation(self, b"flash_level", self)
        animation.setDuration(380)
        animation.setStartValue(1.0)
        animation.setEndValue(0.0)
        animation.setEasingCurve(QEasingCurve.Type.OutQuad)
        animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def get_flash_level(self) -> float:
        return self._flash

    def set_flash_level(self, value: float) -> None:
        self._flash = float(value)
        self.update()

    flash_level = Property(float, get_flash_level, set_flash_level)

    @staticmethod
    def _event_time(event) -> float:
        """When the click happened. On macOS that is the event's own stamp:
        a busy moment before it is processed must not stretch the gap being
        measured. Windows stamps events from its 15.6 ms tick, so every gap
        would read 0, 15, 16 or 31 ms, and a bounce couldn't be told from a
        quick click; there the precise clock is read as the event arrives."""
        if IS_WINDOWS:
            return perf_counter()
        stamp = event.timestamp()
        return stamp / 1000.0 if stamp else monotonic()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        button = PAD_BUTTONS.get(event.button())
        if button is None:
            return
        now = self._event_time(event)
        last_release = self._last_release.get(button)
        last_press = self._last_press.get(button)
        gap = None if last_release is None else (now - last_release) * 1000
        interval = None if last_press is None else (now - last_press) * 1000
        if gap is not None and gap < 0:
            gap = interval = None  # the event clock wrapped; start afresh
        self._last_press[button] = now
        self.pressed_with_gap.emit(gap, interval, button)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        button = PAD_BUTTONS.get(event.button())
        if button is not None:
            self._last_release[button] = self._event_time(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        lk = look()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, lk.radius, lk.radius)
        painter.fillPath(path, lk.section)
        if self._flash > 0.01:
            tint = {"bounce": lk.red, "neutral": lk.secondary, "good": lk.accent}[self._flash_tone]
            painter.fillPath(path, with_alpha(tint, 0.16 * self._flash))
        painter.setPen(QPen(lk.section_border, 1))
        painter.drawPath(path)

        # A quiet label, not a call to action: secondary colour, no caption
        # unless there is something to add.
        offset = 10 if self._caption else 0
        painter.setPen(lk.secondary)
        painter.setFont(font("large"))
        painter.drawText(rect.adjusted(0, -offset, 0, -offset), Qt.AlignmentFlag.AlignCenter, self._headline)
        if self._caption:
            painter.setFont(font("caption"))
            painter.drawText(rect.adjusted(0, 24, 0, 24), Qt.AlignmentFlag.AlignCenter, self._caption)


class GapTimeline(QWidget):
    """Recent release-to-press gaps, newest on the right."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._gaps: list[tuple[float, bool]] = []
        self._threshold = 60.0
        self.setMinimumHeight(96)
        self.setMaximumHeight(120)
        self.setAccessibleName("Recent click gaps")

    def set_threshold(self, threshold_ms: float) -> None:
        self._threshold = float(threshold_ms)
        self.update()

    def add(self, gap_ms: float, bounce: bool) -> None:
        self._gaps.append((float(gap_ms), bool(bounce)))
        del self._gaps[:-48]
        self.update()

    def clear(self) -> None:
        self._gaps.clear()
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        lk = look()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(font("caption"))
        width = float(self.width())
        baseline = self.height() - 8.0
        top = 10.0

        if not self._gaps:
            painter.setPen(lk.secondary)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No clicks yet")
            return

        label = f"{self._threshold:.0f} ms"
        label_width = painter.fontMetrics().horizontalAdvance(label) + 10
        plot_width = width - label_width
        ceiling = max(self._threshold * 4, max(gap for gap, _ in self._gaps), 120.0)

        def y_for(value: float) -> float:
            return baseline - (min(value, ceiling) / ceiling) ** 0.5 * (baseline - top)

        threshold_y = y_for(self._threshold)
        pen = QPen(with_alpha(lk.accent, 0.9), 1, Qt.PenStyle.DashLine)
        pen.setDashPattern([3, 3])
        painter.setPen(pen)
        painter.drawLine(QPointF(0, threshold_y), QPointF(plot_width - 4, threshold_y))
        painter.setPen(lk.secondary)
        painter.drawText(
            QRectF(plot_width, threshold_y - 8, label_width, 16),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            label,
        )

        slot = plot_width / 48
        bar = max(3.0, min(slot * 0.55, 8.0))
        painter.setPen(Qt.PenStyle.NoPen)
        for index, (gap, bounce) in enumerate(reversed(self._gaps)):
            x = plot_width - 4 - (index + 1) * slot + (slot - bar) / 2
            if x < 0:
                break
            y = min(y_for(gap), baseline - 3)
            painter.setBrush(lk.red if bounce else with_alpha(lk.green, 0.9))
            painter.drawRoundedRect(QRectF(x, y, bar, baseline - y), bar / 2, bar / 2)
