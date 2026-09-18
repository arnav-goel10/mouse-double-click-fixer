"""Application state: settings, the global filter, and the signals the UI follows."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, Signal

from . import settings as settings_store
from . import startup
from .core import Button, ClickEvent, clamp_threshold
from .platform import GlobalClickFilter, HookError, is_supported


class AppController(QObject):
    """Owns the filter and the saved settings; everything else observes it."""

    #: The filter was turned on or off (the string carries any failure reason).
    filter_state_changed = Signal(bool, str)
    #: A press or release seen by the system-wide filter.
    global_event = Signal(object)
    #: Threshold or button selection changed.
    settings_changed = Signal()
    #: The hook stopped on its own.
    hook_failed = Signal(str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.settings = settings_store.load()
        self._filter: Optional[GlobalClickFilter] = None
        self._suspended = False
        self._unsaved_filtered = False
        self.session_filtered = 0

    # -- state -------------------------------------------------------------
    @property
    def threshold_ms(self) -> int:
        return int(self.settings["threshold_ms"])

    @property
    def buttons(self) -> list[Button]:
        return settings_store.buttons_from(self.settings)

    @property
    def active(self) -> bool:
        return self._filter is not None and self._filter.running

    @property
    def calibrated(self) -> bool:
        return bool(self.settings["calibrated"])

    @property
    def filtered_total(self) -> int:
        return int(self.settings["filtered_total"])

    def supported(self) -> bool:
        return is_supported()

    # -- filter lifecycle --------------------------------------------------
    def set_active(self, active: bool) -> bool:
        """Turn the system-wide filter on or off. Returns the resulting state."""
        if active == self.active:
            return self.active
        if active:
            try:
                self._filter = GlobalClickFilter(
                    self.threshold_ms,
                    self.buttons,
                    on_event=self._on_global_event,
                    on_error=self.hook_failed.emit,
                )
                self._filter.start()
            except HookError as error:
                self._filter = None
                self._store(fix_enabled=False)
                self.filter_state_changed.emit(False, str(error))
                return False
            self._store(fix_enabled=True)
            self.filter_state_changed.emit(True, "")
            return True

        self._stop_filter()
        self._store(fix_enabled=False)
        self.filter_state_changed.emit(False, "")
        return False

    def _stop_filter(self) -> None:
        if self._filter is not None:
            self._filter.stop()
            self._filter = None

    def suspend(self) -> None:
        """Pause filtering so the click pad measures the raw mouse.

        Calibration has to see the faulty clicks to learn from them.
        """
        if self.active and not self._suspended:
            self._suspended = True
            self._stop_filter()
            self.filter_state_changed.emit(False, "")

    def resume(self) -> None:
        if self._suspended:
            self._suspended = False
            if self.settings["fix_enabled"]:
                self.set_active(True)

    @property
    def suspended(self) -> bool:
        return self._suspended

    def shutdown(self) -> None:
        self._stop_filter()
        self.flush_stats()

    # -- settings ----------------------------------------------------------
    def set_threshold(self, value: int) -> None:
        threshold = clamp_threshold(value)
        if threshold == self.threshold_ms:
            return
        self._store(threshold_ms=threshold)
        if self._filter is not None:
            self._filter.update(threshold_ms=threshold)
        self.settings_changed.emit()

    def set_buttons(self, buttons: list[Button]) -> None:
        # A Qt signal delivers these as plain strings, since Button is a str
        # enum; accept either form.
        names = [Button(button).value for button in buttons] or [Button.LEFT.value]
        if names == self.settings["buttons"]:
            return
        self._store(buttons=names)
        if self._filter is not None:
            self._filter.update(buttons=self.buttons)
        self.settings_changed.emit()

    def set_calibrated(self, value: bool) -> None:
        self._store(calibrated=bool(value))

    def set_start_at_login(self, enabled: bool) -> str:
        """Returns an error message, or an empty string on success."""
        try:
            startup.set_enabled(enabled)
        except (OSError, RuntimeError) as error:
            return str(error)
        self._store(start_at_login=bool(enabled))
        return ""

    def set_start_minimized(self, enabled: bool) -> None:
        self._store(start_minimized=bool(enabled))

    def set_window_geometry(self, encoded: str) -> None:
        if encoded != self.settings.get("window_geometry"):
            self._store(window_geometry=encoded)

    def reset_statistics(self) -> None:
        self.session_filtered = 0
        self._store(filtered_total=0)
        self.settings_changed.emit()

    def _store(self, **values: object) -> None:
        self.settings = settings_store.save(values)

    def flush_stats(self) -> None:
        """Write the running count to disk. Called from the UI thread only."""
        if self._unsaved_filtered:
            self._unsaved_filtered = False
            self._store(filtered_total=self.filtered_total)

    # -- events ------------------------------------------------------------
    def _on_global_event(self, event: ClickEvent) -> None:
        # Runs on the hook thread. Windows drops a low-level hook that takes
        # too long, so this does nothing but count and emit; the signal hops
        # to the UI thread, which owns all disk writes.
        if event.is_bounce:
            self.session_filtered += 1
            self.settings["filtered_total"] = self.filtered_total + 1
            self._unsaved_filtered = True
        self.global_event.emit(event)
