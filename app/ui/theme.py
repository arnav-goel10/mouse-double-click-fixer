"""Colour tokens and the stylesheet built from them."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication


@dataclass(frozen=True)
class Palette:
    dark: bool
    bg: str
    card: str
    card_alt: str
    border: str
    text: str
    muted: str
    accent: str
    accent_soft: str
    accent_text: str
    good: str
    good_soft: str
    warn: str
    warn_soft: str
    danger: str
    danger_soft: str
    track: str


LIGHT = Palette(
    dark=False,
    bg="#f4f6fa",
    card="#ffffff",
    card_alt="#f7f9fc",
    border="#e2e6ee",
    text="#111827",
    muted="#667085",
    accent="#3055ee",
    accent_soft="#e8ecfe",
    accent_text="#ffffff",
    good="#0f9d58",
    good_soft="#e3f6ec",
    warn="#b7791f",
    warn_soft="#fdf3e2",
    danger="#d92d20",
    danger_soft="#fdecea",
    track="#dfe3ec",
)

DARK = Palette(
    dark=True,
    bg="#0f1216",
    card="#171b22",
    card_alt="#1d222b",
    border="#2a303b",
    text="#e8ebf2",
    muted="#98a2b3",
    accent="#6188ff",
    accent_soft="#1e2740",
    accent_text="#0b0e14",
    good="#34d399",
    good_soft="#12261f",
    warn="#fbbf24",
    warn_soft="#2a2213",
    danger="#f87171",
    danger_soft="#2c1718",
    track="#2a303b",
)


def system_palette() -> Palette:
    hints = QGuiApplication.styleHints()
    scheme = getattr(hints, "colorScheme", None)
    if scheme is not None and scheme() == Qt.ColorScheme.Dark:
        return DARK
    return LIGHT


def stylesheet(p: Palette, check_icon: str = "") -> str:
    checked = f"image: url({check_icon});" if check_icon else "image: none;"
    return f"""
    QWidget {{
        color: {p.text};
        font-size: 13px;
    }}
    QWidget#root {{ background: {p.bg}; }}

    QLabel#title {{ font-size: 22px; font-weight: 700; }}
    QLabel#subtitle {{ color: {p.muted}; font-size: 12px; }}
    QLabel#sectionTitle {{ font-size: 15px; font-weight: 600; }}
    QLabel#cardLabel {{ color: {p.muted}; font-size: 11px; font-weight: 600; letter-spacing: 1px; }}
    QLabel#metric {{ font-size: 24px; font-weight: 700; }}
    QLabel#metricUnit {{ color: {p.muted}; font-size: 12px; }}
    QLabel#hint {{ color: {p.muted}; }}
    QLabel#statusHeadline {{ font-size: 17px; font-weight: 600; }}

    QFrame#card {{
        background: {p.card};
        border: 1px solid {p.border};
        border-radius: 14px;
    }}
    QFrame#innerCard {{
        background: {p.card_alt};
        border: 1px solid {p.border};
        border-radius: 10px;
    }}
    QFrame#banner {{
        background: {p.warn_soft};
        border: 1px solid {p.warn};
        border-radius: 10px;
    }}
    QFrame#separator {{ background: {p.border}; border: none; }}

    QPushButton {{
        background: {p.card_alt};
        border: 1px solid {p.border};
        border-radius: 9px;
        padding: 8px 16px;
        font-weight: 600;
    }}
    QPushButton:hover {{ border-color: {p.accent}; }}
    QPushButton:disabled {{ color: {p.muted}; border-color: {p.border}; background: {p.card_alt}; }}
    QPushButton#primary {{
        background: {p.accent};
        color: {p.accent_text};
        border: 1px solid {p.accent};
    }}
    QPushButton#primary:hover {{ background: {p.accent}; border-color: {p.text}; }}
    QPushButton#primary:disabled {{ background: {p.track}; color: {p.muted}; border-color: {p.border}; }}
    QPushButton#link {{
        background: transparent;
        border: none;
        color: {p.accent};
        padding: 4px 6px;
        text-decoration: underline;
    }}

    QPushButton#navButton {{
        background: transparent;
        border: 1px solid transparent;
        border-radius: 9px;
        padding: 7px 18px;
        font-weight: 600;
        color: {p.muted};
    }}
    QPushButton#navButton:hover {{ color: {p.text}; }}
    QPushButton#navButton:checked {{
        background: {p.card};
        border-color: {p.border};
        color: {p.text};
    }}
    QFrame#navBar {{
        background: {p.card_alt};
        border: 1px solid {p.border};
        border-radius: 12px;
    }}

    QCheckBox {{ spacing: 8px; }}
    QCheckBox::indicator {{
        width: 16px; height: 16px;
        border-radius: 5px;
        border: 1px solid {p.border};
        background: {p.card};
    }}
    QCheckBox::indicator:checked {{
        background: {p.accent};
        border-color: {p.accent};
        {checked}
    }}

    QSlider::groove:horizontal {{
        height: 6px;
        border-radius: 3px;
        background: {p.track};
    }}
    QSlider::sub-page:horizontal {{
        height: 6px;
        border-radius: 3px;
        background: {p.accent};
    }}
    QSlider::handle:horizontal {{
        width: 18px; height: 18px;
        margin: -7px 0;
        border-radius: 9px;
        background: {p.card};
        border: 2px solid {p.accent};
    }}

    QSpinBox {{
        background: {p.card};
        border: 1px solid {p.border};
        border-radius: 8px;
        padding: 6px 8px;
        min-width: 70px;
    }}
    QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; }}

    QScrollArea {{ background: transparent; border: none; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QScrollBar:vertical {{
        background: transparent; width: 10px; margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {p.track}; border-radius: 5px; min-height: 30px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}

    QToolTip {{
        background: {p.card};
        color: {p.text};
        border: 1px solid {p.border};
        padding: 6px;
    }}
    """
