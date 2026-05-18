from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional

from clash_auto_switch.core.clash_api import ClashClient, ClashLogEntry


@dataclass(frozen=True)
class ProxyGroupState:
    name: str
    now: Optional[str]
    nodes: list[str]
    raw: dict[str, Any]


class ClashProxyState:
    """Cached Clash proxy facade with explicit invalidation after mutations."""

    def __init__(self, client: ClashClient, *, ttl_sec: float = 5.0) -> None:
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

    async def iter_logs(self, level: Optional[str] = None) -> AsyncIterator[ClashLogEntry]:
        async for log_entry in self._client.iter_logs(level=level):
            yield log_entry
