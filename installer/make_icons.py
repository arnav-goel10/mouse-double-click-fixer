"""Render the app icon into the files the installers need.

    python installer/make_icons.py

Writes installer/assets/icon.png (1024 px preview), icon.ico (Windows) and,
on macOS, icon.icns. The artwork itself lives in app/ui/icons.py, so the
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

from PySide6.QtCore import QBuffer, QIODevice  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

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


def main() -> None:
    QGuiApplication.instance() or QGuiApplication([])
    ASSETS.mkdir(parents=True, exist_ok=True)
    (ASSETS / "icon.png").write_bytes(png_bytes(1024))
    write_ico(ASSETS / "icon.ico")
    if sys.platform == "darwin":
        write_icns(ASSETS / "icon.icns")
    print(f"Icons written to {ASSETS}")


if __name__ == "__main__":
    main()
