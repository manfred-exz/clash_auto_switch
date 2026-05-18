from typing import Optional

from clash_auto_switch.defs import AppConfig, ClashConfig, MonitoringConfig, ProxyServicePair
from clash_auto_switch.project import load_config


def load_app_config() -> Optional[AppConfig]:
    """Load configuration from the standard location."""
    data = load_config()
    if not data:
        return None
    return parse_config_data(data)


def parse_config_data(data: dict) -> AppConfig:
    """Parse configuration data into AppConfig object."""
    clash_data = data.get("clash", {})
    monitoring_data = data.get("monitoring", {})
    tasks_data = data.get("tasks", [])

    clash_config = ClashConfig(
        controller=clash_data.get("controller", "127.0.0.1:9097"),
        secret=clash_data.get("secret"),
        http_proxy=clash_data.get("http_proxy", "http://127.0.0.1:7890"),
    )

    monitoring_config = MonitoringConfig(
        interval_sec=monitoring_data.get("interval_sec", 30.0),
        max_rotations=monitoring_data.get("max_rotations", 0),
        once=monitoring_data.get("once", False),
    )

    tasks = [
        ProxyServicePair(
            proxy_group_name=task_data["proxy_group_name"],
            service_name=task_data["service_name"],
            enabled=task_data.get("enabled", True),
        )
        for task_data in tasks_data
    ]

    return AppConfig(
        clash=clash_config,
        monitoring=monitoring_config,
        tasks=tasks,
    )
