from __future__ import annotations

from typing import Optional
import httpx
import re

from .base import ServiceChecker, ServiceCheckResult, ServiceHostPatterns
from .common import create_http_client, country_code_to_emoji, TestResultItem


def parse_tiktok_region(body: str) -> Optional[str]:
    if not body:
        return None
    match = re.search(r'"region"\s*:\s*"([A-Za-z]{2})"', body)
    if match:
        return match.group(1).upper()
    return None


async def check_tiktok(proxy: Optional[str] = None) -> TestResultItem:
    # 1. First attempt: No follow redirects, standard browser UA
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.87 Safari/537.36"
    }
    
    async with create_http_client(proxy) as client:
        try:
            response = await client.get(
                "https://www.tiktok.com/",
                headers=headers,
                follow_redirects=False,
            )
            region = parse_tiktok_region(response.text)
            if region:
                emoji = country_code_to_emoji(region)
                return TestResultItem("TikTok", "Yes", region=f"{emoji}{region}")
        except httpx.RequestError:
            pass

        # 2. Second attempt: Follow redirects and use detailed browser headers
        headers_idc = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.87 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
            "Accept-Language": "en",
        }
        try:
            response = await client.get(
                "https://www.tiktok.com/",
                headers=headers_idc,
                follow_redirects=True,
            )
            region = parse_tiktok_region(response.text)
            if region:
                emoji = country_code_to_emoji(region)
                return TestResultItem("TikTok", "Yes", region=f"{emoji}{region} (Possible IDC)")

            if response.status_code < 400:
                return TestResultItem("TikTok", "No")
            else:
                return TestResultItem("TikTok", f"Failed (HTTP {response.status_code})")
        except httpx.RequestError as e:
            return TestResultItem("TikTok", f"Failed (Network: {str(e)[:50]})")


class TikTokChecker(ServiceChecker):
    service_name = "tiktok"
    host_patterns = ServiceHostPatterns(
        trigger_hosts=("tiktok.com", "tiktokv.com", "byteoversea.com"),
        extra_connection_hosts=("tiktok.com", "tiktokv.com", "byteoversea.com", "ibyteimg.com", "ibytedtos.com", "muscdn.com"),
        active_connection_hosts=("tiktokv.com", "byteoversea.com"),
    )

    async def check(self, proxy: Optional[str] = None) -> ServiceCheckResult:
        return await check_tiktok(proxy)
