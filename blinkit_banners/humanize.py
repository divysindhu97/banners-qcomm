"""Timing and gesture jitter.

Selector choice is invisible to the server; cadence is not. Analytics SDKs report
interaction events, so the thing worth varying is *when* and *how far* we act, not
*what* we look for.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

Range = tuple[float, float]


@dataclass
class HumanConfig:
    enabled: bool = True
    # Fraction of screen height per feed scroll.
    scroll_distance: Range = (0.55, 0.80)
    # Seconds to dwell after a scroll before capturing.
    settle: Range = (0.9, 2.4)
    carousel_settle: Range = (0.55, 1.10)
    swipe_duration: Range = (0.20, 0.45)
    # Occasionally stop and "read" something, as a person would.
    long_pause_chance: float = 0.15
    long_pause: Range = (3.0, 9.0)
    # Gap between locations within a run.
    location_gap: Range = (20.0, 60.0)
    # Delay before the run starts, so a scheduled job is not punctual to the second.
    start_jitter: Range = (0.0, 0.0)
    tap_jitter_px: int = 12
    # Shuffle the order locations are visited in.
    shuffle_locations: bool = True


@dataclass
class Human:
    cfg: HumanConfig = field(default_factory=HumanConfig)
    seed: int | None = None

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)

    def pick(self, span: Range) -> float:
        low, high = span
        if not self.cfg.enabled:
            return (low + high) / 2
        return self.rng.uniform(low, high)

    def sleep(self, span: Range) -> None:
        duration = self.pick(span)
        if duration > 0:
            time.sleep(duration)

    def settle(self) -> None:
        self.sleep(self.cfg.settle)
        self.maybe_linger()

    def carousel_settle(self) -> None:
        self.sleep(self.cfg.carousel_settle)

    def maybe_linger(self) -> None:
        if self.cfg.enabled and self.rng.random() < self.cfg.long_pause_chance:
            self.sleep(self.cfg.long_pause)

    def swipe_duration(self) -> float:
        return self.pick(self.cfg.swipe_duration)

    def scroll_distance(self) -> float:
        return self.pick(self.cfg.scroll_distance)

    def jitter(self, value: int, limit: int | None = None) -> int:
        """Nudge a coordinate so taps never land on the exact same pixel."""
        span = self.cfg.tap_jitter_px if limit is None else limit
        if not self.cfg.enabled or span <= 0:
            return value
        return value + self.rng.randint(-span, span)

    def order(self, items: list):
        if not (self.cfg.enabled and self.cfg.shuffle_locations):
            return list(items)
        shuffled = list(items)
        self.rng.shuffle(shuffled)
        return shuffled
