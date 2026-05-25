from __future__ import annotations

from typing import Optional

import httpx
import re

from .base import ServiceChecker, ServiceCheckResult, ServiceHostPatterns
from .common import create_http_client, country_code_to_emoji, TestResultItem


async def check_gemini(proxy: Optional[str] = None) -> TestResultItem:
    url = "https://gemini.google.com"
    async with create_http_client(proxy) as client:
        status = "Failed"
        region = None
        try:
            # 等待完整响应，类似Rust版本的 response.text().await
            response = await client.get(url)
            response.raise_for_status()  # 检查HTTP状态码

            # 确保完整读取响应体
            body = response.text
            if not body:
                return TestResultItem("Gemini", "Failed", region=None)

            # 检查是否包含成功标识
            is_ok = "45631641,null,true" in body
            status = "Yes" if is_ok else "No"

            # 尝试提取国家代码
            match = re.search(r',2,1,200,"([A-Z]{3})"', body)
            if match:
                country_code = match.group(1)
                emoji = country_code_to_emoji(country_code)
                region = f"{emoji}{country_code}"

        except httpx.HTTPStatusError as e:
            status = f"Failed (HTTP {e.response.status_code})"
        except httpx.RequestError as e:
            status = f"Failed (Network: {str(e)[:50]})"
        except Exception as e:
            status = f"Failed (Error: {str(e)[:50]})"

    return TestResultItem("Gemini", status, region=region)



class GeminiChecker(ServiceChecker):
    service_name = "gemini"
    display_name = "Gemini"
    host_patterns = ServiceHostPatterns(
        trigger_hosts=("gemini.google.com",),
        extra_connection_hosts=("gemini.google.com", "google.com", "gstatic.com"),
    )

    async def check(self, proxy: Optional[str] = None) -> ServiceCheckResult:
        return await check_gemini(proxy)
