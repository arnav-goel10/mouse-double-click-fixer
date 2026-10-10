"""Platform look: colours, metrics and fonts for macOS and Windows 11.

Native controls (buttons, sliders, checkboxes, progress bars) are left to the
platform style so they look and behave exactly like the system's own. Only the
surfaces this app paints itself — the sidebar, grouped sections, the switch,
the click pad — take their colours from here, and those are derived from the
system palette wherever the system has an opinion (accent, text, window).
"""

from __future__ import annotations

import platform
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPalette

IS_MAC = platform.system() == "Darwin"
IS_WINDOWS = platform.system() == "Windows"


@dataclass(frozen=True)
class Look:
    dark: bool
    mac: bool
    accent: QColor
    text: QColor
    secondary: QColor
    tertiary: QColor
    pane: QColor          # content background when no material shows through
    sidebar: QColor       # sidebar fallback when no material is available
    section: QColor       # grouped section / card fill
    section_border: QColor
    separator: QColor
    selection: QColor     # sidebar selection pill
    hover: QColor
    track_off: QColor
    green: QColor
    red: QColor
    orange: QColor
    radius: float         # section corner radius
    row_height: int
    sidebar_width: int


def _alpha(colour: QColor, alpha: float) -> QColor:
    result = QColor(colour)
    result.setAlphaF(alpha)
    return result


def is_dark() -> bool:
    hints = QGuiApplication.styleHints()
    scheme = getattr(hints, "colorScheme", None)
    if scheme is not None and scheme() != Qt.ColorScheme.Unknown:
        return scheme() == Qt.ColorScheme.Dark
    return QGuiApplication.palette().color(QPalette.ColorRole.Window).lightness() < 128


def system_accent(dark: bool) -> QColor:
    palette = QGuiApplication.palette()
    role = getattr(QPalette.ColorRole, "Accent", QPalette.ColorRole.Highlight)
    colour = palette.color(role)
    if not colour.isValid() or colour.saturation() < 30:
        # Graphite or unknown: fall back to the platform's default blue.
        if IS_MAC:
            return QColor("#0a84ff" if dark else "#007aff")
        return QColor("#4cc2ff" if dark else "#005fb8")
    return colour


def current_look(dark: bool | None = None) -> Look:
    dark = is_dark() if dark is None else dark
    accent = system_accent(dark)
    if IS_MAC or not IS_WINDOWS:
        return _mac_look(dark, accent)
    return _windows_look(dark, accent)


def _mac_look(dark: bool, accent: QColor) -> Look:
    # Values follow AppKit's semantic colours (labelColor, separatorColor,
    # windowBackgroundColor, quaternarySystemFill, systemGreen, ...).
    if dark:
        text = QColor(255, 255, 255, 217)
        return Look(
            dark=True, mac=True, accent=accent,
            text=text,
            secondary=QColor(255, 255, 255, 140),
            tertiary=QColor(255, 255, 255, 64),
            pane=QColor("#1e1e1e"),
            sidebar=QColor("#2a2a2a"),
            section=QColor(255, 255, 255, 13),
            section_border=QColor(255, 255, 255, 20),
            separator=QColor(255, 255, 255, 26),
            selection=QColor(255, 255, 255, 26),
            hover=QColor(255, 255, 255, 13),
            track_off=QColor(255, 255, 255, 46),
            green=QColor("#30d158"), red=QColor("#ff453a"), orange=QColor("#ff9f0a"),
            radius=12.0, row_height=44, sidebar_width=210,
        )
    text = QColor(0, 0, 0, 217)
    return Look(
        dark=False, mac=True, accent=accent,
        text=text,
        secondary=QColor(0, 0, 0, 128),
        tertiary=QColor(0, 0, 0, 64),
        pane=QColor("#f5f5f5"),
        sidebar=QColor("#e8e8e8"),
        section=QColor(0, 0, 0, 9),
        section_border=QColor(0, 0, 0, 13),
        separator=QColor(0, 0, 0, 20),
        selection=QColor(0, 0, 0, 20),
        hover=QColor(0, 0, 0, 10),
        track_off=QColor(0, 0, 0, 38),
        green=QColor("#34c759"), red=QColor("#ff3b30"), orange=QColor("#ff9500"),
        radius=12.0, row_height=44, sidebar_width=210,
    )


def _windows_look(dark: bool, accent: QColor) -> Look:
    # Values follow the WinUI 3 theme resources used by Windows 11 Settings
    # (CardBackgroundFillColorDefault, CardStrokeColorDefault, ...).
    if dark:
        return Look(
            dark=True, mac=False, accent=accent,
            text=QColor("#ffffff"),
            secondary=QColor(255, 255, 255, 200),
            tertiary=QColor(255, 255, 255, 139),
            pane=QColor("#202020"),
            sidebar=QColor("#202020"),
            section=QColor(255, 255, 255, 13),
            section_border=QColor(0, 0, 0, 26),
            separator=QColor(255, 255, 255, 21),
            selection=QColor(255, 255, 255, 15),
            hover=QColor(255, 255, 255, 10),
            track_off=QColor(255, 255, 255, 139),
            green=QColor("#6ccb5f"), red=QColor("#ff99a4"), orange=QColor("#fce100"),
            radius=6.0, row_height=64, sidebar_width=260,
        )
    return Look(
        dark=False, mac=False, accent=accent,
        text=QColor(0, 0, 0, 228),
        secondary=QColor(0, 0, 0, 158),
        tertiary=QColor(0, 0, 0, 114),
        pane=QColor("#f3f3f3"),
        sidebar=QColor("#f3f3f3"),
        section=QColor(255, 255, 255, 179),
        section_border=QColor(0, 0, 0, 15),
        separator=QColor(0, 0, 0, 15),
        selection=QColor(0, 0, 0, 10),
        hover=QColor(0, 0, 0, 6),
        track_off=QColor(0, 0, 0, 158),
        green=QColor("#0f7b0f"), red=QColor("#c42b1c"), orange=QColor("#9d5d00"),
        radius=6.0, row_height=64, sidebar_width=260,
    )


# -- type ramp ---------------------------------------------------------------

def base_font() -> QFont:
    return QFont(QGuiApplication.font())


def application_font() -> QFont | None:
    """The application-wide font to set, or None to keep the system's:
    Windows 11's own UI font at its body size (14 px)."""
    if not IS_WINDOWS:
        return None
    body = QFont()
    body.setFamilies(["Segoe UI Variable Text", "Segoe UI"])
    body.setPixelSize(14)
    return body


def font(role: str) -> QFont:
    """Fonts by role, following each platform's own type ramp."""
    f = base_font()
    if IS_WINDOWS:
        sizes = {"title": 28.0, "headline": 14.0, "body": 14.0, "caption": 12.0, "large": 20.0}
        f.setFamilies(["Segoe UI Variable Display" if role in ("title", "large") else "Segoe UI Variable Text", "Segoe UI"])
        f.setPixelSize(int(sizes[role]))
        f.setWeight(QFont.Weight.DemiBold if role in ("title", "headline", "large") else QFont.Weight.Normal)
        return f
    sizes = {"title": 15.0, "headline": 13.0, "body": 13.0, "caption": 11.0, "large": 22.0}
    f.setPointSizeF(sizes[role])
    f.setWeight(QFont.Weight.Bold if role in ("title", "large") else QFont.Weight.DemiBold if role == "headline" else QFont.Weight.Normal)
    return f


def with_alpha(colour: QColor, alpha: float) -> QColor:
    return _alpha(colour, alpha)
