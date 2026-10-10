"""Wear history: how often each button bounces, day by day.

For each filtered button and each day the filter ran, it keeps the presses
the filter saw, the bounces it dropped, the dropouts it repaired (a drag's
contact breaking and coming back), and a histogram of the bounces' gaps:
2 ms bins up to that day's window, plus one bin for gaps beyond it (left by
a window made shorter during the day). The wheel fix adds the wheel ticks
it saw and the reversals it dropped.

Counting happens on the hook's thread and only touches memory. The file,
wear.json beside the settings, is written from the UI thread every
SAVE_INTERVAL_S and when the app quits, the same safe way as the settings
(settings.write_json), and keeps KEEP_DAYS days. Nothing about where or
when a click happened is recorded, only these daily counts.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

from . import settings as settings_store
from .core import Button
from .inputs import as_button

log = logging.getLogger(__name__)

WEAR_NAME = "wear.json"
FORMAT_VERSION = 1
KEEP_DAYS = 365
SAVE_INTERVAL_S = 300.0
#: The width of each histogram bin.
BIN_MS = 2
#: The days the History pane shows.
SHOWN_DAYS = 30

# -- trend -----------------------------------------------------------------------
#
# The trend compares the bounce rate of the older half of the shown days with
# the newer half. Wear moves over weeks, not days, and two 15-day halves each
# take in two working weeks and two weekends, so a weekly pattern (games at
# the weekend, office work in the week) never reads as a trend.
TREND_HALF_DAYS = SHOWN_DAYS // 2
#: Below this many clicks in either half there is no trend to speak of: a
#: mouse barely used for two weeks says nothing about its switch.
TREND_MIN_CLICKS = 200
#: The smallest change worth telling someone about. How a mouse is used
#: (dragging, double-click-heavy work) moves the rate by tens of percent with
#: no change in the switch; a wearing switch goes from occasional bounce to
#: frequent bounce, several times over.
TREND_RATIO = 1.5
#: And the change must be more than chance: a two-proportion z-test at about
#: 95% (two-sided). With few bounces (1 against 2, say) the ratio is large
#: but means nothing, and this is what says so.
TREND_Z = 2.0

WORSE, BETTER, SAME, UNKNOWN = "worse", "better", "same", "unknown"
TREND_WORDS = {
    WORSE: "Getting worse",
    BETTER: "Getting better",
    SAME: "About the same",
    UNKNOWN: "Not enough clicks yet",
}

WHEEL = "wheel"
_COUNTS = ("presses", "bounces", "dropouts")


def wear_path() -> Path:
    return settings_store.config_dir() / WEAR_NAME


def wear_backup_path() -> Path:
    return settings_store.config_dir() / f"{WEAR_NAME}.bak"


@dataclass(frozen=True)
class DayStats:
    day: str
    presses: int = 0
    bounces: int = 0
    dropouts: int = 0

    @property
    def clicks(self) -> int:
        """Presses the user made: those the filter saw, less the bounces and
        dropouts, which the switch made."""
        return max(0, self.presses - self.bounces - self.dropouts)

    @property
    def rate(self) -> Optional[float]:
        """Bounces per 100 clicks, or None for a day without clicks."""
        return 100.0 * self.bounces / self.clicks if self.clicks else None


@dataclass(frozen=True)
class Trend:
    key: str
    earlier_rate: Optional[float] = None
    recent_rate: Optional[float] = None
    #: The button's window was not the same throughout, which moves the
    #: rate by itself: a longer window catches more.
    window_changed: bool = False

    @property
    def words(self) -> str:
        return TREND_WORDS[self.key]


@dataclass(frozen=True)
class Histogram:
    #: (lower edge in ms, count), every BIN_MS up to the window.
    bins: list
    overflow: int
    window_ms: int

    @property
    def total(self) -> int:
        return sum(count for _low, count in self.bins) + self.overflow


def classify_trend(earlier: DayStats, recent: DayStats) -> str:
    """WORSE, BETTER, SAME or UNKNOWN from two periods' totals (see the
    TREND_ constants for why these thresholds)."""
    if earlier.clicks < TREND_MIN_CLICKS or recent.clicks < TREND_MIN_CLICKS:
        return UNKNOWN
    n1, n2 = earlier.clicks + earlier.bounces, recent.clicks + recent.bounces
    p1, p2 = earlier.bounces / n1, recent.bounces / n2
    pooled = (earlier.bounces + recent.bounces) / (n1 + n2)
    spread = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    if spread == 0:
        return SAME  # no bounce in either period
    z = (p2 - p1) / spread
    r1, r2 = earlier.rate or 0.0, recent.rate or 0.0
    if z >= TREND_Z and r2 >= r1 * TREND_RATIO:
        return WORSE
    if z <= -TREND_Z and r2 * TREND_RATIO <= r1:
        return BETTER
    return SAME


class WearHistory:
    """Daily wear counts per button. note_* run on the hook's thread; the
    rest on the UI thread."""

    def __init__(
        self,
        path: Optional[Path] = None,
        backup: Optional[Path] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._path = path
        self._backup = backup
        self._clock = clock
        self._lock = threading.Lock()
        # day ("YYYY-MM-DD") -> button name or WHEEL -> counts
        self._days: dict[str, dict[str, dict[str, Any]]] = {}
        self._dirty = False
        # Whether what wear.json holds is in memory (or there is nothing to
        # read): until it is, nothing may be written over the file.
        self._stored_read = False
        self._saved_at = clock()
        self._day_key = ""
        self._day_ends = 0.0

    @property
    def path(self) -> Path:
        return self._path or wear_path()

    @property
    def backup(self) -> Path:
        return self._backup or wear_backup_path()

    # -- days ------------------------------------------------------------------
    def today(self) -> str:
        return self._today(self._clock())

    def _today(self, now: float) -> str:
        if now >= self._day_ends or now < self._day_ends - 90000:
            moment = datetime.fromtimestamp(now)
            self._day_key = moment.date().isoformat()
            midnight = datetime.combine(moment.date() + timedelta(days=1), datetime.min.time())
            self._day_ends = midnight.timestamp()
        return self._day_key

    def day_keys(self, count: int = SHOWN_DAYS) -> list[str]:
        """The last `count` days, oldest first, ending today."""
        end = date.fromisoformat(self.today())
        return [(end - timedelta(days=offset)).isoformat() for offset in range(count - 1, -1, -1)]

    # -- counting (hook thread) ------------------------------------------------
    def _entry(self, name: str) -> dict[str, Any]:
        day = self._days.setdefault(self._today(self._clock()), {})
        entry = day.get(name)
        if entry is None:
            entry = day[name] = {"presses": 0, "bounces": 0, "dropouts": 0, "window": 0, "gaps": {}, "over": 0}
        return entry

    def note_event(self, event: Any, window_ms: int) -> None:
        """Count one ClickEvent from the filter for its button. Releases are
        not counted."""
        if not event.pressed:
            return
        name = as_button(event.button).value
        with self._lock:
            entry = self._entry(name)
            entry["presses"] += 1
            entry["window"] = int(window_ms)
            if event.cancels_held:
                entry["dropouts"] += 1
            elif event.is_bounce:
                entry["bounces"] += 1
                gap = event.gap_ms
                if gap is not None and gap >= 0:
                    if gap > window_ms:
                        entry["over"] += 1
                    else:
                        low = str(int(gap // BIN_MS) * BIN_MS)
                        entry["gaps"][low] = entry["gaps"].get(low, 0) + 1
            self._dirty = True

    def note_wheel(self, _axis: int, dropped: bool) -> None:
        with self._lock:
            day = self._days.setdefault(self._today(self._clock()), {})
            entry = day.setdefault(WHEEL, {"ticks": 0, "dropped": 0})
            entry["ticks"] += 1
            if dropped:
                entry["dropped"] += 1
            self._dirty = True

    # -- reading (UI thread) ---------------------------------------------------
    def _snapshot(self, count: int = SHOWN_DAYS) -> dict[str, dict[str, dict[str, Any]]]:
        """A copy of the last `count` days, so the hook's thread waits on
        the lock no longer than that takes."""
        keys = self.day_keys(count)
        with self._lock:
            return {
                day: {name: {**entry, "gaps": dict(entry.get("gaps", {}))} for name, entry in self._days[day].items()}
                for day in keys
                if day in self._days
            }

    def daily(self, button: Any, count: int = SHOWN_DAYS) -> list[DayStats]:
        name = as_button(button).value
        days = self._snapshot(count)
        result = []
        for key in self.day_keys(count):
            entry = days.get(key, {}).get(name, {})
            result.append(DayStats(key, *(int(entry.get(field, 0)) for field in _COUNTS)))
        return result

    @staticmethod
    def _sum(stats: list[DayStats], label: str = "") -> DayStats:
        return DayStats(
            label,
            sum(day.presses for day in stats),
            sum(day.bounces for day in stats),
            sum(day.dropouts for day in stats),
        )

    def totals(self, button: Any, count: int = SHOWN_DAYS) -> DayStats:
        return self._sum(self.daily(button, count), f"last {count} days")

    def trend(self, button: Any, count: int = SHOWN_DAYS) -> Trend:
        stats = self.daily(button, count)
        half = count // 2
        earlier, recent = self._sum(stats[:half]), self._sum(stats[half:])
        days = self._snapshot(count)
        name = as_button(button).value
        windows = {
            days[key][name]["window"]
            for key in self.day_keys(count)
            if name in days.get(key, {}) and days[key][name].get("window")
        }
        return Trend(classify_trend(earlier, recent), earlier.rate, recent.rate, len(windows) > 1)

    def histogram(self, button: Any, count: int = SHOWN_DAYS, window_ms: int = 0) -> Histogram:
        """Bounce gaps over the last `count` days. The bins reach the longest
        window those days used (or `window_ms`, if longer: the current one)."""
        name = as_button(button).value
        days = self._snapshot(count)
        counts: dict[int, int] = {}
        overflow = 0
        window = int(window_ms)
        for key in self.day_keys(count):
            entry = days.get(key, {}).get(name)
            if not entry:
                continue
            window = max(window, int(entry.get("window", 0)))
            overflow += int(entry.get("over", 0))
            for low, number in entry.get("gaps", {}).items():
                counts[int(low)] = counts.get(int(low), 0) + int(number)
        edges = range(0, window + 1, BIN_MS) if window > 0 else range(0)
        bins = [(low, counts.pop(low, 0)) for low in edges]
        overflow += sum(counts.values())  # beyond every window: none expected
        return Histogram(bins, overflow, window)

    def wheel_totals(self, count: int = SHOWN_DAYS) -> tuple[int, int]:
        """(ticks seen, reversals dropped) over the last `count` days."""
        days = self._snapshot(count)
        ticks = dropped = 0
        for key in self.day_keys(count):
            entry = days.get(key, {}).get(WHEEL, {})
            ticks += int(entry.get("ticks", 0))
            dropped += int(entry.get("dropped", 0))
        return ticks, dropped

    def buttons_with_data(self, count: int = SHOWN_DAYS) -> list[Button]:
        days = self._snapshot(count)
        names = {name for key in self.day_keys(count) for name in days.get(key, {}) if name != WHEEL}
        return [button for button in Button if button.value in names]

    # -- the file ----------------------------------------------------------------
    def _prune(self) -> None:
        """Drop the days older than KEEP_DAYS, counted back from today.

        Except when that would drop every day on record (the newest is a year
        or more behind today): a clock that jumped years ahead (a date set
        wrong, a flat battery) is likelier than a year without a click, and
        pruning against it would delete the history for good. The year is
        then counted back from the newest recorded day instead, and the
        first click on the new day (or the clock coming back) settles it.
        """
        if not self._days:
            return
        today = date.fromisoformat(self.today())
        newest = date.fromisoformat(max(self._days))
        anchor = newest if (today - newest).days >= KEEP_DAYS else today
        oldest = (anchor - timedelta(days=KEEP_DAYS - 1)).isoformat()
        for key in [key for key in self._days if key < oldest]:
            del self._days[key]

    def load(self) -> bool:
        """Add what wear.json (or its spare) holds to the counts in memory.
        A missing or damaged file starts an empty history; the counts are only
        ever added to.

        When the file stays unreadable (a backup tool or virus scanner
        holding it) and its spare can't stand in, nothing changes and this
        returns False. save() then reads again before it writes: writing the
        counts since launch over the file would replace a year of history,
        and a second write would replace its spare too.
        """
        content, stored = settings_store.read_json_checked(self.path, self.backup)
        if not stored:
            log.warning("Couldn't read the wear history; it is read again before anything is saved")
            return False
        days = _valid_days(content.get("days"))
        with self._lock:
            # Anything counted before the file was read is kept on top.
            for key, buttons in days.items():
                for name, entry in buttons.items():
                    mine = self._days.setdefault(key, {}).get(name)
                    if mine is None:
                        self._days[key][name] = entry
                    else:
                        _add(mine, entry)
            self._prune()
            self._stored_read = True
        return True

    def save(self) -> bool:
        """Write the history if anything changed. Returns whether it wrote."""
        with self._lock:
            if not self._dirty:
                return False
        if not self._stored_read and not self.load():
            # Still unreadable: keep the counts and try at the next save.
            self._saved_at = self._clock()
            return False
        with self._lock:
            self._prune()
            content = {
                "version": FORMAT_VERSION,
                "days": {
                    day: {name: {**entry, "gaps": dict(entry.get("gaps", {}))} for name, entry in buttons.items()}
                    for day, buttons in sorted(self._days.items())
                },
            }
            self._dirty = False
        self._saved_at = self._clock()
        try:
            settings_store.write_json(self.path, self.backup, content)
        except OSError as error:
            log.warning("Couldn't save the wear history: %s", error)
            with self._lock:
                self._dirty = True
            return False
        return True

    def save_if_due(self) -> bool:
        if self._clock() - self._saved_at < SAVE_INTERVAL_S:
            return False
        return self.save()

    def clear(self) -> None:
        """Start afresh: the next save replaces whatever the file holds."""
        with self._lock:
            self._days.clear()
            self._dirty = True
            self._stored_read = True


def _count(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, number)


def _valid_days(raw: Any) -> dict[str, dict[str, dict[str, Any]]]:
    """The days in a wear.json, with anything malformed left out."""
    days: dict[str, dict[str, dict[str, Any]]] = {}
    if not isinstance(raw, dict):
        return days
    names = {button.value for button in Button}
    for key, buttons in raw.items():
        try:
            date.fromisoformat(key)
        except (TypeError, ValueError):
            continue
        if not isinstance(buttons, dict):
            continue
        for name, entry in buttons.items():
            if not isinstance(entry, dict):
                continue
            if name == WHEEL:
                days.setdefault(key, {})[name] = {"ticks": _count(entry.get("ticks")), "dropped": _count(entry.get("dropped"))}
            elif name in names:
                gaps = entry.get("gaps") if isinstance(entry.get("gaps"), dict) else {}
                days.setdefault(key, {})[name] = {
                    **{field: _count(entry.get(field)) for field in _COUNTS},
                    "window": _count(entry.get("window")),
                    "gaps": {
                        str(int(low)): _count(number)
                        for low, number in gaps.items()
                        if str(low).isdigit() and int(low) % BIN_MS == 0 and _count(number)
                    },
                    "over": _count(entry.get("over")),
                }
    return days


def _add(mine: dict[str, Any], other: dict[str, Any]) -> None:
    for field, value in other.items():
        if field == "gaps":
            for low, number in value.items():
                mine["gaps"][low] = mine["gaps"].get(low, 0) + number
        elif field == "window":
            mine["window"] = mine.get("window") or value
        else:
            mine[field] = mine.get(field, 0) + value
