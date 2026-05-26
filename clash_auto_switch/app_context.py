from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Optional

from clash_auto_switch.core.check_scheduler import AdaptiveCheckScheduler
from clash_auto_switch.core.clash_api import ClashApi
from clash_auto_switch.core.diagnostic_log import DiagnosticLogger
from clash_auto_switch.core.storage import NodeHistoryStorage
from clash_auto_switch.defs import AppConfig


@dataclass
class AppContext:
    """Process-wide application dependencies."""

    config: AppConfig
    storage: NodeHistoryStorage
    diagnostics: DiagnosticLogger
    check_scheduler: AdaptiveCheckScheduler
    _clash: ClashApi | None = None

    _current: ClassVar[Optional["AppContext"]] = None

    @property
    def clash(self) -> ClashApi:
        if self._clash is None:
            raise RuntimeError("ClashApi has not been initialized")
        return self._clash

    @classmethod
    def initialize(cls, config: AppConfig) -> "AppContext":
        cls._current = cls(
            config=config,
            storage=NodeHistoryStorage(),
            diagnostics=DiagnosticLogger(),
            check_scheduler=AdaptiveCheckScheduler(),
        )
        return cls._current

    def set_clash(self, clash: ClashApi) -> None:
        self._clash = clash

    def clear_clash(self) -> None:
        self._clash = None

    @classmethod
    def current(cls) -> "AppContext":
        if cls._current is None:
            raise RuntimeError("AppContext has not been initialized")
        return cls._current

    @classmethod
    def reset(cls) -> None:
        cls._current = None
