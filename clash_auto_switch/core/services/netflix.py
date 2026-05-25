from __future__ import annotations

from typing import Optional

import httpx

from .base import ServiceChecker, ServiceCheckResult, ServiceHostPatterns
from .common import create_http_client, country_code_to_emoji, TestResultItem


async def check_netflix_cdn(proxy: Optional[str] = None) -> TestResultItem:
    url = "https://api.fast.com/netflix/speedtest/v2?https=true&token=YXNkZmFzZGxmbnNkYWZoYXNkZmhrYWxm&urlCount=5"
    async with create_http_client(proxy) as client:
        try:
            response = await client.get(url, timeout=30)
            if response.status_code == 403:
                return TestResultItem("Netflix", "No (IP Banned By Netflix)")

            response.raise_for_status()
            data = response.json()
            targets = data.get("targets", [])
            if targets:
                location = targets[0].get("location", {})
                country = location.get("country")
                if country:
                    emoji = country_code_to_emoji(country)
                    return TestResultItem("Netflix", "Yes", region=f"{emoji}{country}")

            return TestResultItem("Netflix", "Unknown")

        except (httpx.RequestError, httpx.HTTPStatusError, ValueError) as e:
            return TestResultItem("Netflix", f"Failed (CDN API: {e})")

# 测试 Netflix
async def check_netflix(proxy: Optional[str] = None) -> TestResultItem:
    cdn_result = await check_netflix_cdn(proxy)
    if cdn_result.status == "Yes":
        return cdn_result

    async with create_http_client(proxy) as client:
        url1 = "https://www.netflix.com/title/81280792"  # LEGO Ninjago
        url2 = "https://www.netflix.com/title/70143836"  # Breaking Bad

        try:
            res1 = await client.get(url1, timeout=30, follow_redirects=True)
            res2 = await client.get(url2, timeout=30, follow_redirects=True)

            status1 = res1.status_code
            status2 = res2.status_code

            if status1 == 404 and status2 == 404:
                return TestResultItem("Netflix", "Originals Only")

            if status1 == 403 or status2 == 403:
                 return TestResultItem("Netflix", "No")

            if status1 in [200, 301, 302] or status2 in [200, 301, 302]:
                test_url = "https://www.netflix.com/title/80018499"
                try:
                    test_res = await client.get(test_url, timeout=30, follow_redirects=False) # Do not follow redirects to get location
                    if 'location' in test_res.headers:
                        location_str = test_res.headers['location']
                        parts = location_str.split('/')
                        if len(parts) >= 4:
                            region_code = parts[3].split('-')[0]
                            emoji = country_code_to_emoji(region_code)
                            return TestResultItem("Netflix", "Yes", region=f"{emoji}{region_code.upper()}")
                except httpx.RequestError:
                     pass # Fallback to US

                emoji = country_code_to_emoji("us")
                return TestResultItem("Netflix", "Yes", region=f"{emoji}US")

            return TestResultItem("Netflix", f"Failed (Status: {status1}_{status2})")

        except httpx.RequestError as e:
            return TestResultItem("Netflix", f"Failed (Request Error: {e})")



class NetflixChecker(ServiceChecker):
    service_name = "netflix"
    display_name = "Netflix"
    host_patterns = ServiceHostPatterns(
        trigger_hosts=("netflix.com", "nflxvideo.net", "fast.com"),
        extra_connection_hosts=("netflix.com", "nflxvideo.net", "nflximg.net", "fast.com"),
    )

    async def check(self, proxy: Optional[str] = None) -> ServiceCheckResult:
        return await check_netflix(proxy)
