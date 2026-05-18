from typing import Optional, List, Dict
from dataclasses import dataclass, asdict

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
    monitoring: MonitoringConfig
    tasks: List[ProxyServicePair]
    disabled_nodes: Optional[List[DisabledNode]] = None

    def __post_init__(self) -> None:
        if self.disabled_nodes is None:
            self.disabled_nodes = []


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
