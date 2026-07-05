from typing import Optional, List, Dict, Protocol
from dataclasses import dataclass, asdict, field

@dataclass
class ClashConfig:
    """Clash controller configuration."""

    controller: str = "127.0.0.1:9097"
    secret: Optional[str] = None
    http_proxy: str = "http://127.0.0.1:7890"


@dataclass
class MonitoringConfig:
    """Monitoring behavior configuration."""

    interval_sec: float = 30.0
    max_rotations: int = 0
    once: bool = False


@dataclass
class ProxyServicePair:
    """Individual monitoring task configuration."""

    proxy_group_name: str
    service_name: str
    enabled: bool = True


class ServiceTaskRef(Protocol):
    proxy_group_name: str
    service_name: str
    enabled: bool


@dataclass
class DisabledNode:
    """User-disabled node for one proxy group and service."""

    proxy_group_name: str
    service_name: str
    node_name: str


@dataclass
class AppConfig:
    """Complete application configuration."""

    clash: ClashConfig
    tasks: List[ProxyServicePair]
    disabled_nodes: List[DisabledNode] = field(default_factory=list)


@dataclass
class ServiceRecord:
    """Record of a service's status on a specific node."""
    service_name: str
    last_available_time: Optional[float]  # timestamp when service was last available
    last_check_time: float  # timestamp when last checked
    status: str  # "available", "failed", "unknown"
    proxy_group: Optional[str] = None  # proxy group this service check belongs to
    reliability_score: float = 0.0  # reliability metric (0.0 to 1.0)
    total_checks: int = 0  # total number of checks performed
    successful_checks: int = 0  # total number of successful checks

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "ServiceRecord":
        return cls(**data)


@dataclass
class NodeConnectivityRecord:
    """Node-level connectivity score, shared across services using the same node."""
    score: float = 1.0  # 0.0 to 1.0, defaults optimistic
    total_checks: int = 0
    successful_checks: int = 0
    last_check_time: float = 0.0

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "NodeConnectivityRecord":
        return cls(**data)
