import asyncio
from typing import Any, Callable, Optional

import httpx

from clash_auto_switch.core.clash_state import ClashProxyState
from clash_auto_switch.core.service_hosts import connection_host_patterns
from clash_auto_switch.defs import ProxyServicePair


EventFunc = Callable[[str, str], None]


def connection_matches_service(connection: dict[str, Any], service_name: str) -> bool:
    patterns = connection_host_patterns(service_name)
    if not patterns:
        return False

    haystack = " ".join(_connection_search_values(connection)).lower()
    return any(pattern in haystack for pattern in patterns)


async def close_service_connections(
    client: ClashProxyState,
    service_name: str,
) -> int:
    """Close active Clash connections that match a service's known host patterns."""
    connections_payload = await client.get_connections()
    connections = connections_payload.get("connections") or []
    if not isinstance(connections, list):
        return 0

    connection_ids = [
        connection.get("id")
        for connection in connections
        if isinstance(connection, dict)
        and isinstance(connection.get("id"), str)
        and connection_matches_service(connection, service_name)
    ]
    if not connection_ids:
        return 0

    results = await asyncio.gather(
        *(client.close_connection(connection_id) for connection_id in connection_ids),
        return_exceptions=True,
    )
    return sum(1 for result in results if not isinstance(result, httpx.HTTPError))


async def close_task_service_connections(
    client: ClashProxyState,
    task: ProxyServicePair,
) -> int:
    return await close_service_connections(client, task.service_name)


async def close_task_service_connections_best_effort(
    client: ClashProxyState,
    task: ProxyServicePair,
    event_handler: Optional[EventFunc] = None,
) -> int:
    """Close a task's service connections and report failures without raising."""
    try:
        closed_count = await close_task_service_connections(client, task)
    except Exception as exc:
        closed_count = 0
        if event_handler is not None:
            event_handler(task.service_name, f"关闭连接失败 | {exc}")

    if event_handler is not None:
        event_handler(task.service_name, f"关闭连接 | {closed_count} 个")
    return closed_count


def _connection_search_values(connection: dict[str, Any]) -> list[str]:
    values = []

    for key in ("metadata", "rulePayload", "rule", "host", "destinationIP", "network"):
        value = connection.get(key)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, dict):
            values.extend(str(item) for item in value.values() if item is not None)

    return values
