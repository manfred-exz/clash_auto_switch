import asyncio
from typing import Any

import httpx

from clash_auto_switch.core.clash_state import ClashProxyState
from clash_auto_switch.core.services.registry import connection_host_patterns


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


def _connection_search_values(connection: dict[str, Any]) -> list[str]:
    values = []

    for key in ("metadata", "rulePayload", "rule", "host", "destinationIP", "network"):
        value = connection.get(key)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, dict):
            values.extend(str(item) for item in value.values() if item is not None)

    return values
