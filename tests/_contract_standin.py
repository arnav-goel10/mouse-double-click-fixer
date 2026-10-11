"""A stand-in for what the 1.1 controller adds (pausing, the status sentence,
the health check, other mouse utilities, Strength), for the UI's tests and
the screenshot tool only.

The UI calls these on AppController. Until the controller has them (they are
built alongside, in app/controller.py), `controller_class()` returns a
subclass that adds a minimal version of each; once it has all of them, it
returns AppController itself and this file stands in for nothing.
test_ui_contract.py then checks that it really has every one.
"""

from __future__ import annotations

import platform
import time
from dataclasses import dataclass, field
from typing import Optional

from PySide6.QtCore import QTimer, Signal

#: What the UI calls beyond 1.0's controller: the agreed contract and the
#: setters for the settings it names.
CONTRACT = (
    "pause", "resume", "paused", "paused_until", "pause_changed", "pause_shortcut_text",
    "set_pause_shortcut_enabled", "set_auto_pause_games", "set_strength", "apply_calibration",
    "status_sentence", "health", "apply_suggestion", "conflicts", "conflicts_changed",
)


@dataclass(frozen=True)
class Suggestion:
    button: object
    from_ms: int
    to_ms: int
    reason: str


@dataclass(frozen=True)
class Health:
    verdict: str
    detail: str
    suggestion: Optional[Suggestion] = None
    kind: str = ""


@dataclass(frozen=True)
class Conflict:
    name: str
    takes: list = field(default_factory=list)
    key: str = ""


def has_contract(cls) -> bool:
    return all(hasattr(cls, name) for name in CONTRACT)


def controller_class():
    from app.controller import AppController

    if has_contract(AppController):
        return AppController
    return _stand_in(AppController)


def standing_in() -> bool:
    """Whether the tests run on the stand-in rather than the real controller."""
    from app.controller import AppController

    return controller_class() is not AppController


_MADE: dict = {}


def _clock_text(epoch: float) -> str:
    from PySide6.QtCore import QDateTime, QLocale

    moment = QDateTime.fromSecsSinceEpoch(int(epoch))
    return QLocale.system().toString(moment.time(), QLocale.FormatType.ShortFormat)


def _stand_in(base):
    if base in _MADE:
        return _MADE[base]

    class StandInController(base):
        """AppController with the 1.1 contract filled in by stand-ins."""

        pause_changed = Signal()
        conflicts_changed = Signal()

        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self._paused_until: Optional[float] = None
            self._pause_timer = QTimer(self)
            self._pause_timer.setSingleShot(True)
            self._pause_timer.timeout.connect(self.resume)
            self.stand_in_conflicts: list = []
            self.stand_in_health: Optional[Health] = None
            self.calibrations: list = []
            for key, value in (("strength", "automatic"), ("auto_pause_games", False), ("pause_shortcut_enabled", True)):
                self.settings.setdefault(key, value)

        # -- pause --------------------------------------------------------
        @property
        def paused(self) -> bool:
            return self._paused_until is not None

        @property
        def paused_until(self) -> Optional[float]:
            return self._paused_until

        @property
        def wanted(self) -> bool:
            return super().wanted or (self.paused and bool(self.settings["fix_enabled"]))

        def pause(self, minutes: int = 15) -> None:
            if not self.settings["fix_enabled"]:
                return
            self._paused_until = time.time() + 60 * int(minutes)
            self._stop_filter()
            self._pause_timer.start(int(minutes) * 60 * 1000)
            self.filter_state_changed.emit(False, "")
            self.pause_changed.emit()

        def resume(self) -> None:
            if self._paused_until is not None:
                self._paused_until = None
                self._pause_timer.stop()
                self.pause_changed.emit()
                if self.settings["fix_enabled"] and not self.suspended:
                    self.set_active(True)
                return
            super().resume()

        def set_active(self, active: bool) -> bool:
            if active and self.paused:
                return False  # a pause ends with resume(), or on its own
            if not active and self._paused_until is not None:
                self._paused_until = None
                self._pause_timer.stop()
                self.pause_changed.emit()
            return super().set_active(active)

        def shutdown(self) -> None:
            self._pause_timer.stop()
            super().shutdown()

        @property
        def pause_shortcut_text(self) -> str:
            return "⌃⌥⌘P" if platform.system() == "Darwin" else "Ctrl+Alt+Shift+P"

        def set_pause_shortcut_enabled(self, enabled: bool) -> None:
            self._store(pause_shortcut_enabled=bool(enabled))
            self.settings_changed.emit()

        def set_auto_pause_games(self, enabled: bool) -> None:
            self._store(auto_pause_games=bool(enabled))
            self.settings_changed.emit()

        # -- strength -----------------------------------------------------
        def set_strength(self, strength: str) -> None:
            if strength not in ("automatic", "custom"):
                raise ValueError(strength)
            self._apply(strength=strength)

        def apply_calibration(self, button, threshold_ms: int, fastest_double_click_ms=None) -> None:
            self.calibrations.append((button, threshold_ms, fastest_double_click_ms))
            self.set_threshold(button, threshold_ms)
            self.set_calibrated(button, True)
            self.set_button_filtered(button, True)

        # -- words --------------------------------------------------------
        def status_sentence(self) -> str:
            from app import permissions

            if self.active:
                blocked = self.session_filtered
                if not blocked:
                    return "No extra clicks seen yet."
                times = "once" if blocked == 1 else f"{blocked:,} times"
                return f"Your left button bounced {times} today. Every bounce was caught."
            if self._paused_until is not None:
                return f"Paused until {_clock_text(self._paused_until)}."
            if self.suspended and self.settings["fix_enabled"]:
                return "Paused while you test your mouse."
            if self.waiting_for_permission:
                return f"Waiting for {permissions.pane_name()} permission."
            if self.failure:
                return f"{self.failure}."
            if self.settings["fix_enabled"]:
                return "Not filtering right now."
            return "Off."

        # -- health and conflicts ----------------------------------------
        def health(self) -> Health:
            if self.stand_in_health is not None:
                return self.stand_in_health
            from app.core import Button

            totals = self.wear.totals(Button.LEFT)
            if totals.bounces:
                return Health(
                    "Your left button double-clicks on its own",
                    f"Caught {totals.bounces:,} of {totals.bounces:,} extra clicks in the last 30 days.",
                    None,
                    "bouncing",
                )
            if self.stand_in_conflicts:
                names = ", ".join(conflict.name for conflict in self.stand_in_conflicts)
                return Health(f"Another app may be changing clicks: {names}", "", None, "conflict")
            return Health("No bounce seen", "Your mouse may be fine, or it bounces only now and then.", None, "quiet")

        def apply_suggestion(self, suggestion) -> None:
            self.set_threshold(suggestion.button, suggestion.to_ms)

        def conflicts(self) -> list:
            return list(self.stand_in_conflicts)

        def set_conflicts(self, conflicts: list) -> None:
            self.stand_in_conflicts = list(conflicts)
            self.conflicts_changed.emit()

    _MADE[base] = StandInController
    return StandInController
