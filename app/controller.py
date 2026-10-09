"""Application state: settings, the global filter, and the signals the UI follows."""

from __future__ import annotations

import dataclasses
import logging
import sys
import threading
from typing import Any, Optional

from PySide6.QtCore import QObject, Signal

from . import DISPLAY_NAME, permissions
from . import settings as settings_store
from . import startup
from .contracts import (
    SIDE_BUTTONS,
    TOUCH_KINDS,
    Button,
    DeviceInfo,
    FilterConfig,
    GlobalClickFilter,
    as_button,
    button_name,
)
from .core import ClickEvent, clamp_threshold
from .platform import HookError, is_supported
from .wear import WearHistory

log = logging.getLogger(__name__)

#: The settings the updater keeps, through store_update_state().
UPDATE_STATE_KEYS = frozenset({"auto_check", "update_attempt_version", "update_attempt_count"})


def config_from_settings(values: dict[str, Any]) -> FilterConfig:
    """The filter's configuration from the saved settings. The one place a
    FilterConfig is built (see app/contracts.py)."""
    return FilterConfig(
        thresholds=settings_store.thresholds_from(values),
        buttons=frozenset(settings_store.buttons_from(values)),
        wheel_fix=bool(values["wheel_fix"]),
        wheel_window_ms=int(values["wheel_window_ms"]),
        excluded_apps=frozenset(entry["key"] for entry in values["excluded_apps"]),
        ignored_devices=frozenset(entry["key"] for entry in values["ignored_devices"]),
    )


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
    #: The filter saw a device for the first time this run. Emitted from the
    #: hook's thread; connect with a queued connection.
    device_seen = Signal()

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
        # Daily wear counts, from the hook's thread (see _on_global_event).
        self.wear = WearHistory()
        try:
            self.wear.load()
        except OSError as error:
            log.warning("Couldn't read the wear history: %s", error)
        # What the hook thread reads for wear counting: the filtered buttons
        # and their windows. Replaced whole, never changed in place.
        self._watched: dict[Button, int] = {}
        # Devices seen this run, by key; the filter's own list goes when it
        # stops, and the Devices pane keeps showing them.
        self._device_lock = threading.Lock()
        self._devices: dict[str, DeviceInfo] = {}
        # Keys of devices whose clicks the filter passes untouched (touch
        # kinds and ignored mice), read by the hook thread: their presses
        # are not the switch's, so they don't count towards its wear.
        self._passthrough: frozenset[str] = frozenset()
        self._sync_watched()

    # -- state -------------------------------------------------------------
    @property
    def threshold_ms(self) -> int:
        """The left button's window (see threshold_for)."""
        return self.threshold_for(Button.LEFT)

    def threshold_for(self, button: Any) -> int:
        return int(self.settings["thresholds"][as_button(button).value])

    @property
    def thresholds(self) -> dict[Button, int]:
        return settings_store.thresholds_from(self.settings)

    @property
    def buttons(self) -> list[Button]:
        return settings_store.buttons_from(self.settings)

    def filter_config(self) -> FilterConfig:
        return config_from_settings(self.settings)

    @property
    def active(self) -> bool:
        return self._filter is not None and self._filter.running

    @property
    def calibrated(self) -> bool:
        """Whether the left button's window came from calibration."""
        return self.is_calibrated(Button.LEFT)

    def is_calibrated(self, button: Any) -> bool:
        return as_button(button).value in self.settings["calibrated_buttons"]

    @property
    def wheel_fix(self) -> bool:
        return bool(self.settings["wheel_fix"])

    @property
    def wheel_window_ms(self) -> int:
        return int(self.settings["wheel_window_ms"])

    @property
    def excluded_apps(self) -> list[dict[str, str]]:
        return [dict(entry) for entry in self.settings["excluded_apps"]]

    @property
    def ignored_devices(self) -> list[dict[str, str]]:
        return [dict(entry) for entry in self.settings["ignored_devices"]]

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
                self.filter_config(),
                on_event=self._on_global_event,
                on_error=self.hook_failed.emit,
                # macOS re-arms a tap it disabled only while access is still
                # there; without it the filter lets clicks through and stops.
                permission_ok=permissions.event_tap_allowed if permissions.needs_accessibility() else None,
                on_permission_lost=lambda: self.permission_lost.emit(reporter[0] if reporter else None),
                on_device=self._on_device,
                on_wheel=self._on_wheel,
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
        log.info("Filter on: %s%s", self._windows_text(), ", wheel fix" if self.wheel_fix else "")
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
        self._keep_devices(current)
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
            windows = {self.threshold_for(button) for button in self.buttons}
            if len(windows) == 1:
                return f"{DISPLAY_NAME}: on, {windows.pop()} ms"
            return f"{DISPLAY_NAME}: on"
        status = self.status_text()
        return f"{DISPLAY_NAME}: {status[0].lower()}{status[1:]}"

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
        self.wear.save()

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
            "windows": self._windows_text(every=True),
            "side buttons": ", ".join(
                f"{button_name(button).lower()} {'on' if button in self.buttons else 'off'}"
                for button in Button
                if button in SIDE_BUTTONS
            ),
            "wheel fix": f"on, {self.wheel_window_ms} ms" if self.wheel_fix else "off",
            "excluded apps": len(self.settings["excluded_apps"]),
            "ignored devices": ", ".join(entry["name"] for entry in self.settings["ignored_devices"]) or "none",
            "seen devices": ", ".join(f"{device.name} ({device.kind})" for device in self.seen_devices()) or "none",
        }

    def diagnostic_settings(self) -> dict:
        """The settings for a bug report: the excluded apps only as a count
        and the ignored devices by name (their keys carry serial numbers)."""
        shown = dict(self.settings)
        shown["excluded_apps"] = len(self.settings["excluded_apps"])
        shown["ignored_devices"] = [entry["name"] for entry in self.settings["ignored_devices"]]
        return shown

    def _windows_text(self, every: bool = False) -> str:
        """"left 46 ms, right 30 ms": the filtered buttons' windows, or
        every button's with `every`."""
        chosen = list(Button) if every else self.buttons
        return ", ".join(f"{button.value} {self.threshold_for(button)} ms" for button in chosen)

    # -- settings ----------------------------------------------------------
    def _apply(self, **values: object) -> None:
        """Save filter settings, hand the new configuration to a running
        filter (it takes effect at once), and tell the UI."""
        self._store(**values)
        self._sync_watched()
        if self._filter is not None:
            self._filter.update(self.filter_config())
        self.settings_changed.emit()

    def set_threshold(self, button: Any, value: int) -> None:
        """Set one button's window."""
        button = as_button(button)
        threshold = clamp_threshold(value)
        if threshold == self.threshold_for(button):
            return
        self._apply(thresholds={**self.settings["thresholds"], button.value: threshold})

    def set_buttons(self, buttons: list[Button]) -> None:
        """Choose the buttons to filter, side buttons included. One always
        stays: with none chosen, the left button is."""
        # A Qt signal delivers these as plain strings, since Button is a str
        # enum; accept either form.
        chosen = {as_button(button) for button in buttons}
        names = [button.value for button in Button if button in chosen] or [Button.LEFT.value]
        if names == self.settings["buttons"]:
            return
        self._apply(buttons=names)

    def set_button_filtered(self, button: Any, filtered: bool) -> None:
        button = as_button(button)
        current = set(self.buttons)
        self.set_buttons(sorted(current | {button} if filtered else current - {button}, key=list(Button).index))

    def set_side_buttons(self, filtered: bool) -> None:
        """Filter the back and forward buttons, or stop."""
        current = set(self.buttons)
        self.set_buttons(sorted(current | SIDE_BUTTONS if filtered else current - SIDE_BUTTONS, key=list(Button).index))

    def set_calibrated(self, button: Any, value: bool = True) -> None:
        name = as_button(button).value
        names = [other for other in self.settings["calibrated_buttons"] if other != name]
        if value:
            names.append(name)
        if sorted(names) != sorted(self.settings["calibrated_buttons"]):
            self._store(calibrated_buttons=names)
            self.settings_changed.emit()

    def set_wheel_fix(self, enabled: bool, window_ms: Optional[int] = None) -> None:
        values: dict[str, object] = {"wheel_fix": bool(enabled)}
        if window_ms is not None:
            values["wheel_window_ms"] = settings_store.milliseconds(
                window_ms, self.wheel_window_ms, settings_store.WHEEL_MIN_MS, settings_store.WHEEL_MAX_MS
            )
        if all(self.settings.get(key) == value for key, value in values.items()):
            return
        self._apply(**values)

    def add_excluded_app(self, key: str, name: str = "") -> None:
        """Let everything through while this app is in front."""
        if not key or any(entry["key"] == key for entry in self.settings["excluded_apps"]):
            return
        self._apply(excluded_apps=[*self.settings["excluded_apps"], {"key": key, "name": name or key}])

    def remove_excluded_app(self, key: str) -> None:
        kept = [entry for entry in self.settings["excluded_apps"] if entry["key"] != key]
        if len(kept) != len(self.settings["excluded_apps"]):
            self._apply(excluded_apps=kept)

    def is_device_ignored(self, key: str) -> bool:
        return any(entry["key"] == key for entry in self.settings["ignored_devices"])

    def set_device_ignored(self, key: str, name: str, ignored: bool) -> None:
        """Stop filtering a device (or start again). Touch devices are never
        filtered whatever this says."""
        if not key or ignored == self.is_device_ignored(key):
            return
        kept = [entry for entry in self.settings["ignored_devices"] if entry["key"] != key]
        if ignored:
            kept.append({"key": key, "name": name or key})
        self._apply(ignored_devices=kept)

    def seen_devices(self) -> list[DeviceInfo]:
        """The devices the filter has seen this run, newest first, each
        marked filtered or not by the settings as they are now."""
        current = self._filter or self._unstopped
        found: dict[str, DeviceInfo] = {}
        with self._device_lock:
            found.update(self._devices)
        if current is not None:
            try:
                for device in current.seen_devices():
                    found[device.key] = device
            except Exception:  # noqa: BLE001 - a list for the UI; never break it
                log.warning("Couldn't read the filter's devices", exc_info=True)
        ignored = {entry["key"] for entry in self.settings["ignored_devices"]}
        devices = [
            dataclasses.replace(device, filtered=device.kind not in TOUCH_KINDS and device.key not in ignored)
            for device in found.values()
        ]
        return sorted(devices, key=lambda device: device.last_seen, reverse=True)

    def _keep_devices(self, current: object) -> None:
        try:
            devices = list(current.seen_devices())  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - a stand-in or a dying filter
            return
        with self._device_lock:
            for device in devices:
                self._devices[device.key] = device

    def _sync_watched(self) -> None:
        self._watched = {button: self.threshold_for(button) for button in self.buttons}
        with self._device_lock:
            self._passthrough = frozenset(entry["key"] for entry in self.settings["ignored_devices"]) | frozenset(
                key for key, device in self._devices.items() if device.kind in TOUCH_KINDS
            )

    def set_start_at_login(self, enabled: bool) -> str:
        """Returns an error message, or an empty string on success."""
        if enabled and startup.running_from_temporary_location():
            return (
                f"{DISPLAY_NAME} is running from the disk image or a temporary copy, "
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
        """Start the blocked count from zero. The wear history is kept: it is
        what says whether the switch is getting worse."""
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
            self.settings = settings_store.merge(values, self.settings)

    def flush_stats(self) -> None:
        """Write the running count to disk, and the wear history when it is
        due (every few minutes). Called from the UI thread only."""
        with self._count_lock:
            pending, self._pending_filtered = self._pending_filtered, 0
        if pending:
            self._store(filtered_total=int(self.settings["filtered_total"]) + pending)
        self.wear.save_if_due()

    # -- events ------------------------------------------------------------
    def _on_global_event(self, event: ClickEvent) -> None:
        # Runs on the hook thread. Windows drops a low-level hook that takes
        # too long, so this does nothing but count and emit; the signal hops
        # to the UI thread, which owns all disk writes.
        if event.pressed:
            window = self._watched.get(as_button(event.button))
            # Only the buttons being filtered, from devices being filtered.
            if window is not None and getattr(event, "device", None) not in self._passthrough:
                self.wear.note_event(event, window)
        if event.is_bounce:
            with self._count_lock:
                self.session_filtered += 1
                self._pending_filtered += 1
            # The UI only reacts to bounces, so only those cross threads.
            self.global_event.emit(event)

    def _on_device(self, device: DeviceInfo) -> None:
        # The hook thread: remember it, and let the UI know.
        with self._device_lock:
            self._devices[device.key] = device
            if device.kind in TOUCH_KINDS:
                self._passthrough = self._passthrough | {device.key}
        self.device_seen.emit()

    def _on_wheel(self, axis: int, dropped: bool) -> None:
        # The hook thread.
        self.wear.note_wheel(axis, dropped)
