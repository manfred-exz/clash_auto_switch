from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional

import httpx

from clash_auto_switch.core.clash_api_raw import ClashClientRaw, ClashLogEntry
from clash_auto_switch.core.services.registry import connection_host_patterns


@dataclass(frozen=True)
class ProxyGroupState:
    name: str
    now: Optional[str]
    nodes: list[str]
    raw: dict[str, Any]


class ClashApi:
    """Cached Clash proxy facade with explicit invalidation after mutations."""

    def __init__(self, client: ClashClientRaw, *, ttl_sec: float = 5.0) -> None:
        self._client = client
        self._ttl_sec = ttl_sec
        self._proxies: dict[str, dict[str, Any]] = {}
        self._updated_at = 0.0

    async def refresh_proxies(self) -> None:
        data = await self._client.get_proxies()
        proxies = data.get("proxies") if isinstance(data, dict) else None
        if isinstance(proxies, dict):
            self._proxies = {
                str(name): value
                for name, value in proxies.items()
                if isinstance(value, dict)
            }
        else:
            self._proxies = {}
        self._updated_at = time.monotonic()

    def invalidate(self) -> None:
        self._updated_at = 0.0

    async def _ensure_fresh(self) -> None:
        if not self._proxies or time.monotonic() - self._updated_at >= self._ttl_sec:
            await self.refresh_proxies()

    async def get_proxy(self, name: str) -> dict[str, Any]:
        await self._ensure_fresh()
        cached = self._proxies.get(name)
        if cached is not None:
            return dict(cached)

        proxy = await self._client.get_proxy(name)
        self._proxies[name] = proxy
        return dict(proxy)

    async def get_proxy_group(self, name: str) -> ProxyGroupState:
        proxy = await self.get_proxy(name)
        nodes = proxy.get("all") or []
        return ProxyGroupState(
            name=name,
            now=proxy.get("now") if isinstance(proxy.get("now"), str) else None,
            nodes=[node for node in nodes if isinstance(node, str)],
            raw=proxy,
        )

    async def list_proxy_group_names(self) -> list[str]:
        await self._ensure_fresh()
        groups = [
            name
            for name, proxy in self._proxies.items()
            if isinstance(proxy.get("all"), list)
        ]
        return sorted(groups)

    async def select_proxy(self, selector_name: str, proxy_name: str) -> None:
        await self._client.select_proxy(selector_name, proxy_name)
        self.invalidate()
        group = await self.get_proxy(selector_name)
        if group.get("now") != proxy_name:
            raise RuntimeError(
                f"Proxy group '{selector_name}' switch verification failed: "
                f"expected '{proxy_name}', got '{group.get('now')}'"
            )

    async def get_proxy_delay(self, name: str, url: str, timeout_ms: int) -> dict[str, Any]:
        return await self._client.get_proxy_delay(name, url, timeout_ms)

    async def get_connections(self) -> dict[str, Any]:
        return await self._client.get_connections()

    async def close_connection(self, connection_id: str) -> None:
        await self._client.close_connection(connection_id)

    async def close_service_connections(self, service_name: str) -> int:
        connections_payload = await self.get_connections()
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
            *(self.close_connection(connection_id) for connection_id in connection_ids),
            return_exceptions=True,
        )
        return sum(1 for result in results if not isinstance(result, httpx.HTTPError))

    async def iter_logs(self, level: Optional[str] = None) -> AsyncIterator[ClashLogEntry]:
        async for log_entry in self._client.iter_logs(level=level):
            yield log_entry


def connection_matches_service(connection: dict[str, Any], service_name: str) -> bool:
    patterns = connection_host_patterns(service_name)
    if not patterns:
        return False

    haystack = " ".join(_connection_search_values(connection)).lower()
    return any(pattern in haystack for pattern in patterns)


def _connection_search_values(connection: dict[str, Any]) -> list[str]:
    values: list[str] = []

    for key in ("metadata", "rulePayload", "rule", "host", "destinationIP", "network"):
        value = connection.get(key)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, dict):
            values.extend(str(item) for item in value.values() if item is not None)

    return values
