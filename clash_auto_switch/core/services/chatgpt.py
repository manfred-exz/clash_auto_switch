from __future__ import annotations

from typing import Optional

import httpx

from .base import ServiceChecker, ServiceCheckResult, ServiceHostPatterns
from .common import create_http_client, country_code_to_emoji, TestResultItem


async def check_chatgpt(proxy: Optional[str] = None) -> TestResultItem:
    async with create_http_client(proxy) as client:
        region = None

        # 1. 获取国家代码
        try:
            response_country = await client.get("https://chat.openai.com/cdn-cgi/trace")
            if response_country.status_code == 200:
                trace_data = {line.split('=')[0]: line.split('=')[1] for line in response_country.text.splitlines() if '=' in line}
                loc = trace_data.get("loc")
                if loc:
                    emoji = country_code_to_emoji(loc)
                    region = f"{emoji}{loc}"
        except httpx.RequestError:
            pass

        # 2. 测试 ChatGPT Web
        web_status = "Failed"
        try:
            response_web = await client.get("https://api.openai.com/compliance/cookie_requirements")
            response_web.raise_for_status()
            body_lower = response_web.text.lower()
            if "unsupported_country" in body_lower:
                web_status = "Unsupported Country/Region"
            else:
                web_status = "Yes"
        except (httpx.RequestError, httpx.HTTPStatusError):
            pass

    return TestResultItem("ChatGPT Web", web_status, region=region)



class ChatGPTChecker(ServiceChecker):
    service_name = "chatgpt"
    display_name = "ChatGPT Web"
    host_patterns = ServiceHostPatterns(
        trigger_hosts=("chat.openai.com", "chatgpt.com", "api.openai.com"),
        extra_connection_hosts=("openai.com", "chatgpt.com", "oaistatic.com", "oaiusercontent.com"),
    )

    async def check(self, proxy: Optional[str] = None) -> ServiceCheckResult:
        return await check_chatgpt(proxy)
