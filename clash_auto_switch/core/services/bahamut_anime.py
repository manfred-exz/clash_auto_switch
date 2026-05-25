from __future__ import annotations

from typing import Optional

import httpx
import re

from .base import ServiceChecker, ServiceCheckResult, ServiceHostPatterns
from .common import create_http_client, country_code_to_emoji, TestResultItem


async def check_bahamut_anime(proxy: Optional[str] = None) -> TestResultItem:
    status = "Failed"
    region = None
    try:
        # 使用独立的带Windows User-Agent的客户端
        custom_headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"}
        async with create_http_client(proxy, custom_headers) as anime_client:
            # 第一步：获取设备ID
            device_url = "https://ani.gamer.com.tw/ajax/getdeviceid.php"
            device_id_res = await anime_client.get(device_url)
            device_id_res.raise_for_status()
            device_id_json = device_id_res.json()
            device_id = device_id_json.get("deviceid")

            if not device_id:
                return TestResultItem("Bahamut Anime", "Failed")

            # 第二步：使用设备ID检查访问权限
            token_url = f"https://ani.gamer.com.tw/ajax/token.php?adID=89422&sn=37783&device={device_id}"
            token_res = await anime_client.get(token_url)
            token_res.raise_for_status()

            # 确保完整读取响应
            token_body = token_res.text
            if "animeSn" not in token_body:
                return TestResultItem("Bahamut Anime", "No")

            # 第三步：访问主页获取区域信息
            main_page_res = await anime_client.get("https://ani.gamer.com.tw/")
            main_page_res.raise_for_status()
            body = main_page_res.text
            match = re.search(r'data-geo="([^"]+)"', body)
            if match:
                country_code = match.group(1)
                emoji = country_code_to_emoji(country_code)
                region = f"{emoji}{country_code}"

            status = "Yes"

    except (httpx.RequestError, httpx.HTTPStatusError, ValueError):
        status = "Failed"

    return TestResultItem("Bahamut Anime", status, region=region)



class BahamutAnimeChecker(ServiceChecker):
    service_name = "bahamut_anime"
    host_patterns = ServiceHostPatterns(trigger_hosts=("ani.gamer.com.tw", "gamer.com.tw"))

    async def check(self, proxy: Optional[str] = None) -> ServiceCheckResult:
        return await check_bahamut_anime(proxy)
