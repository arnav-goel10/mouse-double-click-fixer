"""Application state: settings, the global filter, and the signals the UI follows."""

from __future__ import annotations

import sys
import threading
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
        # The login item can be changed outside the app (the Windows
        # installer, System Settings), so the system is the source of truth.
        if startup.is_supported():
            try:
                actual = startup.is_enabled()
            except OSError:
                actual = self.settings["start_at_login"]
            if actual != self.settings["start_at_login"]:
                self._store(start_at_login=actual)
            if actual and getattr(sys, "frozen", False):
                # Rewrite the entry so it points at this copy of the app and
                # carries the current format (an older one may lack the app's
                # name and icon in Login Items).
                try:
                    startup.set_enabled(True)
                except (OSError, RuntimeError):
                    pass
        self._filter: Optional[GlobalClickFilter] = None
        self._suspended = False
        # Bounces counted on the hook thread since the last flush. Kept apart
        # from `settings`, which every settings write replaces with what is on
        # disk, so an unrelated write can never lose them.
        self._count_lock = threading.Lock()
        self._pending_filtered = 0
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
        with self._count_lock:
            return int(self.settings["filtered_total"]) + self._pending_filtered

    def supported(self) -> bool:
        return is_supported()

    # -- filter lifecycle --------------------------------------------------
    def set_active(self, active: bool) -> bool:
        """Turn the system-wide filter on or off. Returns the resulting state."""
        if active == self.active and (active or self._filter is None):
            # Nothing to do. A filter whose hook thread has died reads as
            # inactive but still needs releasing, so that case falls through.
            return self.active
        if active:
            # Turning the filter on (from the menu bar, say) ends a pause.
            self._suspended = False
            self._stop_filter()  # release a filter whose hook thread died
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

    def set_window_geometry(self, encoded: str) -> None:
        if encoded != self.settings.get("window_geometry"):
            self._store(window_geometry=encoded)

    def set_auto_update(self, enabled: bool) -> None:
        self._store(auto_update=bool(enabled))

    def set_last_update_check(self) -> None:
        import time

        self._store(last_update_check=time.time())

    def reset_statistics(self) -> None:
        with self._count_lock:
            self._pending_filtered = 0
            self.session_filtered = 0
        self._store(filtered_total=0)
        self.settings_changed.emit()

    def _store(self, **values: object) -> None:
        self.settings = settings_store.save(values)

    def flush_stats(self) -> None:
        """Write the running count to disk. Called from the UI thread only."""
        with self._count_lock:
            pending, self._pending_filtered = self._pending_filtered, 0
        if pending:
            self._store(filtered_total=int(self.settings["filtered_total"]) + pending)

    # -- events ------------------------------------------------------------
    def _on_global_event(self, event: ClickEvent) -> None:
        # Runs on the hook thread. Windows drops a low-level hook that takes
        # too long, so this does nothing but count and emit; the signal hops
        # to the UI thread, which owns all disk writes.
        if event.is_bounce:
            with self._count_lock:
                self.session_filtered += 1
                self._pending_filtered += 1
            # The UI only reacts to bounces, so only those cross threads.
            self.global_event.emit(event)
