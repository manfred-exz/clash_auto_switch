from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional


MIN_CHECK_INTERVAL_SEC = 5.0
MAX_CHECK_INTERVAL_SEC = 30.0 * 60.0


@dataclass
class CheckScheduleState:
    interval_sec: float = MIN_CHECK_INTERVAL_SEC
    last_check_at: Optional[float] = None
    success_streak: int = 0
    failure_streak: int = 0


class AdaptiveCheckScheduler:
    """Adaptive per-service minimum interval controller."""

    def __init__(
        self,
        *,
        min_interval_sec: float = MIN_CHECK_INTERVAL_SEC,
        max_interval_sec: float = MAX_CHECK_INTERVAL_SEC,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.min_interval_sec = min_interval_sec
        self.max_interval_sec = max_interval_sec
        self.clock = clock
        self._states: dict[str, CheckScheduleState] = {}

    def state(self, service_name: str) -> CheckScheduleState:
        return self._states.setdefault(
            service_name,
            CheckScheduleState(interval_sec=self.min_interval_sec),
        )

    def can_check(self, service_name: str, *, force: bool = False) -> bool:
        if force:
            return True
        state = self.state(service_name)
        if state.last_check_at is None:
            return True
        return self.clock() - state.last_check_at >= state.interval_sec

    def remaining_sec(self, service_name: str) -> float:
        state = self.state(service_name)
        if state.last_check_at is None:
            return 0.0
        return max(0.0, state.interval_sec - (self.clock() - state.last_check_at))

    def record_result(self, service_name: str, ok: bool) -> CheckScheduleState:
        state = self.state(service_name)
        state.last_check_at = self.clock()
        if ok:
            state.success_streak += 1
            state.failure_streak = 0
            state.interval_sec = min(self.max_interval_sec, state.interval_sec * 2.0)
        else:
            state.failure_streak += 1
            state.success_streak = 0
            state.interval_sec = max(self.min_interval_sec, state.interval_sec / 2.0)
        return state


def format_interval(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 60.0:
        return f"{seconds:.0f} 秒"
    minutes = seconds / 60.0
    if minutes < 60.0:
        return f"{minutes:.1f} 分钟"
    return f"{minutes / 60.0:.1f} 小时"
