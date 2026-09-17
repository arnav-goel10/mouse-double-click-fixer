"""Reusable widgets: the toggle, the click pad, the gap timeline and tiles."""

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
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .theme import Palette


def scaled_font(base: QFont, delta: int, bold: bool = False) -> QFont:
    """Grow a font by `delta` points.

    The stylesheet sizes text in pixels, which leaves `pointSizeF()` at -1, so
    the pixel size has to be grown instead when it is set.
    """
    font = QFont(base)
    if font.pixelSize() > 0:
        font.setPixelSize(font.pixelSize() + delta)
    else:
        font.setPointSizeF(max(8.0, font.pointSizeF() + delta * 0.75))
    font.setBold(bold)
    return font


class Card(QFrame):
    """A rounded panel with a vertical layout."""

    def __init__(self, parent: Optional[QWidget] = None, spacing: int = 12, margins: int = 18) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(margins, margins, margins, margins)
        self.body.setSpacing(spacing)


class ToggleSwitch(QWidget):
    """An animated on/off switch."""

    toggled = Signal(bool)

    def __init__(self, palette: Palette, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._checked = False
        self._position = 0.0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(QSize(52, 30))
        self._animation = QPropertyAnimation(self, b"position", self)
        self._animation.setDuration(160)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.setAccessibleName("Filter switch")

    def apply_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.update()

    def isChecked(self) -> bool:  # noqa: N802 - matches Qt naming
        return self._checked

    def setChecked(self, value: bool) -> None:  # noqa: N802 - matches Qt naming
        value = bool(value)
        if value == self._checked:
            return
        self._checked = value
        self._animation.stop()
        self._animation.setStartValue(self._position)
        self._animation.setEndValue(1.0 if value else 0.0)
        self._animation.start()

    def get_position(self) -> float:
        return self._position

    def set_position(self, value: float) -> None:
        self._position = float(value)
        self.update()

    position = Property(float, get_position, set_position)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._checked)
            self.toggled.emit(self._checked)
        event.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return):
            self.setChecked(not self._checked)
            self.toggled.emit(self._checked)
            return
        super().keyPressEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(1, 4, self.width() - 2, self.height() - 8)
        off = QColor(self._palette.track)
        on = QColor(self._palette.good)
        blend = QColor(
            int(off.red() + (on.red() - off.red()) * self._position),
            int(off.green() + (on.green() - off.green()) * self._position),
            int(off.blue() + (on.blue() - off.blue()) * self._position),
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(blend)
        painter.drawRoundedRect(track, track.height() / 2, track.height() / 2)

        travel = self.width() - self.height() + 2
        centre = QPointF(self.height() / 2 - 1 + travel * self._position, self.height() / 2)
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(centre, self.height() / 2 - 5, self.height() / 2 - 5)


class StatTile(QFrame):
    """A small labelled number."""

    def __init__(self, label: str, value: str = "--", unit: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("innerCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(2)
        self.caption = QLabel(label.upper())
        self.caption.setObjectName("cardLabel")
        row = QHBoxLayout()
        row.setSpacing(4)
        row.setContentsMargins(0, 0, 0, 0)
        self.value = QLabel(value)
        self.value.setObjectName("metric")
        self.unit = QLabel(unit)
        self.unit.setObjectName("metricUnit")
        row.addWidget(self.value)
        row.addWidget(self.unit, alignment=Qt.AlignmentFlag.AlignBottom)
        row.addStretch(1)
        layout.addWidget(self.caption)
        layout.addLayout(row)

    def set_value(self, value: str, unit: Optional[str] = None) -> None:
        self.value.setText(value)
        if unit is not None:
            self.unit.setText(unit)


class ClickPad(QFrame):
    """A surface that measures the user's own clicks.

    It reports the release-to-press gap, which is the same measurement the
    system-wide filter uses.
    """

    pressed_with_gap = Signal(object, object)  # gap_ms, interval_ms

    def __init__(self, palette: Palette, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self.setObjectName("innerCard")
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._last_release: Optional[float] = None
        self._last_press: Optional[float] = None
        self._flash = 0.0
        self._flash_bounce = False
        self._hovered = False
        self._headline = "Click here to test"
        self._caption = "Clicks on this pad stay inside the app."

    def apply_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.update()

    def set_text(self, headline: str, caption: str) -> None:
        self._headline = headline
        self._caption = caption
        self.update()

    def reset(self) -> None:
        self._last_release = None
        self._last_press = None
        self.update()

    def flash(self, bounce: bool) -> None:
        self._flash = 1.0
        self._flash_bounce = bounce
        self.update()
        animation = QPropertyAnimation(self, b"flash_level", self)
        animation.setDuration(450)
        animation.setStartValue(1.0)
        animation.setEndValue(0.0)
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

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(8, 8, -8, -8)

        if self._flash > 0.01:
            colour = QColor(self._palette.danger if self._flash_bounce else self._palette.accent)
            colour.setAlphaF(0.20 * self._flash)
            path = QPainterPath()
            path.addRoundedRect(rect, 12, 12)
            painter.fillPath(path, colour)

        border = QColor(self._palette.accent if self._hovered else self._palette.border)
        painter.setPen(QPen(border, 1.5, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, 12, 12)

        painter.setPen(QColor(self._palette.text))
        painter.setFont(scaled_font(self.font(), 7, bold=True))
        painter.drawText(rect.adjusted(0, -16, 0, -16), Qt.AlignmentFlag.AlignCenter, self._headline)
        painter.setPen(QColor(self._palette.muted))
        painter.setFont(self.font())
        painter.drawText(rect.adjusted(0, 26, 0, 26), Qt.AlignmentFlag.AlignCenter, self._caption)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self.update()
        super().leaveEvent(event)


class GapTimeline(QWidget):
    """A strip of recent release-to-press gaps, newest on the right."""

    def __init__(self, palette: Palette, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._gaps: list[tuple[float, bool]] = []
        self._threshold = 60.0
        self.setMinimumHeight(104)
        self.setToolTip("Each bar is the pause between releasing and pressing again. Red bars are bounce.")

    def apply_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.update()

    def set_threshold(self, threshold_ms: float) -> None:
        self._threshold = float(threshold_ms)
        self.update()

    def add(self, gap_ms: float, bounce: bool) -> None:
        self._gaps.append((float(gap_ms), bool(bounce)))
        del self._gaps[:-40]
        self.update()

    def clear(self) -> None:
        self._gaps.clear()
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = self.width()
        height = self.height()
        baseline = height - 16

        painter.setPen(QPen(QColor(self._palette.border), 1))
        painter.drawLine(0, baseline, width, baseline)


        if not self._gaps:
            painter.setPen(QColor(self._palette.muted))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No clicks measured yet")
            return

        # A log-ish scale keeps 8 ms bounce visible next to a 400 ms pause.
        ceiling = max(self._threshold * 4, max(gap for gap, _ in self._gaps), 120.0)
        slot = width / max(len(self._gaps), 1)
        bar = max(4.0, min(slot * 0.6, 14.0))

        label = f"{self._threshold:.0f} ms filter"
        label_width = self.fontMetrics().horizontalAdvance(label) + 8
        threshold_y = baseline - (min(self._threshold, ceiling) / ceiling) ** 0.5 * (baseline - 12)
        painter.setPen(QPen(QColor(self._palette.accent), 1, Qt.PenStyle.DashLine))
        painter.drawLine(0, int(threshold_y), int(width - label_width), int(threshold_y))
        painter.setPen(QColor(self._palette.muted))
        painter.drawText(
            QRectF(width - label_width, threshold_y - 8, label_width, 16),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            label,
        )
        width -= label_width

        painter.setPen(Qt.PenStyle.NoPen)
        for index, (gap, bounce) in enumerate(reversed(self._gaps)):
            x = width - (index + 1) * slot + (slot - bar) / 2
            if x < 0:
                break
            scaled = (min(gap, ceiling) / ceiling) ** 0.5
            top = baseline - max(3.0, scaled * (baseline - 12))
            painter.setBrush(QColor(self._palette.danger if bounce else self._palette.good))
            painter.drawRoundedRect(QRectF(x, top, bar, baseline - top), 3, 3)


class ProgressRing(QWidget):
    """A circular progress indicator with a caption in the middle."""

    def __init__(self, palette: Palette, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._value = 0.0
        self._caption = ""
        self.setFixedSize(QSize(124, 124))

    def apply_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.update()

    def set_progress(self, value: float, caption: str) -> None:
        self._value = max(0.0, min(1.0, float(value)))
        self._caption = caption
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(9, 9, -9, -9)
        painter.setPen(QPen(QColor(self._palette.track), 9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(rect, 0, 360 * 16)
        painter.setPen(QPen(QColor(self._palette.accent), 9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(rect, 90 * 16, int(-360 * 16 * self._value))

        painter.setPen(QColor(self._palette.text))
        painter.setFont(scaled_font(self.font(), 7, bold=True))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._caption)
