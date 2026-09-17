"""Platform-neutral double-click detection and suppression logic."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Optional

DEFAULT_BOUNCE_THRESHOLD_MS = 80
MAX_BOUNCE_THRESHOLD_MS = 120
MIN_DOUBLE_CLICK_SAMPLES = 10

@dataclass(frozen=True)
class ClickResult:
    """The result of processing one primary-button click."""

    accepted: bool
    interval_ms: Optional[float]
    is_double_click: bool


class DoubleClickEngine:
    """Classify clicks and optionally suppress clicks inside a debounce window."""

    def __init__(self, threshold_ms: int = 500, suppression_enabled: bool = False) -> None:
        self.threshold_ms = max(20, int(threshold_ms))
        self.suppression_enabled = suppression_enabled
        self._last_click_at: Optional[float] = None

    def reset(self) -> None:
        self._last_click_at = None

    def process_click(self, timestamp: Optional[float] = None) -> ClickResult:
        now = monotonic() if timestamp is None else float(timestamp)
        interval_ms = None
        is_double_click = False

        if self._last_click_at is not None:
            interval_ms = max(0.0, (now - self._last_click_at) * 1000)
            is_double_click = interval_ms <= self.threshold_ms

        accepted = not (self.suppression_enabled and is_double_click)
        self._last_click_at = now
        return ClickResult(accepted, interval_ms, is_double_click)


class ThresholdCalibrator:
    """Learn a bounce threshold from a labeled calibration session."""

    def __init__(self) -> None:
        self.single_click_intervals_ms: list[float] = []
        self.intentional_double_intervals_ms: list[float] = []
        self.isolated_single_clicks = 0

    def add(self, interval_ms: Optional[float]) -> None:
        self.add_intentional_double(interval_ms)

    def add_single_click(self, interval_ms: Optional[float]) -> None:
        if interval_ms is not None and 20 <= interval_ms <= 250:
            self.single_click_intervals_ms.append(float(interval_ms))

    def record_isolated_single_click(self) -> None:
        self.isolated_single_clicks += 1

    def add_intentional_double(self, interval_ms: Optional[float]) -> None:
        if interval_ms is not None and 20 <= interval_ms <= 2000:
            self.intentional_double_intervals_ms.append(float(interval_ms))

    @property
    def sample_count(self) -> int:
        return self.isolated_single_clicks + len(self.single_click_intervals_ms) + len(self.intentional_double_intervals_ms)

    @property
    def double_click_count(self) -> int:
        return len(self.intentional_double_intervals_ms)

    def suggested_threshold(self) -> Optional[int]:
        if self.isolated_single_clicks < 10 or len(self.intentional_double_intervals_ms) < MIN_DOUBLE_CLICK_SAMPLES:
            return None
        if not self.single_click_intervals_ms:
            return int(max(20, min(MAX_BOUNCE_THRESHOLD_MS, min(self.intentional_double_intervals_ms) - 20)))

        candidates = range(20, MAX_BOUNCE_THRESHOLD_MS + 1, 5)
        best = min(
            candidates,
            key=lambda threshold: (
                sum(interval > threshold for interval in self.single_click_intervals_ms)
                + 4 * sum(interval <= threshold for interval in self.intentional_double_intervals_ms),
                threshold,
            ),
        )
        return int(best)
