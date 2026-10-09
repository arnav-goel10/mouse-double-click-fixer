"""The buttons and pointing devices as the app shows and counts them.

Lasting helpers (APP's, not part of the stand-in in contracts.py): what each
button is called, turning whatever a Qt signal or a settings file carries
back into a Button, and which kinds of device are never filtered.
"""

from __future__ import annotations

from typing import Any

# INPUT's Button; until the merge contracts.py chooses it or the stand-in.
# After the merge: from .core import Button
from .contracts import Button

#: What people call each button, as a title ("Back"; "the back button" in a
#: sentence). Kept here, not on the enum, so it never depends on how core
#: spells its own labels.
BUTTON_NAMES = {"left": "Left", "right": "Right", "middle": "Middle", "back": "Back", "forward": "Forward"}

#: Device kinds that are never filtered (DeviceInfo.kind): their clicks come
#: from taps and touches, not a switch that can bounce.
TOUCH_KINDS = frozenset({"trackpad", "touchscreen", "pen"})


def as_button(value: Any) -> Button:
    """A Button from a Button or its name. Qt signals deliver str enums as
    plain strings, and settings files store the names."""
    return Button(getattr(value, "value", value))


def button_name(button: Any) -> str:
    return BUTTON_NAMES[as_button(button).value]
