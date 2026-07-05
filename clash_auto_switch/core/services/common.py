from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import html
import re
from dataclasses import dataclass
from datetime import datetime
import httpx
from typing import TYPE_CHECKING, Callable, Dict, Iterator, Optional

if TYPE_CHECKING:
    from clash_auto_switch.core.clash_api import ClashApi

ServiceDebugEventFunc = Callable[[str, str], None]
_SERVICE_DEBUG_EVENT_HANDLER: ContextVar[Optional[ServiceDebugEventFunc]] = ContextVar(
    "service_debug_event_handler",
    default=None,
)


@contextmanager
def service_debug_event_handler(handler: Optional[ServiceDebugEventFunc]) -> Iterator[None]:
    token = _SERVICE_DEBUG_EVENT_HANDLER.set(handler)
    try:
        yield
    finally:
        _SERVICE_DEBUG_EVENT_HANDLER.reset(token)


# 定义解锁测试项目的结构
@dataclass
class TestResultItem:
    name: str
    status: str
    region: Optional[str] = None
    check_time: Optional[str] = None

    def __post_init__(self) -> None:
        if self.check_time is None:
            self.check_time = get_local_date_string()

    def to_dict(self) -> Dict[str, Optional[str]]:
        return {
            "name": self.name,
            "status": self.status,
            "region": self.region,
            "check_time": self.check_time,
        }

# 获取当前本地时间字符串
def get_local_date_string() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# 将国家代码转换为对应的emoji
def country_code_to_emoji(country_code: str) -> str:
    country_code = country_code.upper()
    if len(country_code) < 2:
        return ""

    c1 = 0x1F1E6 + ord(country_code[0]) - ord('A')
    c2 = 0x1F1E6 + ord(country_code[1]) - ord('A')

    return chr(c1) + chr(c2)

# 创建新的HTTP客户端
def create_http_client(proxy: Optional[str] = None, custom_headers: Optional[Dict[str, str]] = None) -> httpx.AsyncClient:
    default_headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    if custom_headers:
        default_headers.update(custom_headers)

    return httpx.AsyncClient(
        proxy=proxy,
        headers=default_headers,
        timeout=30.0,
        verify=False,
        http2=True
    )


def format_result_status(result: TestResultItem) -> str:
    region = f" ({result.region})" if result.region else ""
    return f"{result.name}: {result.status}{region}"


CONNECTIVITY_MAX_ATTEMPTS = 3
CONNECTIVITY_RETRY_DELAY_SEC = 1.0


async def check_proxy_connectivity(
    clash: ClashApi,
    node_name: Optional[str],
    url: str = "https://cp.cloudflare.com/generate_204",
    timeout_ms: int = 5000,
    max_attempts: int = CONNECTIVITY_MAX_ATTEMPTS,
) -> tuple[bool, str]:
    """Check a specific node's connectivity via Clash's get_proxy_delay.

    Unlike probing through the local HTTP proxy (which follows Clash's routing
    rules and may hit a different node), this targets the named node directly so
    the result reflects the node actually being tested. Retries on failure to
    avoid switching away from nodes with transient connectivity blips.
    """
    if not node_name:
        return False, "connectivity failed: no node selected"

    last_message = "connectivity failed: no attempts"
    for attempt in range(1, max_attempts + 1):
        try:
            result = await clash.get_proxy_delay(node_name, url, timeout_ms)
        except httpx.HTTPError as exc:
            last_message = f"connectivity failed: {str(exc)[:80]}"
        else:
            delay = result.get("delay")
            if isinstance(delay, (int, float)) and delay >= 0:
                suffix = f" (尝试 {attempt}/{max_attempts})" if attempt > 1 else ""
                return True, f"connectivity ok: delay {delay}ms{suffix}"
            last_message = f"connectivity failed: {result.get('message') or result}"

        if attempt < max_attempts:
            await asyncio.sleep(CONNECTIVITY_RETRY_DELAY_SEC)

    return False, f"{last_message} (尝试 {max_attempts}/{max_attempts})"


def parse_trace_country(body: str) -> Optional[str]:
    for line in body.splitlines():
        if line.startswith("loc="):
            country_code = line.removeprefix("loc=").strip().upper()
            return country_code or None
    return None


def normalize_response_text(body: str) -> str:
    """Normalize escaped page text before keyword matching."""
    body = html.unescape(body)
    return re.sub(
        r"\\u([0-9a-fA-F]{4})",
        lambda match: chr(int(match.group(1), 16)),
        body,
    )
