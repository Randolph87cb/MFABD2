"""Shared adaptive polling delays for state-based automation waits."""

from __future__ import annotations

from dataclasses import dataclass


DEFAULT_POLL_DELAYS = (1.0, 2.0, 3.0, 5.0, 8.0)


@dataclass
class AdaptivePoll:
    """Increase polling delays while a state is unchanged, then cap them."""

    delays: tuple[float, ...] = DEFAULT_POLL_DELAYS
    _index: int = 0

    def __post_init__(self) -> None:
        if not self.delays or any(delay <= 0 for delay in self.delays):
            raise ValueError("adaptive polling delays must be positive")

    def reset(self) -> None:
        self._index = 0

    def next_delay(self, *, remaining: float | None = None) -> float:
        delay = self.delays[min(self._index, len(self.delays) - 1)]
        self._index += 1
        if remaining is not None:
            delay = min(delay, max(0.0, remaining))
        return delay
