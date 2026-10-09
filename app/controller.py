"""Application state: settings, the global filter, and the signals the UI follows."""

from __future__ import annotations

import logging
import sys
import threading
from typing import Optional

from PySide6.QtCore import QObject, Signal

from . import permissions
from . import settings as settings_store
from . import startup
from .core import Button, ClickEvent, clamp_threshold
from .platform import GlobalClickFilter, HookError, is_supported

log = logging.getLogger(__name__)

#: The settings the updater keeps, through store_update_state().
UPDATE_STATE_KEYS = frozenset({"auto_check", "update_attempt_version", "update_attempt_count"})


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
    #: macOS: the filter found its permission withdrawn, let every click
    #: through and stopped. Emitted from the hook's thread.
    #: A running filter found its access withdrawn and stopped. Carries that
    #: filter, so a late signal from one since replaced can be ignored.
    permission_lost = Signal(object)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.settings = settings_store.load()
        self.login_item_state = startup.ON if self.settings["start_at_login"] else startup.OFF
        if startup.is_supported():
            self.refresh_login_item()
            if (
                self.login_item_state == startup.ON
                and getattr(sys, "frozen", False)
                and not startup.running_from_temporary_location()
            ):
                # Rewrite the entry so it points at this copy of the app and
                # carries the current format (an older one may lack the app's
                # name and icon in Login Items). Never from a disk image or a
                # translocated copy: that path is gone after an eject or a
                # reboot, and the installed copy would stop opening at login.
                try:
                    startup.set_enabled(True)
                except (OSError, RuntimeError):
                    pass
        self._filter: Optional[GlobalClickFilter] = None
        # A filter whose stop() gave up with its hook thread still alive.
        # Never dropped: no second hook may start beside it, and the next
        # stop tries it again.
        self._unstopped: Optional[GlobalClickFilter] = None
        self._suspended = False
        # Set at quit. Nothing may start a hook after that: the window still
        # hears hide events, and AppKit notifications, as the app goes down.
        self._shut_down = False
        # Set while the user is in another login session (fast user
        # switching). A tap running in a session nobody is using can stall
        # the one in front, so nothing starts one until this session is back.
        self._session_inactive = False
        # Why the filter isn't running although the user has it on: a short
        # line for the menus, and the full message for the window.
        self.failure = ""
        self.failure_detail = ""
        # Turned on, but macOS hasn't granted Accessibility yet (set by the window).
        self.waiting_for_permission = False
        # Bounces counted on the hook thread since the last flush. Kept apart
        # from `settings`, which every settings write replaces, so an
        # unrelated write can never lose them.
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
    @property
    def wanted(self) -> bool:
        """Whether the user has the filter on: running, waiting for
        permission, paused while calibration measures, or saved as on but
        failing to start. The menus show and toggle this, so choosing the
        item in any of those states turns the filter off instead of asking
        for on again."""
        return (
            self.active
            or self.waiting_for_permission
            or (bool(self.settings["fix_enabled"]) and (self._suspended or bool(self.failure)))
        )

    def set_active(self, active: bool) -> bool:
        """Turn the system-wide filter on or off. Returns the resulting state.

        This is the user's own choice, from the window or a menu, and only it
        changes the saved one. Off is saved, so the filter stays off at the
        next login. A failure to turn on saves nothing: a tap macOS refuses
        for a moment at login must not cost the user their "on" for every
        login after it.
        """
        if not active:
            self._turn_off()
            return False
        if self.active:
            return True
        if self._shut_down or self._session_inactive:
            return False
        # Turning the filter on (from the menu bar, say) ends a pause.
        self._suspended = False
        self._stop_filter()  # release a filter whose hook thread died
        if self._unstopped is not None:
            message = "The filter from before is still stopping. Try again in a moment."
            log.warning("The filter can't start: %s", message)
            self.failure, self.failure_detail = "Couldn’t start the filter", message
            self.filter_state_changed.emit(False, message)
            return False
        try:
            reporter: list = []
            self._filter = GlobalClickFilter(
                self.threshold_ms,
                self.buttons,
                on_event=self._on_global_event,
                on_error=self.hook_failed.emit,
                # macOS re-arms a tap it disabled only while access is still
                # there; without it the filter lets clicks through and stops.
                permission_ok=permissions.event_tap_allowed if permissions.needs_accessibility() else None,
                on_permission_lost=lambda: self.permission_lost.emit(reporter[0] if reporter else None),
            )
            reporter.append(self._filter)
            self._filter.start()
        except HookError as error:
            self._filter = None
            log.warning("The filter couldn't start: %s", error)
            self.failure, self.failure_detail = "Couldn’t start the filter", str(error)
            self.filter_state_changed.emit(False, str(error))
            return False
        self.failure = self.failure_detail = ""
        log.info("Filter on: %d ms, buttons %s", self.threshold_ms, ", ".join(self.settings["buttons"]))
        self._store(fix_enabled=True)
        self.filter_state_changed.emit(True, "")
        return True

    def _turn_off(self) -> None:
        # A filter whose hook thread has died reads as inactive but still
        # needs releasing. Turning off also ends a wait for permission, a
        # pause, or a failed start, all of which the menus show as "on".
        changed = self._filter is not None or self._suspended or bool(self.failure)
        self._stop_filter()
        self._suspended = False
        self.failure = self.failure_detail = ""
        if self.settings["fix_enabled"]:
            changed = True
            self._store(fix_enabled=False)
        if changed:
            log.info("Filter turned off")
            self.filter_state_changed.emit(False, "")

    def _stop_filter(self) -> None:
        current, self._filter = self._filter, None
        if current is None:
            # One that didn't stop last time gets another try.
            current, self._unstopped = self._unstopped, None
        if current is None:
            return
        current.stop()
        if current.running:
            # Its hook thread didn't end in time. Dropping the handle to a
            # live hook would let a second one start on top of it.
            log.error("The filter's hook thread didn't stop; no new filter starts until it does")
            self._unstopped = current
            return
        # Counted on the hook thread, so only read here, never logged there.
        log.info(
            "Filter stopped (tap resets %s, hook re-arms %s)",
            getattr(current, "tap_resets", 0),
            getattr(current, "hook_rearms", 0),
        )

    def suspend(self) -> None:
        """Pause filtering so the click pad measures the raw mouse.

        Calibration has to see the faulty clicks to learn from them.
        """
        if self.active and not self._suspended:
            self._suspended = True
            self._stop_filter()
            self.filter_state_changed.emit(False, "")

    def enable_after_calibration(self) -> None:
        """Turned on while calibration has filtering paused: remember it, and
        start once calibration ends, so the pad keeps measuring raw clicks."""
        self._store(fix_enabled=True)
        if self.active:
            self._stop_filter()
        self._suspended = True
        self.failure = self.failure_detail = ""
        self.filter_state_changed.emit(False, "")

    def stop_keeping_choice(self) -> None:
        """Stop the tap but keep the user's choice, so the filter comes back by
        itself once whatever stopped it is over, even after a restart: access
        was revoked, the user switched to another login session, or the tap
        is being rebuilt."""
        self._stop_filter()
        self.filter_state_changed.emit(False, "")

    @property
    def session_active(self) -> bool:
        """Whether this login session is the one in front."""
        return not self._session_inactive

    def set_session_active(self, active: bool) -> None:
        """The user switched into (True) or out of (False) this login
        session. Out of it, the filter stops (sending any release it was
        holding) and nothing starts one, whatever asks: a retry, the end of a
        calibration pause. The user's choice is kept, so the filter comes
        back with the session."""
        self._session_inactive = not active
        if not active and self._filter is not None:
            self.stop_keeping_choice()

    def stop_for_permission(self) -> None:
        """Accessibility was revoked: stop the tap, keeping the user's choice."""
        log.warning("Access was withdrawn; filter stopped until it is back")
        self.stop_keeping_choice()

    def stop_after_failure(self, message: str) -> None:
        """The hook stopped on its own. Release it and say so, but keep the
        user's choice: the next login, or the user, starts it again."""
        log.error("The filter stopped: %s", message)
        self.failure, self.failure_detail = "The filter stopped", message
        self.stop_keeping_choice()

    def clear_failure(self) -> None:
        """Forget the last failure: the window found a better explanation
        for it (access is gone) and shows that instead."""
        self.failure = self.failure_detail = ""

    def tap_alive(self) -> bool:
        """False when the filter runs but its tap no longer receives events.
        A filter that can't tell (Windows, for one) counts as alive."""
        current = self._filter
        if current is None or not current.running:
            return True
        check = getattr(current, "tap_alive", None)
        if check is None:
            return True
        try:
            return bool(check())
        except Exception:  # noqa: BLE001 - unsure: leave a working filter alone
            return True

    def set_waiting_for_permission(self, waiting: bool) -> None:
        if waiting != self.waiting_for_permission:
            self.waiting_for_permission = waiting
            self.settings_changed.emit()

    def status_text(self) -> str:
        """One line for the menu bar and tray menus."""
        if self.active:
            return f"On · {self.filtered_total:,} blocked"
        if self._suspended and self.settings["fix_enabled"]:
            return "Paused for calibration"
        if self.waiting_for_permission:
            return f"Waiting for {permissions.pane_name()} permission"
        if self.failure:
            # Kept until the filter starts or the user turns it off, so a
            # failure with the window closed is still explained.
            return self.failure
        return "Off"

    def tooltip_text(self) -> str:
        """The status line as a tooltip for the menu bar or tray icon, so a
        paused or waiting filter never reads as plain "off"."""
        if self.active:
            return f"DoubleClick Fixer: on, {self.threshold_ms} ms"
        status = self.status_text()
        return f"DoubleClick Fixer: {status[0].lower()}{status[1:]}"

    def resume(self) -> None:
        if self._suspended:
            self._suspended = False
            if self.settings["fix_enabled"]:
                self.set_active(True)

    @property
    def suspended(self) -> bool:
        return self._suspended

    def shutdown(self) -> None:
        self._shut_down = True
        self._stop_filter()
        self.flush_stats()

    def diagnostic_state(self) -> dict:
        """The filter's state for a bug report. Counts only, never clicks."""
        current = self._filter
        return {
            "filter running": self.active,
            "old hook still stopping": self._unstopped is not None,
            "user has it on": self.wanted,
            "saved choice": "on" if self.settings["fix_enabled"] else "off",
            "paused for calibration": self._suspended,
            "waiting for permission": self.waiting_for_permission,
            "failure": f"{self.failure}: {self.failure_detail}" if self.failure else "none",
            "tap alive": self.tap_alive(),
            "tap resets": getattr(current, "tap_resets", "-"),
            "hook re-arms": getattr(current, "hook_rearms", "-"),
            "blocked this session": self.session_filtered,
            "login item": self.login_item_state,
        }

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
        if enabled and startup.running_from_temporary_location():
            return (
                "DoubleClick Fixer is running from the disk image or a temporary copy, "
                "which won’t be there at your next login. Move it to Applications, open "
                "it from there, and turn this on again."
            )
        try:
            startup.set_enabled(enabled)
        except (OSError, RuntimeError) as error:
            return str(error)
        self._store(start_at_login=bool(enabled))
        self.refresh_login_item()
        return ""

    def refresh_login_item(self) -> str:
        """Read the login item from the system, which is the source of truth:
        the Windows installer, System Settings and Task Manager all change it
        behind the app's back. Returns startup.ON, OFF or BLOCKED."""
        if not startup.is_supported():
            return self.login_item_state
        try:
            state = startup.status()
        except OSError:
            return self.login_item_state
        self.login_item_state = state
        actual = state == startup.ON
        if actual != self.settings["start_at_login"]:
            self._store(start_at_login=actual)
        return state

    def set_window_geometry(self, encoded: str) -> None:
        if encoded != self.settings.get("window_geometry"):
            self._store(window_geometry=encoded)

    @property
    def tray_hint_shown(self) -> bool:
        return bool(self.settings.get("tray_hint_shown"))

    def note_tray_hint_shown(self) -> None:
        self._store(tray_hint_shown=True)

    def set_auto_update(self, enabled: bool) -> None:
        """Install updates without asking (the updater's auto_install)."""
        self._store(auto_update=bool(enabled))

    def store_update_state(self, **values: object) -> None:
        """Save the updater's own settings (UPDATE_STATE_KEYS) with all the
        others, so the next save of anything else keeps them."""
        unknown = sorted(set(values) - UPDATE_STATE_KEYS)
        if unknown:
            raise ValueError(f"Not an update setting: {', '.join(unknown)}")
        self._store(**values)

    def set_last_update_check(self) -> None:
        import time

        self._store(last_update_check=time.time())

    def set_pending_update(self, version: str) -> None:
        self._store(pending_update=version)

    def take_update_result(self, current_version: str) -> str:
        """After an update relaunch: "updated", "failed", or "" when no
        update was in progress. Clears the record either way."""
        pending = self.settings.get("pending_update", "")
        if not pending:
            return ""
        self._store(pending_update="")
        return "updated" if pending == current_version else "failed"

    def reset_statistics(self) -> None:
        with self._count_lock:
            self._pending_filtered = 0
            self.session_filtered = 0
        self._store(filtered_total=0)
        self.settings_changed.emit()

    def _store(self, **values: object) -> None:
        try:
            # Merged onto what the app holds, never onto a fresh read of the
            # file: a read that fails for a moment would otherwise reset
            # every other setting to its default.
            self.settings = settings_store.save(values, current=self.settings)
        except OSError:
            # The disk is full or the file is locked (a sync tool, antivirus).
            # Keep running on the new values; the next write tries again.
            self.settings = settings_store.coerce({**self.settings, **values})

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
