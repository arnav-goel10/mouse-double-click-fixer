"""Platform-neutral click classification and switch-bounce filtering.

Switch bounce ("chatter") is a hardware fault: the metal contact inside the
button vibrates on release and the controller reports a second press that the
finger never made. The signature is the gap between a *release* and the next
*press*: chatter lands in the single-digit to low tens of milliseconds, while a
deliberate double-click leaves the button up for far longer. Filtering on that
release-to-press gap is what lets the filter drop chatter without touching
normal double-clicks, and it is the measurement hardware debounce settings use
too.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from statistics import median
from time import monotonic
from typing import Optional

# A conservative default: longer than almost any real bounce, far shorter than
# the gap a human leaves between the two halves of a double-click.
DEFAULT_THRESHOLD_MS = 60
MIN_THRESHOLD_MS = 5
MAX_THRESHOLD_MS = 200

# Calibration needs enough evidence to be worth trusting.
REQUIRED_SINGLE_CLICKS = 12
REQUIRED_DOUBLE_CLICKS = 5

# Gaps below this are treated as bounce candidates while calibrating; a human
# cannot release and press again this fast.
BOUNCE_CANDIDATE_MS = 45.0
# Chatter comes and goes, so a suggestion never drops below this even when the
# calibration run happened to catch only mild bounce.
MIN_SUGGESTION_MS = 25.0
# Safety margin kept between the suggested threshold and the fastest real
# double-click the user produced.
DOUBLE_CLICK_SAFETY_RATIO = 0.5


class Button(str, Enum):
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"

    @property
    def label(self) -> str:
        return {"left": "Left", "right": "Right", "middle": "Middle"}[self.value]


@dataclass(frozen=True)
class ClickEvent:
    """One press or release after the filter has made its decision."""

    button: Button
    pressed: bool
    accepted: bool
    #: Release-to-press gap in milliseconds; only set on a press.
    gap_ms: Optional[float]
    #: Press-to-press interval in milliseconds; only set on a press.
    interval_ms: Optional[float]
    #: A release held back in case the contact is only dropping out mid-hold.
    #: The caller must suppress it and deliver it later if `commit_held`
    #: says so.
    held: bool = False
    #: A press that cancelled a held release: the contact dropped out and came
    #: back, so neither the release nor this press ever reach applications.
    cancels_held: bool = False
    #: A press that arrived after a held release had already expired: the
    #: caller must deliver that release first, then this press.
    flush_held: bool = False
    #: Real, but held back by the hook so it reaches apps after an event the
    #: app re-sent just before it (see GlobalClickFilter._defer).
    deferred: bool = False

    @property
    def is_bounce(self) -> bool:
        return self.pressed and not self.accepted and not self.flush_held and not self.deferred


#: Releases are held back once the button has been down this long. Worn
#: switches drop contact as early as 60 ms into a drag, which overlaps the
#: length of an ordinary click, so only the briefest taps skip the hold.
HOLD_AFTER_MS = 30.0
#: A release this soon after its press is the contact bouncing as it closes
#: (no finger lets go that fast), so it is held too: if the contact settles
#: and presses again, the press and the hold that follows stay intact.
IMPOSSIBLE_TAP_MS = 12.0


class BounceFilter:
    """Decide whether each press and release is real or switch bounce.

    The filter is fed raw press/release events for one button.

    * A press that arrives within the threshold of the previous release is
      bounce and is suppressed, together with its matching release, so no
      application ever sees half a click.
    * While the button is held (a drag), the contact can drop out for a few
      milliseconds, which looks like a release followed by a press. Such a
      release is held back for the threshold: if a press follows in time, both
      are dropped and the drag carries on; otherwise the release is delivered
      late by the caller (see `commit_held`).
    """

    def __init__(
        self,
        threshold_ms: float = DEFAULT_THRESHOLD_MS,
        enabled: bool = True,
        button: Button = Button.LEFT,
        hold_releases: bool = True,
    ) -> None:
        self.threshold_ms = clamp_threshold(threshold_ms)
        self.enabled = enabled
        self.button = button
        self.hold_releases = hold_releases
        self._last_release_at: Optional[float] = None
        self._last_press_at: Optional[float] = None
        self._held_release_at: Optional[float] = None
        # Numbers each held release, so a timer settles only its own one.
        self._held_id = 0
        self._swallow_release = False
        self.filtered_count = 0

    def reset(self) -> None:
        self._last_release_at = None
        self._last_press_at = None
        self._held_release_at = None
        self._swallow_release = False

    @property
    def holding_release(self) -> bool:
        return self._held_release_at is not None

    def press(self, timestamp: Optional[float] = None) -> ClickEvent:
        now = monotonic() if timestamp is None else float(timestamp)
        flush = False
        if self._held_release_at is not None:
            held_gap = max(0.0, (now - self._held_release_at) * 1000)
            if self.enabled and held_gap <= self.threshold_ms:
                # The contact dropped out mid-hold and came back: the button
                # never really went up. Drop both; the drag continues, and the
                # eventual real release must go through.
                self._held_release_at = None
                self._swallow_release = False
                self.filtered_count += 1
                return ClickEvent(self.button, True, False, held_gap, None, cancels_held=True)
            # The held release was real; it has to be delivered before this.
            self._last_release_at = self._held_release_at
            self._held_release_at = None
            flush = True

        gap_ms = None if self._last_release_at is None else max(0.0, (now - self._last_release_at) * 1000)
        interval_ms = None if self._last_press_at is None else max(0.0, (now - self._last_press_at) * 1000)
        is_bounce = gap_ms is not None and gap_ms <= self.threshold_ms
        accepted = not (self.enabled and is_bounce)

        if not accepted:
            self.filtered_count += 1

        self._swallow_release = not accepted
        self._last_press_at = now
        if flush and accepted:
            # Delivered by the caller, after the held release.
            return ClickEvent(self.button, True, False, gap_ms, interval_ms, flush_held=True)
        return ClickEvent(self.button, True, accepted, gap_ms, interval_ms)

    def release(self, timestamp: Optional[float] = None, allow_hold: bool = True) -> ClickEvent:
        """`allow_hold=False` delivers the release at once, for when the caller
        could not re-send it later (Windows blocks sending input to apps
        running as administrator)."""
        now = monotonic() if timestamp is None else float(timestamp)
        if self._swallow_release:
            self._swallow_release = False
            self._last_release_at = now
            return ClickEvent(self.button, False, False, None, None)
        held_ms = None if self._last_press_at is None else (now - self._last_press_at) * 1000
        worth_holding = held_ms is not None and (held_ms >= HOLD_AFTER_MS or held_ms < IMPOSSIBLE_TAP_MS)
        if self.enabled and self.hold_releases and allow_hold and worth_holding:
            self._held_release_at = now
            self._held_id += 1
            return ClickEvent(self.button, False, False, None, None, held=True)
        self._last_release_at = now
        return ClickEvent(self.button, False, True, None, None)

    @property
    def held_id(self) -> Optional[int]:
        """Identifies the release being held back, or None. A counter rather
        than its timestamp: Windows stamps events in ~16 ms ticks, so two
        releases in one burst can share a time."""
        return self._held_id if self._held_release_at is not None else None

    def commit_held(self, expected: Optional[int] = None) -> bool:
        """Settle a held release once the threshold has passed.

        Returns True when the release was real and must now be delivered.
        `expected` (from `held_id`) makes a timer settle only the release it
        was started for: after a dropout is cancelled, a later release can be
        held while the first timer is still pending, and that one needs its
        own full window.
        """
        if self._held_release_at is None:
            return False
        if expected is not None and self._held_id != expected:
            return False
        self._last_release_at = self._held_release_at
        self._held_release_at = None
        return True


def clamp_threshold(value: float) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError, OverflowError):  # OverflowError: infinity
        return DEFAULT_THRESHOLD_MS
    return max(MIN_THRESHOLD_MS, min(MAX_THRESHOLD_MS, number))


@dataclass(frozen=True)
class Suggestion:
    """The outcome of a calibration run."""

    threshold_ms: int
    bounces_seen: int
    worst_bounce_ms: Optional[float]
    fastest_double_click_ms: float
    headroom_ms: float
    confident: bool
    summary: str


class Calibrator:
    """Learn a threshold from two labeled phases of clicking.

    Phase one is isolated single clicks: every short release-to-press gap in it
    is chatter, because the user only pressed once. Phase two is deliberate
    double-clicks, which set the ceiling the threshold has to stay under.
    """

    def __init__(self) -> None:
        self.single_gaps_ms: list[float] = []
        self.double_gaps_ms: list[float] = []
        self.single_clicks = 0
        self.double_clicks = 0

    def add_single_click(self, gap_ms: Optional[float]) -> bool:
        """Record one press from the isolated-click phase.

        Returns True when the press counted as a genuine click, False when it
        looked like bounce and was recorded as evidence instead.
        """
        if gap_ms is not None and gap_ms <= BOUNCE_CANDIDATE_MS:
            self.single_gaps_ms.append(float(gap_ms))
            return False
        self.single_clicks += 1
        return True

    def add_double_click(self, gap_ms: Optional[float]) -> bool:
        """Record the second press of a deliberate double-click."""
        if gap_ms is None:
            return False
        if gap_ms <= BOUNCE_CANDIDATE_MS:
            # Chatter landed inside the pair; count it as bounce evidence.
            self.single_gaps_ms.append(float(gap_ms))
            return False
        self.double_gaps_ms.append(float(gap_ms))
        self.double_clicks += 1
        return True

    @property
    def single_progress(self) -> float:
        return min(1.0, self.single_clicks / REQUIRED_SINGLE_CLICKS)

    @property
    def double_progress(self) -> float:
        return min(1.0, self.double_clicks / REQUIRED_DOUBLE_CLICKS)

    @property
    def has_enough_singles(self) -> bool:
        return self.single_clicks >= REQUIRED_SINGLE_CLICKS

    @property
    def has_enough_doubles(self) -> bool:
        return self.double_clicks >= REQUIRED_DOUBLE_CLICKS

    def suggest(self) -> Optional[Suggestion]:
        if not self.has_enough_doubles:
            return None

        fastest_double = min(self.double_gaps_ms)
        # Never allow a threshold that could eat the user's own double-click.
        ceiling = max(MIN_THRESHOLD_MS, fastest_double * DOUBLE_CLICK_SAFETY_RATIO)
        worst_bounce = max(self.single_gaps_ms) if self.single_gaps_ms else None

        if worst_bounce is None:
            # No chatter observed. Keep a light default rather than nothing, so
            # a fault that only shows up occasionally is still caught.
            threshold = min(DEFAULT_THRESHOLD_MS, ceiling)
            confident = True
            summary = (
                "No bounce was detected during calibration. A "
                f"{clamp_threshold(threshold)} ms filter is kept as insurance and stays well clear "
                f"of your fastest double-click ({fastest_double:.0f} ms)."
            )
        else:
            # Sit above the worst bounce seen, with margin, but under the
            # ceiling. Chatter is intermittent, so the worst gap measured in a
            # minute of clicking is a floor, not a maximum.
            wanted = max(worst_bounce * 2, worst_bounce + 15, MIN_SUGGESTION_MS)
            threshold = min(wanted, ceiling)
            confident = threshold >= worst_bounce + 5
            typical = median(self.single_gaps_ms)
            count = len(self.single_gaps_ms)
            summary = (
                f"{count} bounce event{'s' if count != 1 else ''} measured, typically {typical:.0f} ms "
                f"and up to {worst_bounce:.0f} ms. Your fastest deliberate double-click left "
                f"{fastest_double:.0f} ms between release and press, so a "
                f"{clamp_threshold(threshold)} ms filter removes the bounce with room to spare."
            )
            if not confident:
                summary = (
                    f"Bounce reached {worst_bounce:.0f} ms but your fastest double-click was only "
                    f"{fastest_double:.0f} ms apart, which leaves little room. "
                    f"{clamp_threshold(threshold)} ms is the safe limit here; if bounce still gets "
                    "through, raise it slightly and double-click a little more slowly."
                )

        value = clamp_threshold(threshold)
        return Suggestion(
            threshold_ms=value,
            bounces_seen=len(self.single_gaps_ms),
            worst_bounce_ms=worst_bounce,
            fastest_double_click_ms=fastest_double,
            headroom_ms=fastest_double - value,
            confident=confident,
            summary=summary,
        )
