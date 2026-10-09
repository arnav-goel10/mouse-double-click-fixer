"""JSON-backed settings, stored in the platform's own config location."""

from __future__ import annotations

import json
import math
import os
import platform
import shutil
from pathlib import Path
from time import sleep
from typing import Any

from .contracts import DEFAULT_THRESHOLD_MS, WHEEL_DEFAULT_MS, Button
from .core import MAX_THRESHOLD_MS, MIN_THRESHOLD_MS

#: 3 (1.0): a window per button, side buttons, the wheel fix, app exclusions
#: and ignored devices. 2: one window for every button.
SCHEMA_VERSION = 3

#: The window every install had before 1.0 unless the user moved it or
#: calibrated. An uncalibrated install still at it takes the new default.
OLD_DEFAULT_THRESHOLD_MS = 60

#: The range the wheel-reversal window may take.
WHEEL_MIN_MS = 10
WHEEL_MAX_MS = 150

DEFAULTS: dict[str, Any] = {
    "version": SCHEMA_VERSION,
    # Per button name, in milliseconds.
    "thresholds": {button.value: DEFAULT_THRESHOLD_MS for button in Button},
    "fix_enabled": False,
    # The buttons being filtered; "back" and "forward" are off by default.
    "buttons": [Button.LEFT.value],
    # The buttons whose window came from calibration.
    "calibrated_buttons": [],
    "wheel_fix": False,
    "wheel_window_ms": WHEEL_DEFAULT_MS,
    # [{"key": app key, "name": shown name}]: nothing is filtered while one
    # of these is the frontmost app.
    "excluded_apps": [],
    # [{"key": device key, "name": shown name}]: never filtered.
    "ignored_devices": [],
    # Copies from before 1.0 read these after a downgrade. Always the left
    # button's window and whether it was calibrated; never read by this one,
    # except to migrate a file such a copy saved.
    "threshold_ms": DEFAULT_THRESHOLD_MS,
    "calibrated": False,
    "start_at_login": False,
    "start_minimized": False,
    "filtered_total": 0,
    "window_geometry": "",
    # Install updates without asking. Checking for them is "auto_check",
    # which has no default here: see coerce().
    "auto_update": True,
    "last_update_check": 0.0,
    # The version an update was installing when the app quit for it.
    "pending_update": "",
    # The update last installed without asking, and how many times it was
    # tried; one that keeps failing is left for the user.
    "update_attempt_version": "",
    "update_attempt_count": 0,
    # Windows: the "still running in the notification area" hint was shown.
    "tray_hint_shown": False,
}

#: How often a settings file that can't be opened is tried again at launch.
READ_ATTEMPTS = 3

LEGACY_PATH = Path.home() / ".doubleclick-fixer.json"


def config_dir() -> Path:
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / "DoubleClickFixer"
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "DoubleClickFixer"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "doubleclick-fixer"


def settings_path() -> Path:
    return config_dir() / "settings.json"


def backup_path() -> Path:
    """The previous good settings, used when settings.json is damaged."""
    return config_dir() / "settings.json.bak"


# -- values ----------------------------------------------------------------------

def milliseconds(value: Any, default: int, low: int = MIN_THRESHOLD_MS, high: int = MAX_THRESHOLD_MS) -> int:
    """`value` as whole milliseconds within [low, high], or `default` when it
    is not a finite number (true and false are not numbers here)."""
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return max(low, min(high, int(round(number))))


def _button_names(values: Any) -> list[str]:
    """Known button names from `values`, once each, in the buttons' order."""
    names = set(values) if isinstance(values, (list, tuple)) else set()
    return [button.value for button in Button if button.value in names]


def _thresholds(values: Any) -> dict[str, int]:
    stored = values if isinstance(values, dict) else {}
    return {button.value: milliseconds(stored.get(button.value), DEFAULT_THRESHOLD_MS) for button in Button}


def _named_keys(values: Any) -> list[dict[str, str]]:
    """[{"key", "name"}] entries with a usable key, once per key, in order."""
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in values if isinstance(values, list) else []:
        if isinstance(item, dict):
            key, name = item.get("key"), item.get("name")
        else:
            key, name = item, item
        if not isinstance(key, str) or not key.strip() or key in seen:
            continue
        if not isinstance(name, str) or not name.strip():
            name = key
        seen.add(key)
        entries.append({"key": key, "name": name})
    return entries


def _version(values: dict[str, Any]) -> int:
    version = values.get("version", 1)
    if isinstance(version, bool) or not isinstance(version, (int, float)) or not math.isfinite(version):
        return 1
    return int(version)


