"""The buttons and pointing devices as the app shows and counts them.

Lasting helpers: what each button is called, turning whatever a Qt signal or
a settings file carries back into a Button, and which kinds of device are
never filtered (core.TOUCH_KINDS, the filter's own list).
"""

from __future__ import annotations

from typing import Any

from .core import TOUCH_KINDS, Button

#: What people call each button, as a title ("Back"; "the back button" in a
#: sentence). Kept here, not on the enum, so it never depends on how core
#: spells its own labels.
BUTTON_NAMES = {"left": "Left", "right": "Right", "middle": "Middle", "back": "Back", "forward": "Forward"}

__all__ = ["BUTTON_NAMES", "TOUCH_KINDS", "Button", "as_button", "button_name"]


def as_button(value: Any) -> Button:
    """A Button from a Button or its name. Qt signals deliver str enums as
    plain strings, and settings files store the names."""
    return Button(getattr(value, "value", value))


def button_name(button: Any) -> str:
    return BUTTON_NAMES[as_button(button).value]
