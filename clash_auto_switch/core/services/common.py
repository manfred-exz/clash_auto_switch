import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import html
import re
from dataclasses import dataclass
from datetime import datetime
import httpx
from typing import Callable, Dict, Iterator, Optional

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


async def check_proxy_connectivity(proxy: Optional[str] = None) -> tuple[bool, str]:
    """Check basic node connectivity before a service-specific probe."""
    async with create_http_client(proxy) as client:
        try:
            response = await client.get("https://cp.cloudflare.com/generate_204")
            if response.status_code in {204, 200}:
                return True, f"Cloudflare connectivity: HTTP {response.status_code}"
        except httpx.RequestError as exc:
            pass

        # retry
        await asyncio.sleep(1)

        try:
            response = await client.get("https://cp.cloudflare.com/generate_204")
            if response.status_code in {204, 200}:
                return True, f"Cloudflare connectivity: HTTP {response.status_code}"
            return False, f"Cloudflare connectivity failed: HTTP {response.status_code}"
        except httpx.RequestError as exc:
            return False, f"Cloudflare connectivity failed: {str(exc)[:80]}"


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