def migrate(values: dict[str, Any]) -> dict[str, Any]:
    """Settings written before 1.0 (schema 2 or earlier), in 1.0's shape.
    Keeps every value it finds, and changes nothing in 1.0 settings, so it
    can run on every load.

    * A file 0.x wrote: every button takes its one window (calibrated or set
      by hand, it covered them all), and a calibrated install's left button
      counts as calibrated. Only an uncalibrated window still at the old
      default of 60 ms becomes the new default.
    * A 1.0 file that an older copy saved again after a downgrade (it keeps
      the keys it doesn't know): that copy's window and calibration were the
      left button's, the rest stay as 1.0 left them.
    """
    if _version(values) >= SCHEMA_VERSION:
        return values
    migrated = dict(values)
    window = milliseconds(values.get("threshold_ms"), -1)
    calibrated = bool(values.get("calibrated"))
    if isinstance(values.get("thresholds"), dict):
        thresholds = dict(values["thresholds"])
        if window > 0:
            thresholds[Button.LEFT.value] = window
        names = _button_names(values.get("calibrated_buttons"))
        if calibrated and Button.LEFT.value not in names:
            names.insert(0, Button.LEFT.value)
    else:
        if window < 0 or (window == OLD_DEFAULT_THRESHOLD_MS and not calibrated):
            window = DEFAULT_THRESHOLD_MS
        thresholds = {button.value: window for button in Button}
        names = [Button.LEFT.value] if calibrated else []
    migrated["thresholds"] = thresholds
    migrated["calibrated_buttons"] = names
    migrated["version"] = SCHEMA_VERSION
    return migrated


def coerce(values: dict[str, Any]) -> dict[str, Any]:
    migrated = migrate(values)
    merged = {**DEFAULTS, **migrated}
    merged["thresholds"] = _thresholds(merged.get("thresholds"))
    merged["buttons"] = _button_names(merged.get("buttons")) or [Button.LEFT.value]
    merged["calibrated_buttons"] = _button_names(merged.get("calibrated_buttons"))
    merged["wheel_window_ms"] = milliseconds(merged.get("wheel_window_ms"), WHEEL_DEFAULT_MS, WHEEL_MIN_MS, WHEEL_MAX_MS)
    merged["excluded_apps"] = _named_keys(merged.get("excluded_apps"))
    merged["ignored_devices"] = _named_keys(merged.get("ignored_devices"))
    # What an older copy reads after a downgrade: the left button's.
    merged["threshold_ms"] = merged["thresholds"][Button.LEFT.value]
    merged["calibrated"] = Button.LEFT.value in merged["calibrated_buttons"]
    for flag in ("fix_enabled", "start_at_login", "start_minimized", "auto_update", "tray_hint_shown", "wheel_fix"):
        merged[flag] = bool(merged.get(flag))
    if "auto_check" in values:
        merged["auto_check"] = bool(values["auto_check"])
    else:
        # Before 1.0 one switch covered checking for updates and installing
        # them, and turning it off stopped the checks too. Settings saved
        # then take that answer for both, so an opt-out stays an opt-out.
        merged["auto_check"] = merged["auto_update"]
    for count in ("filtered_total", "update_attempt_count"):
        try:
            merged[count] = max(0, int(merged.get(count, 0)))
        except (TypeError, ValueError, OverflowError):
            merged[count] = 0
    try:
        merged["last_update_check"] = float(merged.get("last_update_check") or 0.0)
    except (TypeError, ValueError):
        merged["last_update_check"] = 0.0
    for text in ("window_geometry", "pending_update", "update_attempt_version"):
        if not isinstance(merged.get(text), str):
            merged[text] = ""
    merged["version"] = SCHEMA_VERSION
    return merged


# -- files -----------------------------------------------------------------------

def _read(path: Path) -> dict[str, Any]:
    """The values stored at `path`, or {} when there are none: the file is
    missing, or it is not valid settings (cut short by a crash, say).

    Any other failure to read it raises OSError. A file that another program
    has open for a moment is not the same as no settings, and treating it as
    such would write the defaults over the user's own.
    """
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return {}
    try:
        content = json.loads(data.decode("utf-8"))
    except ValueError:  # includes a decoding error
        return {}
    return content if isinstance(content, dict) else {}


