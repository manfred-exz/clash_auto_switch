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
import re
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, Optional

import httpx


_LOG_PATTERN = re.compile(
    r"^\[(?P<network>[^\]]+)\]\s+"
    r"(?:(?:dial\s+(?P<dial_outbound>.+?)\s+\(match\s+(?P<dial_rule>.+?)\)\s+))?"
    r"(?P<source>.+?)\s+-->\s+(?P<destination>.+?)"
    r"(?:\s+match\s+(?P<rule>.+?)\s+using\s+(?P<outbound>.+)|\s+error:\s+(?P<error>.+))$"
)


@dataclass(frozen=True)
class ClashEndpoint:
    """One side of a Clash connection log entry."""

    host: str
    port: Optional[int] = None
    process: Optional[str] = None


@dataclass(frozen=True)
class ClashRuleMatch:
    """Rule information extracted from a Clash log line."""

    raw: str
    rule_type: str
    rule_value: Optional[str] = None


@dataclass(frozen=True)
class ClashOutbound:
    """Outbound policy or selected node extracted from a Clash log line."""

    raw: str
    policy: str
    selected: Optional[str] = None


@dataclass(frozen=True)
class ClashLogEntry:
    """Structured Clash log entry.

    Supports both successful connection logs:
      [TCP] src:port(process) --> dst:port match GeoSite(google) using Google[node]

    and failed dial logs:
      [TCP] dial DIRECT (match GeoIP/cn) src:port(process) --> dst:port error: ...
    """

    type: str
    payload: str
    network: Optional[str] = None
    source: Optional[ClashEndpoint] = None
    destination: Optional[ClashEndpoint] = None
    rule: Optional[ClashRuleMatch] = None
    outbound: Optional[ClashOutbound] = None
    error: Optional[str] = None

    @property
    def is_connection_log(self) -> bool:
        return self.source is not None and self.destination is not None

    @property
    def is_error(self) -> bool:
        return self.error is not None

    @classmethod
    def from_api_item(cls, item: Dict[str, Any]) -> "ClashLogEntry":
        log_type = str(item.get("type", ""))
        payload = str(item.get("payload", ""))
        parsed = parse_clash_log_payload(payload)
        return cls(type=log_type, payload=payload, **parsed)


def parse_clash_endpoint(value: str) -> ClashEndpoint:
    """Parse host:port(process) endpoint text from Clash logs."""

    endpoint_text = value.strip()
    process: Optional[str] = None
    process_match = re.match(r"^(?P<address>.+)\((?P<process>[^()]*)\)$", endpoint_text)
    if process_match:
        endpoint_text = process_match.group("address").strip()
        process = process_match.group("process") or None

    if endpoint_text.startswith("["):
        address_match = re.match(r"^\[(?P<host>[^\]]+)\](?::(?P<port>\d+))?$", endpoint_text)
        if address_match:
            port = address_match.group("port")
            return ClashEndpoint(
                host=address_match.group("host"),
                port=int(port) if port is not None else None,
                process=process,
            )

    host = endpoint_text
    port: Optional[int] = None
    if ":" in endpoint_text:
        possible_host, possible_port = endpoint_text.rsplit(":", 1)
        if possible_port.isdigit():
            host = possible_host
            port = int(possible_port)

    return ClashEndpoint(host=host, port=port, process=process)


def parse_clash_rule(value: str) -> ClashRuleMatch:
    """Parse rule text like GeoSite(google), GeoIP/cn, or Match."""

    raw = value.strip()
    function_match = re.match(r"^(?P<type>[^()]+)\((?P<value>.*)\)$", raw)
    if function_match:
        return ClashRuleMatch(
            raw=raw,
            rule_type=function_match.group("type"),
            rule_value=function_match.group("value") or None,
        )

    if "/" in raw:
        rule_type, rule_value = raw.split("/", 1)
        return ClashRuleMatch(raw=raw, rule_type=rule_type, rule_value=rule_value or None)

    return ClashRuleMatch(raw=raw, rule_type=raw)


def parse_clash_outbound(value: str) -> ClashOutbound:
    """Parse outbound text like DIRECT or Google[yushe | 狮城 02]."""

    raw = value.strip()
    selected_match = re.match(r"^(?P<policy>.+?)\[(?P<selected>.*)\]$", raw)
    if selected_match:
        return ClashOutbound(
            raw=raw,
            policy=selected_match.group("policy"),
            selected=selected_match.group("selected") or None,
        )

    return ClashOutbound(raw=raw, policy=raw)


def parse_clash_log_payload(payload: str) -> Dict[str, Any]:
    """Parse a Clash log payload into dataclass-ready fields.

    Unknown payload formats are kept as raw payload by returning empty fields.
    """

    match = _LOG_PATTERN.match(payload.strip())
    if not match:
        return {}

    rule_text = match.group("rule") or match.group("dial_rule")
    outbound_text = match.group("outbound") or match.group("dial_outbound")

    return {
        "network": match.group("network"),
        "source": parse_clash_endpoint(match.group("source")),
        "destination": parse_clash_endpoint(match.group("destination")),
        "rule": parse_clash_rule(rule_text) if rule_text else None,
        "outbound": parse_clash_outbound(outbound_text) if outbound_text else None,
        "error": match.group("error"),
    }


class ClashClientRaw:
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

    async def __aenter__(self) -> "ClashClientRaw":  # noqa: D401
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
    ) -> "ClashClientRaw":
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

    async def iter_logs(self, level: Optional[str] = None) -> AsyncIterator[ClashLogEntry]:
        """Stream realtime logs.

        GET /logs?level={error|warning|info|debug}
        Yields structured ClashLogEntry objects parsed from dicts like
        {"type": "info", "payload": "..."}.
        Docs: https://clash.gitbook.io/doc/restful-api/common
        """
        params: Dict[str, Any] = {}
        if level:
            params["level"] = level

        async with self._client.stream("GET", "/logs", params=params, timeout=None) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line:
                    continue
                try:
                    yield ClashLogEntry.from_api_item(json.loads(line))
                except json.JSONDecodeError:
                    try:
                        yield ClashLogEntry.from_api_item(json.loads(line.strip()))
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

    # ---------- Connections ----------
    async def get_connections(self) -> Dict[str, Any]:
        """Get active connections.

        GET /connections
        """
        response = await self._client.get("/connections")
        response.raise_for_status()
        return response.json()

    async def close_connection(self, connection_id: str) -> None:
        """Close one active connection by id.

        DELETE /connections/:id
        """
        response = await self._client.delete(f"/connections/{connection_id}")
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

    async def get_default_proxy_group(self) -> Optional[str]:
        rules = await self.get_rules()
        if not rules:
            return None

        last_rule = rules[-1]
        if last_rule['type'].lower() == 'match':
            return last_rule['proxy']
        else:
            return None


__all__ = [
    "ClashEndpoint",
    "ClashRuleMatch",
    "ClashOutbound",
    "ClashLogEntry",
    "ClashClientRaw",
    "parse_clash_endpoint",
    "parse_clash_rule",
    "parse_clash_outbound",
    "parse_clash_log_payload",
]



if __name__ == "__main__":
    import asyncio
    import json

    async def main():
        async with ClashClientRaw.from_external_controller("127.0.0.1:9097", secret='set-your-secret') as client:
            proxies = await client.get_proxy('Youtube')
            with open('proxy.json', 'w', encoding='utf-8') as f:
                json.dump(proxies, f, indent=2, ensure_ascii=False)

    asyncio.run(main())
