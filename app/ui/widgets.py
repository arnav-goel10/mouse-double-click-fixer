"""Custom-painted pieces. Everything else is a native control.

These follow each platform's own components: grouped rows and NSSwitch on
macOS, settings cards and the ToggleSwitch on Windows 11.
"""

from __future__ import annotations

from time import monotonic
from typing import Optional

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import symbols
from .theme import IS_MAC, Look, current_look, font, with_alpha

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

class Switch(QWidget):
    """NSSwitch on macOS, the WinUI ToggleSwitch on Windows."""

    toggled = Signal(bool)

    def __init__(self, parent: Optional[QWidget] = None, accessible_name: str = "") -> None:
        super().__init__(parent)
        self._checked = False
        self._position = 0.0
        self._hovered = False
        self._keyboard_focus = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedSize(QSize(38, 22) if IS_MAC else QSize(40, 20))
        self.setAccessibleName(accessible_name)
        self._animation = QPropertyAnimation(self, b"position", self)
        self._animation.setDuration(150)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def isChecked(self) -> bool:  # noqa: N802 - Qt naming
        return self._checked

    def setChecked(self, value: bool, animate: bool = True) -> None:  # noqa: N802 - Qt naming
        value = bool(value)
        if value == self._checked:
            return
        self._checked = value
        self._animation.stop()
        if animate and self.isVisible():
            self._animation.setStartValue(self._position)
            self._animation.setEndValue(1.0 if value else 0.0)
            self._animation.start()
        else:
            self._position = 1.0 if value else 0.0
            self.update()

    def get_position(self) -> float:
        return self._position

    def set_position(self, value: float) -> None:
        self._position = float(value)
        self.update()

    position = Property(float, get_position, set_position)

    def _flip(self) -> None:
        if not self.isEnabled():
            return
        self.setChecked(not self._checked)
        self.toggled.emit(self._checked)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self._flip()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._flip()
            return
        super().keyPressEvent(event)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        lk = look()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(0.4)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
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
        # Only keyboard focus earns a ring, as with native controls.
        self._keyboard_focus = event.reason() in (
            Qt.FocusReason.TabFocusReason,
            Qt.FocusReason.BacktabFocusReason,
        )
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event) -> None:  # noqa: N802
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

class Sidebar(QWidget):
    """The navigation list: System Settings on macOS, Settings on Windows."""

    current_changed = Signal(int)

    def __init__(self, items: list[tuple[str, str]], top_inset: int = 0, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.items = items  # (symbol name, title)
        self.current = 0
        self.top_inset = top_inset
        self._hover = -1
        self.window_active = True
        self.paint_background = False
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedWidth(look().sidebar_width)
        self.setAccessibleName("Sidebar")

    @property
    def item_height(self) -> int:
        return 32 if IS_MAC else 38

    def _item_rect(self, index: int) -> QRectF:
        top = self.top_inset + (8 if IS_MAC else 12) + index * (self.item_height + (2 if IS_MAC else 4))
        inset = 10 if IS_MAC else 6
        return QRectF(inset, top, self.width() - inset * 2, self.item_height)

    def _index_at(self, point) -> int:
        for index in range(len(self.items)):
            if self._item_rect(index).contains(QPointF(point)):
                return index
        return -1

    def set_current(self, index: int, emit: bool = True) -> None:
        if 0 <= index < len(self.items) and index != self.current:
            self.current = index
            self.update()
            if emit:
                self.current_changed.emit(index)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        index = self._index_at(event.position())
        if index >= 0:
            self.set_current(index)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        index = self._index_at(event.position())
        if index != self._hover:
            self._hover = index
            self.update()

    def leaveEvent(self, _event) -> None:  # noqa: N802
        self._hover = -1
        self.update()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Down:
            self.set_current(min(len(self.items) - 1, self.current + 1))
        elif event.key() == Qt.Key.Key_Up:
            self.set_current(max(0, self.current - 1))
        else:
            super().keyPressEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        lk = look()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.paint_background:
            painter.fillRect(self.rect(), lk.sidebar)
        painter.setFont(font("body"))
        for index, (name, title) in enumerate(self.items):
            rect = self._item_rect(index)
            selected = index == self.current
            if selected:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(lk.selection)
                painter.drawRoundedRect(rect, 7 if IS_MAC else 4, 7 if IS_MAC else 4)
                if not IS_MAC:
                    bar = QRectF(rect.left(), rect.center().y() - 8, 3, 16)
                    painter.setBrush(lk.accent)
                    painter.drawRoundedRect(bar, 1.5, 1.5)
            elif index == self._hover and not IS_MAC:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(lk.hover)
                painter.drawRoundedRect(rect, 4, 4)

            icon_size = 20 if IS_MAC else 16
            icon_left = rect.left() + (6 if IS_MAC else 14)
            icon = QRectF(icon_left, rect.center().y() - icon_size / 2, icon_size, icon_size)
            symbols.paint_sidebar_icon(painter, name, icon, lk.text, self.window_active)
            painter.setPen(lk.text)
            text_rect = QRectF(icon.right() + (8 if IS_MAC else 14), rect.top(), rect.width(), rect.height())
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, title)


# -- click pad and timeline ------------------------------------------------------

class ClickPad(QWidget):
    """A surface that measures the user's own clicks.

    It reports the release-to-press gap, the same measurement the system-wide
    filter uses.
    """

    pressed_with_gap = Signal(object, object)  # gap_ms, interval_ms

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(170)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("Click test area")
        self._last_release: Optional[float] = None
        self._last_press: Optional[float] = None
        self._flash = 0.0
        self._flash_bounce = False
        self._headline = "Click Here"
        self._caption = "Clicks here stay inside DoubleClick Fixer."

    def set_text(self, headline: str, caption: str) -> None:
        self._headline = headline
        self._caption = caption
        self.update()

    def reset(self) -> None:
        self._last_release = None
        self._last_press = None
        self.update()

    def flash(self, bounce: bool) -> None:
        self._flash_bounce = bounce
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

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        now = monotonic()
        gap = None if self._last_release is None else (now - self._last_release) * 1000
        interval = None if self._last_press is None else (now - self._last_press) * 1000
        self._last_press = now
        self.pressed_with_gap.emit(gap, interval)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._last_release = monotonic()

    def paintEvent(self, _event) -> None:  # noqa: N802
        lk = look()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, lk.radius, lk.radius)
        painter.fillPath(path, lk.section)
        if self._flash > 0.01:
            tint = lk.red if self._flash_bounce else lk.accent
            painter.fillPath(path, with_alpha(tint, 0.16 * self._flash))
        painter.setPen(QPen(lk.section_border, 1))
        painter.drawPath(path)

        painter.setPen(lk.text)
        painter.setFont(font("large"))
        painter.drawText(rect.adjusted(0, -14, 0, -14), Qt.AlignmentFlag.AlignCenter, self._headline)
        painter.setPen(lk.secondary)
        painter.setFont(font("caption"))
        painter.drawText(rect.adjusted(0, 22, 0, 22), Qt.AlignmentFlag.AlignCenter, self._caption)


class GapTimeline(QWidget):
    """Recent release-to-press gaps, newest on the right."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._gaps: list[tuple[float, bool]] = []
        self._threshold = 60.0
        self.setMinimumHeight(96)
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
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Your clicks will appear here.")
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
