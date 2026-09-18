"""Render the app icon into the files the installers need.

    python installer/make_icons.py

Writes installer/assets/icon.png (1024 px preview), icon.ico (Windows) and,
on macOS, icon.icns plus dmg-background.tiff, the picture behind the icons in
the disk image window. The icon artwork lives in app/ui/icons.py, so the
running app and the installed one always show the same picture.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "installer" / "assets"
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from run import _unhide_qt_plugins  # noqa: E402

_unhide_qt_plugins()

from PySide6.QtCore import QBuffer, QIODevice, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)

from app.ui.icons import render_app_icon  # noqa: E402


def png_bytes(size: int) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    render_app_icon(size).save(buffer, "PNG")
    return bytes(buffer.data())


def write_ico(path: Path, sizes: tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256)) -> None:
    """An .ico is a small directory of images; PNG entries are allowed."""
    images = [png_bytes(size) for size in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b""
    for size, data in zip(sizes, images):
        edge = 0 if size >= 256 else size  # 0 means 256 in this format
        entries += struct.pack("<BBBBHHII", edge, edge, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    path.write_bytes(header + entries + b"".join(images))


def write_icns(path: Path) -> None:
    iconset = Path(tempfile.mkdtemp()) / "icon.iconset"
    iconset.mkdir()
    for size in (16, 32, 128, 256, 512):
        (iconset / f"icon_{size}x{size}.png").write_bytes(png_bytes(size))
        (iconset / f"icon_{size}x{size}@2x.png").write_bytes(png_bytes(size * 2))
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(path)], check=True)
    shutil.rmtree(iconset.parent, ignore_errors=True)


# The disk image window, in points. Icon centres match installer/dmg_settings.py.
DMG_SIZE = (640, 360)
APP_CENTRE = (170, 170)
APPLICATIONS_CENTRE = (470, 170)


def render_dmg_background(scale: int) -> QImage:
    """Just the arrow. The window title names the app; the arrow is the instruction."""
    width, height = DMG_SIZE
    image = QImage(width * scale, height * scale, QImage.Format.Format_ARGB32_Premultiplied)
    image.setDotsPerMeterX(int(2835 * scale))  # 72 dpi times the scale
    image.setDotsPerMeterY(int(2835 * scale))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.scale(scale, scale)

    gradient = QLinearGradient(0, 0, 0, height)
    gradient.setColorAt(0.0, QColor("#fbfbfd"))
    gradient.setColorAt(1.0, QColor("#eeeff3"))
    painter.fillRect(QRectF(0, 0, width, height), gradient)

    # The arrow runs between the two icons, clear of their 128 pt artwork.
    y = APP_CENTRE[1]
    start = QPointF(APP_CENTRE[0] + 84, y)
    end = QPointF(APPLICATIONS_CENTRE[0] - 84, y)
    pen = QPen(QColor(142, 142, 147), 5)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.drawLine(start, QPointF(end.x() - 2, y))
    head = QPainterPath()
    head.moveTo(end.x() - 16, y - 14)
    head.lineTo(end.x(), y)
    head.lineTo(end.x() - 16, y + 14)
    painter.drawPath(head)

    painter.end()
    return image


def write_dmg_background(path: Path) -> None:
    """A two-resolution TIFF, so the backdrop is sharp on Retina displays."""
    folder = Path(tempfile.mkdtemp())
    one, two = folder / "background.png", folder / "background@2x.png"
    render_dmg_background(1).save(str(one), "PNG")
    render_dmg_background(2).save(str(two), "PNG")
    subprocess.run(["tiffutil", "-cathidpicheck", str(one), str(two), "-out", str(path)], check=True)
    shutil.rmtree(folder, ignore_errors=True)


def main() -> None:
    QGuiApplication.instance() or QGuiApplication([])
    ASSETS.mkdir(parents=True, exist_ok=True)
    (ASSETS / "icon.png").write_bytes(png_bytes(1024))
    write_ico(ASSETS / "icon.ico")
    if sys.platform == "darwin":
        write_icns(ASSETS / "icon.icns")
        write_dmg_background(ASSETS / "dmg-background.tiff")
    print(f"Icons written to {ASSETS}")


if __name__ == "__main__":
    main()
