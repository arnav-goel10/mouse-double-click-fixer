"""What the menus, the tray and the panes say about the filter, in one place.

The menu bar menu, the Dock menu, the tray's menu and flyout, and the panes'
status lines all read the controller the same way, so they never disagree.
"""

from __future__ import annotations

from typing import Iterable

from .. import DISPLAY_NAME
from .theme import IS_MAC

#: How long "Pause" pauses filtering for, from the menus (the shortcut's
#: time is the controller's own, the same 15 minutes).
PAUSE_MINUTES = 15

#: The two strengths (the controller's "strength" setting).
AUTOMATIC = "automatic"
CUSTOM = "custom"


def platform_case(text: str) -> str:
    """Menu wording in the platform's case: Title Case on macOS, sentence
    case on Windows (the first word keeps its capital)."""
    if IS_MAC:
        return text
    first, *rest = text.split(" ")
    return " ".join([first, *(word.lower() for word in rest)])


def pause_title() -> str:
    return platform_case(f"Pause for {PAUSE_MINUTES} Minutes")


def pause_item_title(controller) -> str:
    """The menus' Pause item: "Resume" while paused (for a while, or for a
    game in front), else "Pause for 15 Minutes"."""
    return "Resume" if controller.paused else pause_title()


def can_pause(controller) -> bool:
    """A pause means something only while the filter is wanted on and not
    paused already."""
    return bool(controller.wanted) and not controller.paused


def pause_item_shown(controller) -> bool:
    """Whether the Pause item has anything to do: resume, or pause."""
    return bool(controller.paused) or can_pause(controller)


def toggle_pause(controller) -> None:
    """The menus' Pause item: pause for PAUSE_MINUTES, or end a pause."""
    if controller.paused:
        controller.resume()
    elif can_pause(controller):
        controller.pause(PAUSE_MINUTES)


def shortcut_shown(controller) -> str:
    """The pause shortcut to show beside Pause ("⌃⌥⌘P"), or "" while it is
    turned off."""
    if not controller.settings.get("pause_shortcut_enabled", True):
        return ""
    return controller.pause_shortcut_text or ""


def _bounces(count: int) -> str:
    return f"{count:,} bounce{'' if count == 1 else 's'}"


def _without_stop(sentence: str) -> str:
    return sentence[:-1] if sentence.endswith(".") else sentence


def status_line(controller) -> str:
    """The menu bar and Dock menus' first, disabled line: "Filtering · 1,981
    bounces blocked" while it runs, else the status sentence as a line
    ("Paused until 3:45 PM", "Off")."""
    if controller.active:
        return f"Filtering · {_bounces(controller.filtered_total)} blocked"
    return _without_stop(controller.status_sentence())


def tooltip(controller) -> str:
    """The icon's tooltip: the app's name and a word or two of status, with
    no instructions ("Mouse Double-Click Fixer: on")."""
    if controller.active:
        state = "on"
    else:
        line = _without_stop(controller.status_sentence())
        state = line[:1].lower() + line[1:] if line else "off"
    return f"{DISPLAY_NAME}: {state}"


def strength(controller) -> str:
    """"automatic" (each button's measured window, or the default) or
    "custom" (the windows the user chose)."""
    value = controller.settings.get("strength", AUTOMATIC)
    return value if value in (AUTOMATIC, CUSTOM) else AUTOMATIC


def join_words(words: Iterable[str]) -> str:
    """"side buttons", "side buttons and the wheel", "a, b and c"."""
    words = [word for word in words if word]
    if len(words) <= 1:
        return "".join(words)
    return f"{', '.join(words[:-1])} and {words[-1]}"


def conflict_title(conflict) -> str:
    return f"{conflict.name} is running"


def conflict_detail(conflict) -> str:
    """What another mouse utility may take over, in a sentence."""
    takes = [word if word.startswith("the ") else f"the {word}" for word in conflict.takes if word]
    if not takes:
        return "It may change clicks before this app sees them."
    return f"It may take over {join_words(takes)}. If those act up, check its settings."
