"""JSON-backed settings, stored in the platform's own config location."""

from __future__ import annotations

import json
import os
import platform
from pathlib import Path
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
    "auto_update": True,
    "last_update_check": 0.0,
}

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


def _coerce(values: dict[str, Any]) -> dict[str, Any]:
    merged = {**DEFAULTS, **values}
    merged["threshold_ms"] = clamp_threshold(merged.get("threshold_ms", DEFAULT_THRESHOLD_MS))
    buttons = merged.get("buttons") or [Button.LEFT.value]
    valid = {button.value for button in Button}
    merged["buttons"] = [name for name in buttons if name in valid] or [Button.LEFT.value]
    for flag in ("fix_enabled", "start_at_login", "start_minimized", "calibrated", "auto_update"):
        merged[flag] = bool(merged.get(flag))
    try:
        merged["filtered_total"] = max(0, int(merged.get("filtered_total", 0)))
    except (TypeError, ValueError):
        merged["filtered_total"] = 0
    try:
        merged["last_update_check"] = float(merged.get("last_update_check") or 0.0)
    except (TypeError, ValueError):
        merged["last_update_check"] = 0.0
    if not isinstance(merged.get("window_geometry"), str):
        merged["window_geometry"] = ""
    merged["version"] = SCHEMA_VERSION
    return merged


def _read(path: Path) -> dict[str, Any]:
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
        return content if isinstance(content, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def load_raw() -> dict[str, Any]:
    """Stored values without migration; used by `save` to avoid recursion."""
    return _read(settings_path())


def load() -> dict[str, Any]:
    values = load_raw()
    if values:
        return _coerce(values)

    legacy = _read(LEGACY_PATH)
    if legacy:
        # Version 1 measured press-to-press time; this version measures the gap
        # between release and press, so its threshold does not carry over.
        return save(
            {
                "start_at_login": bool(legacy.get("start_at_login")),
                "fix_enabled": False,
                "calibrated": False,
            }
        )
    return _coerce({})


def save(values: dict[str, Any]) -> dict[str, Any]:
    """Merge `values` into the stored settings and write them atomically."""
    merged = _coerce({**load_raw(), **values})
    destination = settings_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    temporary.replace(destination)
    return merged


def buttons_from(values: dict[str, Any]) -> list[Button]:
    return [Button(name) for name in values.get("buttons", [Button.LEFT.value])]
