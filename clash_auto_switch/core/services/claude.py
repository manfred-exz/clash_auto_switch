from __future__ import annotations

from typing import Optional

import httpx

from .base import ServiceChecker, ServiceCheckResult, ServiceHostPatterns
from .common import create_http_client, country_code_to_emoji, parse_trace_country, TestResultItem


CLAUDE_BLOCKED_CODES = {"AF", "BY", "CN", "CU", "HK", "IR", "KP", "MO", "RU", "SY"}


async def check_claude(proxy: Optional[str] = None) -> TestResultItem:
    url = "https://claude.ai/cdn-cgi/trace"
    async with create_http_client(proxy) as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
            country_code = parse_trace_country(response.text)
            if not country_code:
                return TestResultItem("Claude", "Failed")

            emoji = country_code_to_emoji(country_code)
            status = "No" if country_code in CLAUDE_BLOCKED_CODES else "Yes"
            return TestResultItem("Claude", status, region=f"{emoji}{country_code}")
        except (httpx.RequestError, httpx.HTTPStatusError):
            return TestResultItem("Claude", "Failed")



class ClaudeChecker(ServiceChecker):
    service_name = "claude"
    host_patterns = ServiceHostPatterns(trigger_hosts=("claude.ai", "anthropic.com"))

    async def check(self, proxy: Optional[str] = None) -> ServiceCheckResult:
        return await check_claude(proxy)
