"""Icons drawn at runtime, so the app ships without binary assets."""

from __future__ import annotations

import platform

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap


def _mouse_pixmap(size: int, body: QColor, outline: QColor, dot: QColor | None) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    unit = size / 32
    shell = QRectF(9 * unit, 4 * unit, 14 * unit, 24 * unit)
    painter.setPen(QPen(outline, 2 * unit))
    painter.setBrush(body)
    painter.drawRoundedRect(shell, 7 * unit, 9 * unit)
    painter.drawLine(shell.center().x(), shell.top() + 1.5 * unit, shell.center().x(), 13 * unit)

    if dot is not None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(dot)
        painter.drawEllipse(QRectF(21 * unit, 2 * unit, 9 * unit, 9 * unit))
    painter.end()
    return pixmap


def checkmark_file(directory, colour: str = "#ffffff", size: int = 14) -> str:
    """Write the tick used inside checked checkboxes and return its path.

    Qt stylesheets can only point at a file, so the glyph is drawn once at
    startup instead of shipping an image with the app.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "checkmark.png"
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(colour), max(1.6, size / 7))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    unit = size / 14
    painter.drawPolyline(
        [
            QPointF(3 * unit, 7.5 * unit),
            QPointF(5.8 * unit, 10.3 * unit),
            QPointF(11 * unit, 4 * unit),
        ]
    )
    painter.end()
    pixmap.save(str(path))
    return path.as_posix()


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 32, 64, 128, 256):
        icon.addPixmap(_mouse_pixmap(size, QColor("#3055ee"), QColor("#1b2c66"), None))
    return icon


def tray_icon(active: bool) -> QIcon:
    """The menu bar / notification area icon.

    On macOS it is a template image so the system can tint it for light and
    dark menu bars; a coloured badge marks the active state elsewhere.
    """
    if platform.system() == "Darwin":
        icon = QIcon()
        for size in (18, 36, 54):
            body = QColor(0, 0, 0, 255 if active else 0)
            icon.addPixmap(_mouse_pixmap(size, body, QColor(0, 0, 0), None))
        icon.setIsMask(True)
        return icon

    accent = QColor("#0f9d58") if active else QColor("#98a2b3")
    icon = QIcon()
    for size in (16, 24, 32, 64):
        icon.addPixmap(_mouse_pixmap(size, accent, QColor("#1f2430"), None))
    return icon
