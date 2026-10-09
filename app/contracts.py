"""The 1.0 input contracts, as the app side sees them (design section 2).

STAND-IN MODULE, for INPUT to absorb. INPUT owns the real definitions:
`Button`, `SIDE_BUTTONS` and `DEFAULT_THRESHOLD_MS` in app/core.py, and
`FilterConfig`, `DeviceInfo`, `GlobalClickFilter` and `WHEEL_DEFAULT_MS` in
app/platform.py. Every name here is the real one as soon as those modules
define it, and a minimal compatible copy until then, so the app and its tests
run on either side of the merge. After the merge this module can go: import
the names from core and platform instead. The controller builds its
FilterConfig in one place (controller.config_from_settings), so nothing else
changes.

The copies:

* `Button` adds BACK and FORWARD to core's three buttons.
* `GlobalClickFilter` is today's single-window filter behind the 1.0
  interface (`_SingleWindowFilter`): it takes a FilterConfig, filters the
  left, right and middle buttons with the left button's window, and knows
  nothing of side buttons, the wheel, excluded apps or devices.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional

from . import core
from . import platform as _platform

# -- buttons ---------------------------------------------------------------------

if hasattr(core.Button, "BACK"):
    Button = core.Button
    DEFAULT_THRESHOLD_MS: int = core.DEFAULT_THRESHOLD_MS
    SIDE_BUTTONS = core.SIDE_BUTTONS
else:  # stand-in until INPUT's core.py lands

    class Button(str, enum.Enum):  # type: ignore[no-redef]
        LEFT = "left"
        RIGHT = "right"
        MIDDLE = "middle"
        BACK = "back"
        FORWARD = "forward"

    #: The window for a new or uncalibrated install (was 60 before 1.0).
    DEFAULT_THRESHOLD_MS = 46
    #: Filtered by the drop rule only: their releases are never held.
    SIDE_BUTTONS = frozenset({Button.BACK, Button.FORWARD})


#: What people call each button, in a sentence ("the back button") and as a
#: title ("Back button"). Kept here, not on the enum, so it never depends on
#: how core spells its own labels.
BUTTON_NAMES = {"left": "Left", "right": "Right", "middle": "Middle", "back": "Back", "forward": "Forward"}


def as_button(value: Any) -> Button:
    """A Button from a Button of either definition, or its name. Qt signals
    deliver str enums as plain strings."""
    return Button(getattr(value, "value", value))


def button_name(button: Any) -> str:
    return BUTTON_NAMES[as_button(button).value]


#: Device kinds that are never filtered: their clicks come from taps and
#: touches, not a switch that can bounce.
TOUCH_KINDS = frozenset({"trackpad", "touchscreen", "pen"})

# -- filter configuration ----------------------------------------------------------

if hasattr(_platform, "FilterConfig"):
    FilterConfig = _platform.FilterConfig
    DeviceInfo = _platform.DeviceInfo
    GlobalClickFilter = _platform.GlobalClickFilter
    WHEEL_DEFAULT_MS: int = _platform.WHEEL_DEFAULT_MS
else:  # stand-ins until INPUT's platform.py lands

    #: The wheel-reversal window. DoubleClickFix (nenning), the most used
    #: competitor, sets 50 ms the moment its wheel filter is switched on
    #: (InteractiveForm.cs, OnButtonEnabledCheckedChanged); MouseFix
    #: (matreshka15) ships 30 ms in its default preset, 35 in "office" and
    #: 20 in "strict" (main.c, PRESET_DEFAULT). 50 ms catches the slower
    #: stray notches as well, and stays far below the time a person needs to
    #: stop a wheel and roll it back the other way, so a deliberate reversal
    #: is never dropped.
    WHEEL_DEFAULT_MS = 50

    @dataclass(frozen=True)
    class FilterConfig:  # type: ignore[no-redef]
        thresholds: Mapping[Any, int]
        buttons: frozenset
        wheel_fix: bool = False
        wheel_window_ms: int = WHEEL_DEFAULT_MS
        excluded_apps: frozenset = frozenset()
        ignored_devices: frozenset = frozenset()

    @dataclass(frozen=True)
    class DeviceInfo:  # type: ignore[no-redef]
        key: str
        name: str
        kind: str
        filtered: bool
        last_seen: float = field(default=0.0)

    class _SingleWindowFilter:
        """STAND-IN: the filter as it is before INPUT's work, behind the 1.0
        interface. Left, right and middle take the left button's window;
        everything else in the config waits for the real filter."""

        def __init__(
            self,
            config: "FilterConfig",
            on_event: Optional[Callable] = None,
            on_error: Optional[Callable] = None,
            permission_ok: Optional[Callable] = None,
            on_permission_lost: Optional[Callable] = None,
            on_device: Optional[Callable] = None,
            on_wheel: Optional[Callable] = None,
        ) -> None:
            self._inner = _platform.GlobalClickFilter(
                self._window(config),
                self._buttons(config),
                on_event=on_event,
                on_error=on_error,
                permission_ok=permission_ok,
                on_permission_lost=on_permission_lost,
            )

        @staticmethod
        def _window(config: "FilterConfig") -> int:
            return int(config.thresholds[Button.LEFT])

        @staticmethod
        def _buttons(config: "FilterConfig") -> list:
            known = {button.value for button in core.Button}
            return [core.Button(button.value) for button in config.buttons if button.value in known]

        def update(self, config: "FilterConfig") -> None:
            self._inner.update(threshold_ms=self._window(config), buttons=self._buttons(config))

        def seen_devices(self) -> list:
            return []

        def __getattr__(self, name: str) -> Any:
            # running, start, stop, tap_alive and the counters.
            return getattr(self._inner, name)

    GlobalClickFilter = _SingleWindowFilter  # type: ignore[misc,assignment]


#: Whether the names above are the stand-ins (for the report and the tests).
STAND_IN = not hasattr(_platform, "FilterConfig")
