"""Icons drawn at runtime, so the app ships without binary assets."""

from __future__ import annotations

import platform

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QIcon,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)


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


def render_app_icon(size: int) -> QImage:
    """The application icon: a white mouse on a blue tile.

    Drawn on Apple's icon grid (an 824-unit tile inside a 1024 canvas) so it
    sits at the same visual size as other apps in the Dock and Launchpad. The
    ripple beside the left button stands for the click the app protects.
    """
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    unit = size / 1024

    tile = QRectF(100 * unit, 100 * unit, 824 * unit, 824 * unit)
    radius = 185 * unit

    # A soft drop shadow, built from a few translucent layers.
    painter.setPen(Qt.PenStyle.NoPen)
    for step, alpha in ((14, 18), (9, 26), (5, 34)):
        painter.setBrush(QColor(8, 16, 48, alpha))
        painter.drawRoundedRect(tile.translated(0, step * unit).adjusted(-2, -2, 2, 2), radius, radius)

    gradient = QLinearGradient(tile.topLeft(), tile.bottomLeft())
    gradient.setColorAt(0.0, QColor("#5b86ff"))
    gradient.setColorAt(1.0, QColor("#2340c9"))
    painter.setBrush(gradient)
    painter.drawRoundedRect(tile, radius, radius)

    # Top highlight for depth.
    sheen = QLinearGradient(tile.topLeft(), QPointF(tile.left(), tile.center().y()))
    sheen.setColorAt(0.0, QColor(255, 255, 255, 46))
    sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
    painter.setBrush(sheen)
    painter.drawRoundedRect(tile, radius, radius)

    # The mouse body.
    body = QRectF(372 * unit, 300 * unit, 280 * unit, 440 * unit)
    painter.setBrush(QColor("#ffffff"))
    painter.drawRoundedRect(body, 140 * unit, 150 * unit)

    # Left button pressed: a tinted cap over the top-left quarter.
    split_y = body.top() + 175 * unit
    left_button = QPainterPath()
    left_button.moveTo(body.center().x(), body.top())
    left_button.lineTo(body.center().x(), split_y)
    left_button.lineTo(body.left(), split_y)
    left_button.arcTo(QRectF(body.left(), body.top(), 280 * unit, 300 * unit), 180, -90)
    left_button.closeSubpath()
    painter.setBrush(QColor("#dbe4ff"))
    painter.drawPath(left_button)

    line = QPen(QColor("#2340c9"), 14 * unit)
    line.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(line)
    painter.drawLine(QPointF(body.center().x(), body.top() + 24 * unit), QPointF(body.center().x(), split_y))
    painter.drawLine(QPointF(body.left() + 30 * unit, split_y), QPointF(body.right() - 30 * unit, split_y))

    # Click ripple beside the left button.
    ripple = QPen(QColor(255, 255, 255, 235), 22 * unit)
    ripple.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(ripple)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    centre = QPointF(body.left() + 40 * unit, body.top() + 40 * unit)
    for reach in (95, 160):
        painter.drawArc(
            QRectF(centre.x() - reach * unit, centre.y() - reach * unit, 2 * reach * unit, 2 * reach * unit),
            100 * 16,
            70 * 16,
        )
    painter.end()
    return image


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 32, 64, 128, 256, 512):
        icon.addPixmap(QPixmap.fromImage(render_app_icon(size)))
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
