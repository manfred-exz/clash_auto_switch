from typing import Optional

from clash_auto_switch.defs import AppConfig, ClashConfig, DisabledNode, MonitoringConfig, ProxyServicePair
from clash_auto_switch.project import load_config, save_config


def load_app_config() -> Optional[AppConfig]:
    """Load configuration from the standard location."""
    data = load_config()
    if not data:
        return None
    return parse_config_data(data)


def parse_config_data(data: dict) -> AppConfig:
    """Parse configuration data into AppConfig object."""
    clash_data = data.get("clash", {})
    tasks_data = data.get("tasks", [])
    disabled_nodes_data = data.get("disabled_nodes", [])

    clash_config = ClashConfig(
        controller=clash_data.get("controller", "127.0.0.1:9097"),
        secret=clash_data.get("secret"),
        http_proxy=clash_data.get("http_proxy", "http://127.0.0.1:7890"),
    )

    tasks = [
        ProxyServicePair(
            proxy_group_name=task_data["proxy_group_name"],
            service_name=task_data["service_name"],
            enabled=task_data.get("enabled", True),
        )
        for task_data in tasks_data
    ]
    disabled_nodes = [
        DisabledNode(
            proxy_group_name=item["proxy_group_name"],
            service_name=item["service_name"],
            node_name=item["node_name"],
        )
        for item in disabled_nodes_data
        if isinstance(item, dict)
        and item.get("proxy_group_name")
        and item.get("service_name")
        and item.get("node_name")
    ]

    return AppConfig(
        clash=clash_config,
        tasks=tasks,
        disabled_nodes=disabled_nodes,
    )


def dump_config_data(config: AppConfig) -> dict:
    """Serialize AppConfig into the config file format."""
    return {
        "clash": {
            "controller": config.clash.controller,
            "secret": config.clash.secret,
            "http_proxy": config.clash.http_proxy,
        },
        "tasks": [
            {
                "proxy_group_name": task.proxy_group_name,
                "service_name": task.service_name,
                "enabled": task.enabled,
            }
            for task in config.tasks
        ],
        "disabled_nodes": [
            {
                "proxy_group_name": node.proxy_group_name,
                "service_name": node.service_name,
                "node_name": node.node_name,
            }
            for node in config.disabled_nodes
        ],
    }


def save_app_config(config: AppConfig) -> bool:
    """Persist AppConfig to the standard config file."""
    return save_config(dump_config_data(config))


def add_task_to_config(config: AppConfig, task: ProxyServicePair) -> bool:
    """Add one enabled monitoring task and persist the config."""
    if any(
        existing.proxy_group_name == task.proxy_group_name
        and existing.service_name == task.service_name
        for existing in config.tasks
    ):
        return True
    config.tasks.append(task)
    return save_app_config(config)
