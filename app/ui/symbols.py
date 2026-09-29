"""System icons: SF Symbols on macOS, Segoe Fluent Icons on Windows.

Using the platform's own icon set is most of what makes a sidebar look like it
belongs. Each lookup falls back to a plain drawn shape if the system set is
unavailable, so the layout never breaks.
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPixmap

from .theme import IS_MAC, IS_WINDOWS

# name -> (SF Symbol, Segoe Fluent Icons code point, macOS tile colour)
SYMBOLS = {
    "filter": ("cursorarrow.click.2", "", "#0a84ff"),
    "test": ("hand.tap.fill", "", "#30b158"),
    "calibrate": ("slider.horizontal.3", "", "#ff9500"),
    "general": ("gearshape.fill", "", "#8e8e93"),
    "warning": ("exclamationmark.triangle.fill", "", "#ff9f0a"),
    "ok": ("checkmark.circle.fill", "", "#30b158"),
    # Card icons, used on Windows only (Windows 11 Settings gives every card one).
    "mouse": ("computermouse", "", "#8e8e93"),
    "stopwatch": ("stopwatch", "", "#8e8e93"),
    "power": ("power", "", "#8e8e93"),
    "chart": ("chart.bar", "", "#8e8e93"),
    "sync": ("arrow.triangle.2.circlepath", "", "#8e8e93"),
}


@lru_cache(maxsize=64)
def _sf_symbol(name: str, point_size: float, scale: float) -> Optional[QImage]:
    """Render an SF Symbol as a white glyph with alpha."""
    try:
        import AppKit

        image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
        if image is None:
            return None
        config = AppKit.NSImageSymbolConfiguration.configurationWithPointSize_weight_(
            point_size, AppKit.NSFontWeightSemibold
        )
        image = image.imageWithSymbolConfiguration_(config)
        size = image.size()
        width = max(1, int(math.ceil(size.width * scale)))
        height = max(1, int(math.ceil(size.height * scale)))
        rep = AppKit.NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
            None, width, height, 8, 4, True, False, AppKit.NSDeviceRGBColorSpace, 0, 0
        )
        rep.setSize_(size)
        context = AppKit.NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
        AppKit.NSGraphicsContext.saveGraphicsState()
        AppKit.NSGraphicsContext.setCurrentContext_(context)
        rect = ((0, 0), (size.width, size.height))
        image.drawInRect_(rect)
        AppKit.NSColor.whiteColor().set()
        AppKit.NSRectFillUsingOperation(rect, AppKit.NSCompositingOperationSourceIn)
        AppKit.NSGraphicsContext.restoreGraphicsState()
        data = rep.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, None)
        result = QImage()
        if not result.loadFromData(bytes(data)):
            return None
        result.setDevicePixelRatio(scale)
        return result
    except Exception:  # noqa: BLE001 - fall back to a drawn shape
        return None


def _tinted(glyph: QImage, colour: QColor) -> QImage:
    tinted = QImage(glyph.size(), QImage.Format.Format_ARGB32_Premultiplied)
    tinted.setDevicePixelRatio(glyph.devicePixelRatio())
    tinted.fill(Qt.GlobalColor.transparent)
    painter = QPainter(tinted)
    painter.drawImage(0, 0, glyph)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(tinted.rect(), colour)
    painter.end()
    return tinted


def _draw_fallback(painter: QPainter, name: str, rect: QRectF, colour: QColor) -> None:
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(colour)
    c = rect.center()
    r = min(rect.width(), rect.height()) / 2
    if name == "general":
        painter.drawEllipse(c, r * 0.8, r * 0.8)
        painter.setBrush(Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        painter.drawEllipse(c, r * 0.32, r * 0.32)
    elif name == "calibrate":
        for index, offset in enumerate((-0.45, 0.0, 0.45)):
            y = c.y() + offset * r
            painter.drawRoundedRect(QRectF(rect.left() + r * 0.15, y - r * 0.08, r * 1.7, r * 0.16), 2, 2)
            knob = c.x() + (-0.4, 0.35, -0.1)[index] * r
            painter.drawEllipse(QPointF(knob, y), r * 0.22, r * 0.22)
    elif name == "warning":
        path = QPainterPath()
        path.moveTo(c.x(), rect.top())
        path.lineTo(rect.right(), rect.bottom())
        path.lineTo(rect.left(), rect.bottom())
        path.closeSubpath()
        painter.drawPath(path)
    else:
        body = QRectF(c.x() - r * 0.45, c.y() - r * 0.75, r * 0.9, r * 1.5)
        painter.drawRoundedRect(body, r * 0.45, r * 0.45)
    painter.restore()


def paint_glyph(painter: QPainter, name: str, rect: QRectF, colour: QColor) -> None:
    """Draw the named symbol inside `rect`, tinted `colour`."""
    sf_name, fluent, _tile = SYMBOLS[name]
    if IS_MAC:
        glyph = _sf_symbol(sf_name, rect.height() * 0.78, 2.0)
        if glyph is not None:
            tinted = _tinted(glyph, colour)
            w = tinted.width() / tinted.devicePixelRatio()
            h = tinted.height() / tinted.devicePixelRatio()
            scale = min(rect.width() / w, rect.height() / h, 1.0)
            target = QRectF(0, 0, w * scale, h * scale)
            target.moveCenter(rect.center())
            painter.drawImage(target, tinted)
            return
    if IS_WINDOWS:
        font = QFont()
        font.setFamilies(["Segoe Fluent Icons", "Segoe MDL2 Assets"])
        font.setPixelSize(int(rect.height()))
        painter.save()
        painter.setFont(font)
        painter.setPen(colour)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, fluent)
        painter.restore()
        return
    _draw_fallback(painter, name, rect.adjusted(2, 2, -2, -2), colour)


def paint_sidebar_icon(painter: QPainter, name: str, rect: QRectF, text: QColor, active: bool) -> None:
    """macOS: white symbol on a coloured rounded tile, like System Settings.
    Windows: a plain Fluent glyph in the text colour, like Windows Settings.

    macOS 27 keeps tile colour for the frontmost window only; background
    windows show the tiles in grey.
    """
    if IS_MAC:
        tile = QColor(SYMBOLS[name][2]) if active else QColor(142, 142, 147)
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(tile)
        painter.drawRoundedRect(rect, rect.width() * 0.26, rect.width() * 0.26)
        painter.restore()
        inset = rect.width() * 0.16
        paint_glyph(painter, name, rect.adjusted(inset, inset, -inset, -inset), QColor("#ffffff"))
        return
    paint_glyph(painter, name, rect, text)


def icon_pixmap(name: str, size: int, colour: QColor, ratio: float = 2.0) -> QPixmap:
    pixmap = QPixmap(int(size * ratio), int(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    paint_glyph(painter, name, QRectF(0, 0, size, size), colour)
    painter.end()
    return pixmap
