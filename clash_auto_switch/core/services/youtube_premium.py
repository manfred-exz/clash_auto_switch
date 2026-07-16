from __future__ import annotations

from typing import Optional

import httpx
import re

from .base import ServiceChecker, ServiceCheckResult, ServiceHostPatterns
from .common import create_http_client, country_code_to_emoji, normalize_response_text, TestResultItem


def parse_youtube_premium_page(body: str) -> tuple[str, Optional[str]]:
    if not body:
        return "Failed", None

    normalized_body = normalize_response_text(body)
    body_lower = normalized_body.lower()
    if "youtube premium is not available in your country" in body_lower:
        return "No", None

    region = None
    for pattern in (
        r'id="country-code"[^>]*>([^<]+)<',
        r'"GL":"([A-Z]{2})"',
        r'"countryCode":"([A-Z]{2})"',
        r'"regionCode":"([A-Z]{2})"',
    ):
        match = re.search(pattern, normalized_body)
        if match:
            country_code = match.group(1).strip().upper()
            if len(country_code) == 2:
                region = f"{country_code_to_emoji(country_code)}{country_code}"
                break

    if any(
        indicator in body_lower
        for indicator in (
            "ad-free",
            "youtube premium",
            "innertube_api_key",
            "ytcfg.set",
        )
    ):
        return "Yes", region

    return "Failed (Unexpected Page)", region


async def check_youtube_premium(proxy: Optional[str] = None) -> TestResultItem:
    url = "https://www.youtube.com/premium"
    async with create_http_client(proxy) as client:
        status = "Failed"
        region = None
        try:
            response = await client.get(url)
            response.raise_for_status()

            # 确保完整读取响应体
            body = response.text
            if not body:
                return TestResultItem("Youtube Premium", "Failed", region=None)

            status, region = parse_youtube_premium_page(body)
        except httpx.HTTPStatusError as e:
            status = f"Failed (HTTP {e.response.status_code})"
        except httpx.RequestError as e:
            status = f"Failed (Network: {str(e)[:50]})"
        except Exception as e:
            status = f"Failed (Error: {str(e)[:50]})"

    return TestResultItem("Youtube Premium", status, region=region)



class YouTubePremiumChecker(ServiceChecker):
    service_name = "youtube_premium"
    host_patterns = ServiceHostPatterns(
        trigger_hosts=("youtube.com", "googlevideo.com", "ytimg.com"),
        extra_connection_hosts=("youtube.com", "googlevideo.com", "ytimg.com"),
        active_connection_hosts=("googlevideo.com",),
    )

    async def check(self, proxy: Optional[str] = None) -> ServiceCheckResult:
        return await check_youtube_premium(proxy)
