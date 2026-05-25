from __future__ import annotations

from typing import Optional

import httpx
import re

from .base import ServiceChecker, ServiceCheckResult, ServiceHostPatterns
from .common import create_http_client, country_code_to_emoji, TestResultItem


async def check_prime_video(proxy: Optional[str] = None) -> TestResultItem:
    url = "https://www.primevideo.com"
    async with create_http_client(proxy) as client:
        try:
            response = await client.get(url)
            response.raise_for_status()

            # 确保完整读取响应
            body = response.text
            if not body:
                return TestResultItem("Prime Video", "Failed (Empty Response)")

            if "isServiceRestricted" in body:
                return TestResultItem("Prime Video", "No (Service Not Available)")

            match_region = re.search(r'"currentTerritory":"([^"]+)"', body)
            if match_region:
                region = match_region.group(1)
                emoji = country_code_to_emoji(region)
                return TestResultItem("Prime Video", "Yes", region=f"{emoji}{region}")

            return TestResultItem("Prime Video", "Failed (Error: PAGE ERROR)")

        except httpx.HTTPStatusError as e:
            return TestResultItem("Prime Video", f"Failed (HTTP {e.response.status_code})")
        except httpx.RequestError as e:
            return TestResultItem("Prime Video", f"Failed (Network: {str(e)[:50]})")



class PrimeVideoChecker(ServiceChecker):
    service_name = "prime_video"
    host_patterns = ServiceHostPatterns(
        trigger_hosts=("primevideo.com", "amazonvideo.com"),
        extra_connection_hosts=("primevideo.com", "amazonvideo.com", "media-amazon.com"),
    )

    async def check(self, proxy: Optional[str] = None) -> ServiceCheckResult:
        return await check_prime_video(proxy)
