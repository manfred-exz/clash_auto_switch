"""
Async Clash REST API client using httpx.

Covers endpoints documented in:
- RESTful API: https://clash.gitbook.io/doc/restful-api
- Common: https://clash.gitbook.io/doc/restful-api/common
- Proxies: https://clash.gitbook.io/doc/restful-api/proxies
- Config: https://clash.gitbook.io/doc/restful-api/config

Usage example:

    import asyncio
    from clash_api import ClashClient

    async def main():
        async with ClashClient.from_external_controller("127.0.0.1:8080", secret=None) as client:
            proxies = await client.get_proxies()
            print(proxies)

    asyncio.run(main())
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Dict, Optional, Set

import httpx


class ClashClient:
    """Async client for Clash REST API.

    Parameters
    - base_url: Full base URL including scheme and host:port, e.g., "http://127.0.0.1:8080".
    - secret: If set, will add Authorization: Bearer <secret> header.
    - verify_ssl: Whether to verify TLS certificates (for https base_url).
    - timeout: Default request timeout in seconds.
    - http2: Whether to enable HTTP/2 on the underlying client.
    - proxy: Optional upstream proxy for these API calls (rarely needed).
    """

    def __init__(
        self,
        base_url: str,
        *,
        secret: Optional[str] = None,
        verify_ssl: bool = True,
        timeout: Optional[float] = 30.0,
        http2: bool = True,
        proxy: Optional[str] = None,
    ) -> None:
        headers: Dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": (
                "ClashClient/1.0 (+https://clash.gitbook.io/doc/restful-api)"
            ),
        }
        if secret:
            headers["Authorization"] = f"Bearer {secret}"

        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers=headers,
            timeout=timeout,
            verify=verify_ssl,
            http2=http2,
            proxy=proxy,
        )

    # ---------- Lifecycle ----------
    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "ClashClient":  # noqa: D401
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: D401
        await self.aclose()

    # ---------- Constructors ----------
    @classmethod
    def from_external_controller(
        cls,
        external_controller: str,
        *,
        secret: Optional[str] = None,
        scheme: str = "http",
        verify_ssl: bool = True,
        timeout: Optional[float] = 30.0,
        http2: bool = True,
        proxy: Optional[str] = None,
    ) -> "ClashClient":
        """Create client from Clash external-controller string.

        Accepts values like "127.0.0.1:8080" or full URLs like "http://127.0.0.1:8080".
        """
        base_url = (
            external_controller
            if "://" in external_controller
            else f"{scheme}://{external_controller}"
        )
        return cls(
            base_url,
            secret=secret,
            verify_ssl=verify_ssl,
            timeout=timeout,
            http2=http2,
            proxy=proxy,
        )

    # ---------- Common ----------
    async def iter_traffic(self) -> AsyncIterator[Dict[str, Any]]:
        """Stream current traffic stats.

        GET /traffic
        Yields dicts like {"up": <bytes>, "down": <bytes>} every second.
        Docs: https://clash.gitbook.io/doc/restful-api/common
        """
        async with self._client.stream("GET", "/traffic") as response:
            response.raise_for_status()
            # One JSON object per line
            async for line in response.aiter_lines():
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    # Some implementations may stream without newlines; fallback to chunks
                    try:
                        yield json.loads(line.strip())
                    except json.JSONDecodeError:
                        continue

    async def iter_logs(self, level: Optional[str] = None) -> AsyncIterator[Dict[str, Any]]:
        """Stream realtime logs.

        GET /logs?level={error|warning|info|debug}
        Yields dicts like {"type": "info", "payload": "..."}
        Docs: https://clash.gitbook.io/doc/restful-api/common
        """
        params: Dict[str, Any] = {}
        if level:
            params["level"] = level

        async with self._client.stream("GET", "/logs", params=params) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    try:
                        yield json.loads(line.strip())
                    except json.JSONDecodeError:
                        continue

    # ---------- Proxies ----------
    async def get_proxies(self) -> Dict[str, Any]:
        """Get all proxies.

        GET /proxies
        Docs: https://clash.gitbook.io/doc/restful-api/proxies
        """
        response = await self._client.get("/proxies")
        response.raise_for_status()
        return response.json()

    async def get_proxy(self, name: str) -> Dict[str, Any]:
        """Get single proxy info by name (case-sensitive).

        GET /proxies/:name
        Docs: https://clash.gitbook.io/doc/restful-api/proxies
        """
        response = await self._client.get(f"/proxies/{name}")
        response.raise_for_status()
        return response.json()

    async def get_proxy_delay(self, name: str, url: str, timeout_ms: int) -> Dict[str, Any]:
        """Get proxy delay test result.

        GET /proxies/:name/delay?url=...&timeout=...
        Docs: https://clash.gitbook.io/doc/restful-api/proxies
        """
        params = {"url": url, "timeout": timeout_ms}
        response = await self._client.get(f"/proxies/{name}/delay", params=params)
        response.raise_for_status()
        return response.json()

    async def select_proxy(self, selector_name: str, proxy_name: str) -> None:
        """Switch selected proxy of a Selector.

        PUT /proxies/:name with body {"name": "<proxy>"}
        Returns 204 No Content on success.
        Docs: https://clash.gitbook.io/doc/restful-api/proxies
        """
        response = await self._client.put(
            f"/proxies/{selector_name}", json={"name": proxy_name}
        )
        # Some implementations may return 204 (expected) or 200 with body
        if response.status_code not in (200, 204):
            response.raise_for_status()

    # ---------- Config ----------
    async def get_configs(self) -> Dict[str, Any]:
        """Get current base settings.

        GET /configs
        Docs: https://clash.gitbook.io/doc/restful-api/config
        """
        response = await self._client.get("/configs")
        response.raise_for_status()
        return response.json()

    async def patch_configs(self, config_update: Dict[str, Any]) -> None:
        """Incrementally update configs.

        PATCH /configs with partial fields like:
          port, socks-port, redir-port, allow-lan, mode, log-level
        Returns 204 No Content.
        Docs: https://clash.gitbook.io/doc/restful-api/config
        """
        response = await self._client.patch("/configs", json=config_update)
        if response.status_code != 204:
            response.raise_for_status()

    async def reload_configs(self, path: str, *, force: Optional[bool] = None) -> Optional[Dict[str, Any]]:
        """Reload YAML config file.

        PUT /configs?force=true|false with body {"path": "<absolute_path>"}
        Docs: https://clash.gitbook.io/doc/restful-api/config
        """
        params: Dict[str, Any] = {}
        if force is not None:
            params["force"] = str(force).lower()

        response = await self._client.put("/configs", params=params, json={"path": path})
        # Some implementations return 200 with JSON; others may be empty
        if response.status_code >= 400:
            response.raise_for_status()
        try:
            return response.json()
        except json.JSONDecodeError:
            return None

    async def get_rules(self) -> Dict[str, Any]:
        """Get all parsed rules.

        GET /rules
        Docs: https://clash.gitbook.io/doc/restful-api/config
        """
        response = await self._client.get("/rules")
        response.raise_for_status()
        return response.json()


__all__ = [
    "ClashClient",
]



if __name__ == "__main__":
    import asyncio
    import json
    from clash_api import ClashClient

    async def main():
        async with ClashClient.from_external_controller("127.0.0.1:9097", secret='set-your-secret') as client:
            proxies = await client.get_proxy('CherryPick')
            with open('proxy.json', 'w', encoding='utf-8') as f:
                json.dump(proxies, f, indent=2, ensure_ascii=False)

    asyncio.run(main())
