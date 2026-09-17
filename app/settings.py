"""Small JSON-backed settings store."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULTS = {
    "bounce_threshold_ms": 80,
    "fix_enabled": False,
    "start_at_login": False,
    "calibration_complete": False,
}


def settings_path() -> Path:
    return Path.home() / ".doubleclick-fixer.json"


def load() -> dict[str, Any]:
    try:
        values = json.loads(settings_path().read_text(encoding="utf-8"))
        return {**DEFAULTS, **values}
    except (OSError, ValueError, TypeError):
        return DEFAULTS.copy()


def save(values: dict[str, Any]) -> None:
    destination = settings_path()
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps({**DEFAULTS, **values}, indent=2), encoding="utf-8")
    temporary.replace(destination)