def read_json(path: Path, backup: Path) -> dict[str, Any]:
    """What the JSON file at `path` holds. When it is there but can't be
    used, because it is damaged or stays locked through a few tries, the copy
    kept beside it (`backup`) stands in. A missing file means nothing stored:
    deleting it is how users start afresh, so the copy must not bring the old
    values back."""
    return read_json_checked(path, backup)[0]


def read_json_checked(path: Path, backup: Path) -> tuple[dict[str, Any], bool]:
    """read_json, and whether its answer stands for what is stored. That is
    False only when the file stayed unreadable through every try (a backup
    tool or virus scanner holding it) and its copy couldn't stand in: the
    empty answer then means "couldn't read", not "nothing stored", and
    writing over the file would replace what it holds."""
    locked = False
    for _attempt in range(READ_ATTEMPTS):
        try:
            values = _read(path)
        except OSError:
            # A backup tool or virus scanner has it open; it lets go quickly.
            locked = True
            sleep(0.1)
            continue
        if values or not path.exists():
            return values, True
        locked = False  # damaged, not locked: the copy is all there is
        break
    try:
        values = _read(backup)
    except OSError:
        return {}, False
    return values, bool(values) or not locked


def write_json(destination: Path, backup: Path, content: Any) -> None:
    """Write `content` to `destination` safely.

    The new file is written beside the old one and flushed to disk before it
    takes its place, so a crash leaves either the old file or the new, never
    a half-written one. The old one is kept as `backup` for read_json to fall
    back on should the main file ever be damaged anyway.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    with open(temporary, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(content, indent=2))
        handle.flush()
        os.fsync(handle.fileno())
    try:
        if not destination.exists():
            # The user deleted the file to start afresh; an older copy must
            # not come back later in its place.
            backup.unlink(missing_ok=True)
        elif _read(destination):
            # Only a good file becomes the spare: a damaged one would replace
            # the copy that is about to be needed.
            shutil.copyfile(destination, backup)
    except OSError:
        pass  # the copy is a spare; the write itself matters
    temporary.replace(destination)


def _stored() -> dict[str, Any]:
    return read_json(settings_path(), backup_path())


def load_raw() -> dict[str, Any]:
    """Stored values without migration."""
    return _stored()


def load() -> dict[str, Any]:
    values = _stored()
    if values:
        return coerce(values)

    legacy = _read_quietly(LEGACY_PATH)
    if legacy:
        # Version 1 measured press-to-press time; this version measures the gap
        # between release and press, so its threshold does not carry over.
        return save(
            {
                "start_at_login": bool(legacy.get("start_at_login")),
                "fix_enabled": False,
                "calibrated": False,
            },
            current={},
        )
    return coerce({})


def _read_quietly(path: Path) -> dict[str, Any]:
    try:
        return _read(path)
    except OSError:
        return {}


def _legacy_writes(values: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    """A write of the pre-1.0 keys alone means the left button: they are
    only its mirror now, and would otherwise be overwritten unseen."""
    values = dict(values)
    if "threshold_ms" in values and "thresholds" not in values:
        thresholds = dict(base.get("thresholds") or {})
        thresholds[Button.LEFT.value] = values["threshold_ms"]
        values["thresholds"] = thresholds
    if "calibrated" in values and "calibrated_buttons" not in values:
        names = [name for name in base.get("calibrated_buttons") or [] if name != Button.LEFT.value]
        values["calibrated_buttons"] = ([Button.LEFT.value] if values["calibrated"] else []) + names
    return values


def merge(values: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """The settings `current` becomes with `values` written into it, the
    same whether or not the file can be written."""
    if _version(current) >= SCHEMA_VERSION:
        values = _legacy_writes(values, current)
    return coerce({**current, **values})


def save(values: dict[str, Any], current: dict[str, Any] | None = None) -> dict[str, Any]:
    """Merge `values` into the settings and write them safely (write_json).

    `current` is what the app already holds in memory, normally the whole
    picture, so nothing has to be read back first. Without it the file is
    read, and a file that can't be read raises OSError rather than being
    taken as empty, which would reset everything else to defaults.
    """
    base = _read(settings_path()) if current is None else current
    merged = merge(values, base)
    write_json(settings_path(), backup_path(), merged)
    return merged


def buttons_from(values: dict[str, Any]) -> list[Button]:
    return [Button(name) for name in values.get("buttons", [Button.LEFT.value])]


def thresholds_from(values: dict[str, Any]) -> dict[Button, int]:
    stored = values.get("thresholds") or {}
    return {button: int(stored.get(button.value, DEFAULT_THRESHOLD_MS)) for button in Button}
