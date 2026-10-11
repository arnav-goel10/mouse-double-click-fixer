"""What the window remembers for itself, beside the settings: the Settings
pane last shown, which a Mac Settings window opens on again.

Kept apart from settings.json, which only the controller writes, in a small
file of its own in the same folder. Losing it costs nothing but the pane, so
it is read and written plainly: a file that can't be read is no file.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from .. import settings as settings_store

log = logging.getLogger(__name__)

FILE_NAME = "window.json"


def path() -> Path:
    return settings_store.config_dir() / FILE_NAME


def load() -> dict[str, Any]:
    try:
        content = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return content if isinstance(content, dict) else {}


def remember(**values: Any) -> None:
    """Keep `values`, written beside the old file and moved over it, so a
    crash leaves one or the other."""
    stored = load()
    merged = {**stored, **values}
    if merged == stored:
        return
    target = path()
    temporary = target.with_name(target.name + ".tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(merged, indent=2), encoding="utf-8")
        os.replace(temporary, target)
    except OSError as error:
        log.info("Couldn't keep the window's state: %s", error)
