"""The History pane's two charts, drawn with QPainter in the app's own look.

One series each, so no legend: the row above says what is plotted. Columns
grow from one baseline with a rounded top, a gap of the surface between
them; a hairline grid stays out of the way; hovering a column shows its
value. The numbers are also in the rows beside the charts and in each
chart's accessible description, for screen readers.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Optional

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from ..wear import DayStats, Histogram
from .theme import font, with_alpha
from .widgets import look

#: Columns are never wider than this; the rest of each slot is air.
MAX_COLUMN = 24.0
#: The surface left between neighbouring columns.
GAP = 2.0
#: The rounded data end.
END_RADIUS = 4.0


def nice_ceiling(value: float, floor: float, whole: bool = False) -> float:
    """The top of the scale: a round number at or above `value` (a whole
    one with `whole`, for counts)."""
    value = max(value, floor)
    scale = 10.0 ** math.floor(math.log10(value))
    for step in (1, 2, 5, 10) if whole or scale < 1 else (1, 2, 2.5, 5, 10):
        if step * scale >= value - 1e-9:
            return step * scale
    return 10 * scale


def column(painter: QPainter, rect: QRectF) -> None:
    """A column with a rounded top and a square foot on the baseline."""
    if rect.height() <= 0 or rect.width() <= 0:
        return
    radius = min(END_RADIUS, rect.width() / 2, rect.height())
    path = QPainterPath()
    path.moveTo(rect.bottomLeft())
    path.lineTo(rect.left(), rect.top() + radius)
    path.quadTo(rect.topLeft(), QPointF(rect.left() + radius, rect.top()))
    path.lineTo(rect.right() - radius, rect.top())
    path.quadTo(rect.topRight(), QPointF(rect.right(), rect.top() + radius))
    path.lineTo(rect.bottomRight())
    path.closeSubpath()
    painter.drawPath(path)


def _rate(value: Optional[float]) -> str:
    if value is None:
        return "no clicks"
    return f"{value:.1f}" if value < 10 else f"{value:.0f}"


class _ColumnChart(QWidget):
    """Shared layout: a plot area with room for the scale on the left and
    labels underneath, and hover tooltips per column."""

    LEFT = 34.0
    BOTTOM = 24.0
    TOP = 8.0

    def __init__(self, empty_text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.empty_text = empty_text
        self.setMinimumHeight(150)
        self.setMaximumHeight(190)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMouseTracking(True)
        # Column rectangles and their tooltips, from the last paint.
        self._targets: list[tuple[QRectF, str]] = []

    def plot_rect(self) -> QRectF:
        return QRectF(self.LEFT, self.TOP, self.width() - self.LEFT - 6, self.height() - self.TOP - self.BOTTOM)

    def slots(self, count: int) -> list[QRectF]:
        """`count` equal slots across the plot, each its full height."""
        plot = self.plot_rect()
        width = plot.width() / max(1, count)
        return [QRectF(plot.left() + index * width, plot.top(), width, plot.height()) for index in range(count)]

    @staticmethod
    def column_rect(slot: QRectF, top_y: float, baseline: float) -> QRectF:
        width = max(2.0, min(MAX_COLUMN, slot.width() - GAP))
        return QRectF(slot.center().x() - width / 2, top_y, width, baseline - top_y)

    def draw_scale(self, painter: QPainter, ceiling: float, text: str) -> None:
        """Hairlines at zero, half and the top, the top one labelled."""
        lk = look()
        plot = self.plot_rect()
        painter.setPen(QPen(lk.separator, 1))
        for fraction in (0.0, 0.5, 1.0):
            y = round(plot.bottom() - fraction * plot.height()) + 0.5
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        painter.setPen(lk.secondary)
        painter.setFont(font("caption"))
        painter.drawText(
            QRectF(0, plot.top() - 8, self.LEFT - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, text
        )
        painter.drawText(
            QRectF(0, plot.bottom() - 8, self.LEFT - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "0"
        )

    def draw_empty(self, painter: QPainter) -> None:
        painter.setPen(look().secondary)
        painter.setFont(font("caption"))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.empty_text)

    def event(self, event) -> bool:  # noqa: N802 - Qt naming
        if event.type() == QEvent.Type.ToolTip:
            point = QPointF(event.pos())
            for rect, text in self._targets:
                # The whole slot height answers, not just the column.
                target = QRectF(rect.left() - GAP, self.plot_rect().top(), rect.width() + 2 * GAP, self.plot_rect().height())
                if target.contains(point):
                    QToolTip.showText(event.globalPos(), text, self)
                    return True
            QToolTip.hideText()
            event.ignore()
            return True
        return super().event(event)


class DailyRateChart(_ColumnChart):
    """Bounces per 100 clicks for each of the last days, oldest on the left."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("No clicks recorded yet", parent)
        self.days: list[DayStats] = []
        self.setAccessibleName("Bounces per 100 clicks, day by day")

    def set_days(self, days: list[DayStats]) -> None:
        self.days = list(days)
        used = [day for day in self.days if day.rate is not None]
        if used:
            worst = max(used, key=lambda day: day.rate or 0.0)
            self.setAccessibleDescription(
                f"{len(used)} of the last {len(self.days)} days had clicks. Highest: "
                f"{_rate(worst.rate)} bounces per 100 clicks on {_day(worst.day)}. "
                f"Latest: {_rate(used[-1].rate)} on {_day(used[-1].day)}."
            )
        else:
            self.setAccessibleDescription(self.empty_text)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        lk = look()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._targets = []
        rates = [day.rate for day in self.days]
        if not any(rate is not None for rate in rates):
            self.draw_empty(painter)
            return
        ceiling = nice_ceiling(max(rate for rate in rates if rate is not None), 1.0)
        self.draw_scale(painter, ceiling, f"{ceiling:g}")
        plot = self.plot_rect()
        baseline = plot.bottom()
        painter.setPen(Qt.PenStyle.NoPen)
        slots = self.slots(len(self.days))
        for slot, day in zip(slots, self.days):
            rect = self.column_rect(slot, baseline, baseline)
            if day.rate is None:
                text = f"{_day(day.day)}: no clicks"
            else:
                height = max(1.5, day.rate / ceiling * plot.height())
                rect = self.column_rect(slot, baseline - height, baseline)
                painter.setBrush(lk.accent)
                column(painter, rect)
                text = (
                    f"{_day(day.day)}: {_rate(day.rate)} bounces per 100 clicks "
                    f"({day.bounces:,} in {day.clicks:,} clicks)"
                )
            self._targets.append((rect, text))
        # The first and last day, under their columns.
        painter.setPen(lk.secondary)
        painter.setFont(font("caption"))
        if slots:
            below = QRectF(plot.left(), baseline + 4, plot.width(), self.BOTTOM - 4)
            painter.drawText(below, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, _day(self.days[0].day))
            painter.drawText(below, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, "Today")


