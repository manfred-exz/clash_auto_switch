from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal, Optional, Protocol


class ServiceCheckResult(Protocol):
    name: str
    status: str
    region: Optional[str]


@dataclass(frozen=True)
class ServiceHostPatterns:
    """Host patterns used for service traffic detection."""

    trigger_hosts: tuple[str, ...]
    extra_connection_hosts: tuple[str, ...] = ()
    active_connection_hosts: tuple[str, ...] = ()

    @property
    def connection_match_hosts(self) -> tuple[str, ...]:
        return self.extra_connection_hosts or self.trigger_hosts


class ServiceChecker(ABC):
    """Base class for one service availability checker."""

    service_name: str
    host_patterns: Optional[ServiceHostPatterns] = None
    trigger_mode: Literal["traffic", "periodic"] = "traffic"
    close_connections_on_switch: bool = True
    periodic_interval_sec: float = 60.0

    @abstractmethod
    async def check(self, proxy: Optional[str] = None) -> ServiceCheckResult:
        """Return the service availability through the given proxy."""
