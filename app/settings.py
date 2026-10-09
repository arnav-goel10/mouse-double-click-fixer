"""JSON-backed settings, stored in the platform's own config location."""

from __future__ import annotations

import json
import os
import platform
import shutil
from pathlib import Path
from time import sleep
from typing import Any

from .core import DEFAULT_THRESHOLD_MS, Button, clamp_threshold

SCHEMA_VERSION = 2

DEFAULTS: dict[str, Any] = {
    "version": SCHEMA_VERSION,
    "threshold_ms": DEFAULT_THRESHOLD_MS,
    "fix_enabled": False,
    "buttons": [Button.LEFT.value],
    "start_at_login": False,
    "start_minimized": False,
    "calibrated": False,
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


def coerce(values: dict[str, Any]) -> dict[str, Any]:
    merged = {**DEFAULTS, **values}
    merged["threshold_ms"] = clamp_threshold(merged.get("threshold_ms", DEFAULT_THRESHOLD_MS))
    buttons = merged.get("buttons") or [Button.LEFT.value]
    valid = {button.value for button in Button}
    merged["buttons"] = [name for name in buttons if name in valid] or [Button.LEFT.value]
    for flag in ("fix_enabled", "start_at_login", "start_minimized", "calibrated", "auto_update", "tray_hint_shown"):
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


def _stored() -> dict[str, Any]:
    """What settings.json holds. When the file is there but can't be used,
    because it is damaged or stays locked through a few tries, the copy kept
    beside it stands in. A missing file means defaults: deleting it is how
    users start afresh, so the copy must not bring the old values back."""
    path = settings_path()
    for _attempt in range(READ_ATTEMPTS):
        try:
            values = _read(path)
        except OSError:
            # A backup tool or virus scanner has it open; it lets go quickly.
            sleep(0.1)
            continue
        if values or not path.exists():
            return values
        break
    try:
        return _read(backup_path())
    except OSError:
        return {}


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


def save(values: dict[str, Any], current: dict[str, Any] | None = None) -> dict[str, Any]:
    """Merge `values` into the settings and write them safely.

    `current` is what the app already holds in memory, normally the whole
    picture, so nothing has to be read back first. Without it the file is
    read, and a file that can't be read raises OSError rather than being
    taken as empty, which would reset everything else to defaults.

    The new file is written beside the old one and flushed to disk before it
    takes its place, so a crash leaves either the old settings or the new,
    never a half-written file. The old one is kept as settings.json.bak for
    `load` to fall back on should the main file ever be damaged anyway.
    """
    base = _read(settings_path()) if current is None else current
    merged = coerce({**base, **values})
    destination = settings_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".json.tmp")
    with open(temporary, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(merged, indent=2))
        handle.flush()
        os.fsync(handle.fileno())
    backup = backup_path()
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
    return merged


def buttons_from(values: dict[str, Any]) -> list[Button]:
    return [Button(name) for name in values.get("buttons", [Button.LEFT.value])]
