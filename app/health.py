"""Is the mouse bouncing, is the filter keeping up, and is something else in
the way? Plain-words verdicts from the wear history (1.1 trust features).

Three verdicts, the first that applies:

* bounces were caught in the last HEALTH_DAYS days: "Your left button
  double-clicks on its own: caught 14 of 14". The filter knows only the
  bounces its window catches (one that gets past looks like a click), so
  the count is of those; what warns before any get past is the check below,
  on bounce gaps creeping up to the window;
* no bounce, but another mouse utility is running (conflicts): "Another app
  may be changing clicks: Logi Options+";
* otherwise "No bounce seen": the mouse may be fine, or it bounces only now
  and then (or, with nothing counted yet, the check hasn't started).

And a suggestion to raise a button's window when its recent bounce gaps
creep up on it. The filter only sees the bounces its window catches, so a
switch whose bounce gaps keep growing shows up as gaps crowding the top of
the window before any get past it. When the SUGGEST_PERCENTILE gap of the
last SUGGEST_DAYS days is above SUGGEST_NEAR of the window, the suggestion
is the window that puts that gap at SUGGEST_TARGET of it, but never more
than half the fastest deliberate double-click Test and Tune measured for the
button (core.DOUBLE_CLICK_SAFETY_RATIO, the rule calibration itself keeps),
or UNKNOWN_DOUBLE_CLICK_CAP_MS when none was measured. A suggestion only
ever raises a window, and nothing applies one without the user.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from .core import DOUBLE_CLICK_SAFETY_RATIO, MAX_THRESHOLD_MS, Button
from .inputs import as_button, button_name
from .wear import BIN_MS, SHOWN_DAYS, WORSE

#: The days a verdict looks back over: the History pane's.
HEALTH_DAYS = SHOWN_DAYS
#: The days a suggestion looks back over: recent enough to follow a switch
#: that is getting worse, long enough to have bounces to go by.
SUGGEST_DAYS = 14
#: Fewer bounces than this say too little about where their gaps end.
SUGGEST_MIN_BOUNCES = 20
SUGGEST_PERCENTILE = 0.99
#: The gap above this share of the window is "close to it".
SUGGEST_NEAR = 0.8
#: The share of the suggested window that gap then takes.
SUGGEST_TARGET = 0.7
#: A raise smaller than this isn't worth asking about: the gaps are close to
#: the window, but the cap leaves no real room (Test and Tune can check).
SUGGEST_MIN_RAISE_MS = 3
#: With no double-click measured: the window every install had before 1.0
#: (and the one calibration rarely went past).
UNKNOWN_DOUBLE_CLICK_CAP_MS = 60

BOUNCING, CONFLICT, QUIET, WAITING = "bouncing", "conflict", "quiet", "waiting"


@dataclass(frozen=True)
class Suggestion:
    """Raise `button`'s window from `from_ms` to `to_ms`, because `reason`."""

    button: Button
    from_ms: int
    to_ms: int
    reason: str


@dataclass(frozen=True)
class Health:
    verdict: str
    detail: str
    suggestion: Optional[Suggestion] = None
    #: BOUNCING, CONFLICT, QUIET or WAITING, for the UI to pick an icon.
    kind: str = ""


def names_text(names: list[str]) -> str:
    """"left", "left and right", "left, right and back"."""
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def times_text(count: int) -> str:
    return "once" if count == 1 else "twice" if count == 2 else f"{count:,} times"


def percentile_ms(histogram: Any, fraction: float) -> Optional[float]:
    """The gap below which `fraction` of the histogram's bounces fall,
    reading each bin at its upper edge (so never under the truth). Bounces
    counted beyond every bin count as at the window."""
    total = histogram.total
    if total <= 0:
        return None
    wanted = max(1, math.ceil(fraction * total))
    seen = 0
    for low, count in histogram.bins:
        seen += count
        if seen >= wanted:
            # No gap counted is longer than the longest window that caught it.
            return float(min(low + BIN_MS, max(histogram.window_ms, low)))
    return float(histogram.window_ms)


def window_cap(fastest_double_click_ms: Optional[float]) -> int:
    """The longest window a suggestion may go to."""
    if fastest_double_click_ms:
        return min(MAX_THRESHOLD_MS, int(math.floor(fastest_double_click_ms * DOUBLE_CLICK_SAFETY_RATIO)))
    return UNKNOWN_DOUBLE_CLICK_CAP_MS


def suggest(
    button: Any, histogram: Any, window_ms: int, fastest_double_click_ms: Optional[float] = None
) -> tuple[Optional[Suggestion], bool]:
    """(the suggestion for one button, or None; whether its bounces are
    close to the window at all). Close but no suggestion means a longer
    window could catch the user's own double-clicks."""
    if histogram.total < SUGGEST_MIN_BOUNCES or window_ms <= 0:
        return None, False
    gap = percentile_ms(histogram, SUGGEST_PERCENTILE)
    if gap is None or gap <= SUGGEST_NEAR * window_ms:
        return None, False
    # Rounded first: 42 / 0.7 is 60.000000000000004 in floating point.
    target = min(int(math.ceil(round(gap / SUGGEST_TARGET, 6))), window_cap(fastest_double_click_ms), MAX_THRESHOLD_MS)
    if target < window_ms + SUGGEST_MIN_RAISE_MS:
        return None, True
    name = button_name(button).lower()
    where = "past" if gap > window_ms else "close to"
    reason = (
        f"Recent bounces on the {name} button reached about {gap:.0f} ms, {where} its {window_ms} ms window. "
        f"Raise it to {target} ms?"
    )
    return Suggestion(as_button(button), int(window_ms), target, reason), True


