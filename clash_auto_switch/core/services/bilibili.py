from __future__ import annotations

from typing import Optional

import httpx

from .base import ServiceChecker, ServiceCheckResult, ServiceHostPatterns
from .common import create_http_client, TestResultItem


class BilibiliMainlandChecker(ServiceChecker):
    service_name = "bilibili_mainland"
    host_patterns = ServiceHostPatterns(
        trigger_hosts=("bilibili.com", "bilibili.tv"),
        extra_connection_hosts=("bilibili.com", "hdslb.com", "bilivideo.com"),
        active_connection_hosts=("bilivideo.com", "hdslb.com"),
    )

    async def check(self, proxy: Optional[str] = None) -> ServiceCheckResult:
        return await check_bilibili_china_mainland(proxy)


class BilibiliHkMcTwChecker(ServiceChecker):
    service_name = "bilibili_hk_mc_tw"
    host_patterns = ServiceHostPatterns(
        trigger_hosts=("bilibili.com", "bilibili.tv"),
        extra_connection_hosts=("bilibili.com", "hdslb.com", "bilivideo.com"),
        active_connection_hosts=("bilivideo.com", "hdslb.com"),
    )

    async def check(self, proxy: Optional[str] = None) -> ServiceCheckResult:
        return await check_bilibili_hk_mc_tw(proxy)


async def check_bilibili_china_mainland(proxy: Optional[str] = None) -> TestResultItem:
    url = "https://api.bilibili.com/pgc/player/web/playurl?avid=82846771&qn=0&type=&otype=json&ep_id=307247&fourk=1&fnver=0&fnval=16&module=bangumi"
    async with create_http_client(proxy) as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
            body = response.json()
            code = body.get("code")
            if code == 0:
                status = "Yes"
            elif code == -10403:
                status = "No"
            else:
                status = "Failed"
        except (httpx.RequestError, httpx.HTTPStatusError, ValueError):
            status = "Failed"

    return TestResultItem("哔哩哔哩大陆", status)


async def check_bilibili_hk_mc_tw(proxy: Optional[str] = None) -> TestResultItem:
    url = "https://api.bilibili.com/pgc/player/web/playurl?avid=18281381&cid=29892777&qn=0&type=&otype=json&ep_id=183799&fourk=1&fnver=0&fnval=16&module=bangumi"
    async with create_http_client(proxy) as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
            body = response.json()
            code = body.get("code")
            if code == 0:
                status = "Yes"
            elif code == -10403:
                status = "No"
            else:
                status = "Failed"
        except (httpx.RequestError, httpx.HTTPStatusError, ValueError):
            status = "Failed"

    return TestResultItem("哔哩哔哩港澳台", status)
