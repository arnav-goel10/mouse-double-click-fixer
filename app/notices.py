"""Where the third-party notices are, and opening them.

THIRD_PARTY_NOTICES.md lists the software the app is built on (Qt and Qt for
Python under the LGPL among it), with exact versions, sources and licence
texts. Every build carries its own copy, written for what that build bundles
(tools/make_notices.py, run by the PyInstaller spec).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

NOTICES_FILE = "THIRD_PARTY_NOTICES.md"


def candidates() -> List[Path]:
    """Where to look, most specific first."""
    found = []
    if getattr(sys, "frozen", False):
        bundled = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        found.append(bundled / NOTICES_FILE)  # Windows (and macOS, as a link)
        found.append(bundled.parent / "Resources" / NOTICES_FILE)  # macOS: Contents/Resources
        found.append(Path(sys.executable).parent / NOTICES_FILE)  # next to the program
    found.append(Path(__file__).resolve().parent.parent / NOTICES_FILE)  # a source checkout
    return found


def notices_path() -> Optional[Path]:
    """The notices file, or None if this copy has none."""
    for path in candidates():
        if path.is_file():
            return path.resolve()
    return None


def open_file(path: Path) -> bool:
    """Open `path` in the system's viewer for it. False if none would."""
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))))