def crowded(wear: Any, buttons: Iterable[Any], thresholds: dict) -> list[Button]:
    """The buttons among `buttons` whose recent bounces come close to their
    window (suggest()'s second answer), suggestion or not."""
    found = []
    for button in (as_button(button) for button in buttons):
        window = int(thresholds.get(button, thresholds.get(button.value, 0)))
        if suggest(button, wear.histogram(button, SUGGEST_DAYS, window), window)[1]:
            found.append(button)
    return found


def assess(
    wear: Any,
    buttons: Iterable[Any],
    thresholds: dict,
    conflicts: Iterable[Any] = (),
    double_clicks: Optional[dict] = None,
    watching: bool = False,
) -> Health:
    """The verdict for the filtered `buttons` (see the module notes).
    `watching`: the filter is running now, so counting goes on."""
    buttons = [as_button(button) for button in buttons]
    double_clicks = {as_button(key): value for key, value in (double_clicks or {}).items()}
    caught: list[tuple[Button, int]] = []
    pressed = 0
    for button in buttons:
        totals = wear.totals(button, HEALTH_DAYS)
        pressed += totals.presses
        extra = totals.bounces + totals.dropouts
        if extra:
            caught.append((button, extra))

    best: Optional[Suggestion] = None
    best_ratio = 0.0
    crowded: list[Button] = []
    for button in buttons:
        window = int(thresholds.get(button, thresholds.get(button.value, 0)))
        histogram = wear.histogram(button, SUGGEST_DAYS, window)
        suggestion, near = suggest(button, histogram, window, double_clicks.get(button))
        if suggestion is not None:
            ratio = suggestion.to_ms / max(1, suggestion.from_ms)
            if ratio > best_ratio:
                best, best_ratio = suggestion, ratio
        elif near:
            crowded.append(button)

    found = list(conflicts)
    if caught:
        names = [button_name(button).lower() for button, _count in caught]
        total = sum(count for _button, count in caught)
        if len(names) == 1:
            verdict = f"Your {names[0]} button double-clicks on its own: caught {total:,} of {total:,}"
        else:
            verdict = f"Your {names_text(names)} buttons double-click on their own: caught {total:,} of {total:,}"
        which = "the one extra click" if total == 1 else "every extra click"
        parts = [f"Over the last {HEALTH_DAYS} days the filter ignored {which}."]
        worse = [button_name(button).lower() for button, _count in caught if wear.trend(button, HEALTH_DAYS).key == WORSE]
        if worse:
            parts.append(
                f"The {names_text(worse)} {'button is' if len(worse) == 1 else 'buttons are'} bouncing more often "
                "than two weeks ago: the switch is wearing out, and in time it will need replacing."
            )
        if best is not None:
            parts.append(best.reason)
        elif crowded:
            parts.append(
                f"Bounces on the {names_text([button_name(b).lower() for b in crowded])} button come close to the "
                "window, but a longer one could catch your own double-clicks. Test and Tune can check."
            )
        if found:
            parts.append(
                f"{names_text([conflict.name for conflict in found])} "
                f"{'is' if len(found) == 1 else 'are'} running too and may change clicks as well."
            )
        return Health(verdict, " ".join(parts), best, BOUNCING)

    if found:
        verdict = f"Another app may be changing clicks: {names_text([conflict.name for conflict in found])}"
        parts = [f"{conflict.name} may take over {conflict.takes_text()}." for conflict in found]
        parts.append("If clicks still go wrong, check its button settings, or quit it for a moment and try again.")
        return Health(verdict, " ".join(parts), None, CONFLICT)

    if not pressed:
        detail = (
            "No clicks counted yet. The filter counts them as you use your mouse."
            if watching
            else "Turn on Bounce Filter and it starts counting your clicks."
        )
        return Health("No bounce seen yet", detail, None, WAITING)
    return Health("No bounce seen", "Your mouse may be fine, or it bounces only now and then.", None, QUIET)


def today_sentence(today: dict, caught_total: int, crowded: Iterable[Any] = ()) -> str:
    """The status sentence while the filter runs. `today`: {button: extra
    clicks caught today} for the filtered buttons; `caught_total`: every
    bounce blocked so far; `crowded`: buttons whose recent bounces come
    close to their window (suggest()), for which "every bounce was caught"
    would promise more than the filter knows."""
    bounced = [(as_button(button), count) for button, count in today.items() if count > 0]
    bounced.sort(key=lambda item: list(Button).index(item[0]))
    if not bounced:
        if caught_total > 0:
            return f"No extra clicks today. {caught_total:,} caught so far."
        return "No extra clicks seen yet."
    names = [button_name(button).lower() for button, _count in bounced]
    total = sum(count for _button, count in bounced)
    if len(names) == 1:
        subject = f"Your {names[0]} button bounced"
    else:
        subject = f"Your {names_text(names)} buttons bounced"
    close = {as_button(button) for button in crowded}
    if any(button in close for button, _count in bounced):
        caught = "Some came close to getting past: see History."
    else:
        caught = "The bounce was caught." if total == 1 else "Every bounce was caught."
    return f"{subject} {times_text(total)} today. {caught}"