class GapHistogram(_ColumnChart):
    """How long after a release the blocked bounces came, in 2 ms bins up
    to the window, and those beyond it."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("No bounces recorded yet", parent)
        self.histogram: Optional[Histogram] = None
        self.setAccessibleName("Bounce gaps, in milliseconds")

    def set_histogram(self, histogram: Histogram) -> None:
        self.histogram = histogram
        if histogram.total:
            peak_low, peak = max(histogram.bins, key=lambda entry: entry[1]) if histogram.bins else (0, 0)
            longest = max((low for low, count in histogram.bins if count), default=None)
            parts = [f"{histogram.total:,} bounces."]
            if peak:
                parts.append(f"Most came {peak_low} to {peak_low + 2} ms after a release.")
            if longest is not None:
                parts.append(f"The longest bin with any is {longest} to {longest + 2} ms; the window is {histogram.window_ms} ms.")
            if histogram.overflow:
                parts.append(f"{histogram.overflow:,} came after a longer window that day.")
            self.setAccessibleDescription(" ".join(parts))
        else:
            self.setAccessibleDescription(self.empty_text)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        lk = look()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._targets = []
        histogram = self.histogram
        if histogram is None or not histogram.total:
            self.draw_empty(painter)
            return
        entries = [(f"{low}–{low + 2} ms", count) for low, count in histogram.bins]
        if histogram.overflow:
            entries.append((f"over {histogram.window_ms} ms", histogram.overflow))
        ceiling = nice_ceiling(max(count for _label, count in entries), 1.0, whole=True)
        self.draw_scale(painter, ceiling, f"{ceiling:,.0f}")
        plot = self.plot_rect()
        baseline = plot.bottom()
        slots = self.slots(len(entries))
        painter.setPen(Qt.PenStyle.NoPen)
        for index, (slot, (text, count)) in enumerate(zip(slots, entries)):
            overflow = histogram.overflow and index == len(entries) - 1
            height = count / ceiling * plot.height() if count else 0.0
            rect = self.column_rect(slot, baseline - height, baseline)
            painter.setBrush(with_alpha(lk.secondary, 0.6) if overflow else lk.accent)
            column(painter, rect)
            self._targets.append((rect, f"{text}: {count:,} bounce{'s' if count != 1 else ''}"))
        painter.setPen(lk.secondary)
        painter.setFont(font("caption"))
        below = QRectF(plot.left(), baseline + 4, plot.width(), self.BOTTOM - 4)
        painter.drawText(below, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, "0 ms")
        end = "over" if histogram.overflow else f"{histogram.window_ms} ms"
        painter.drawText(below, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, end)


def _day(key: str) -> str:
    """"9 Oct" for "2026-10-09"."""
    try:
        moment = date.fromisoformat(key)
    except ValueError:
        return key
    return f"{moment.day} {moment.strftime('%b')}"
