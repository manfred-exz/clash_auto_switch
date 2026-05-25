from __future__ import annotations

from typing import Optional

import httpx
import re

from .base import ServiceChecker, ServiceCheckResult, ServiceHostPatterns
from .common import create_http_client, country_code_to_emoji, TestResultItem


async def check_disney_plus(proxy: Optional[str] = None) -> TestResultItem:
    auth_header = "Bearer ZGlzbmV5JmJyb3dzZXImMS4wLjA.Cu56AgSfBTDag5NiRA81oLHkDZfu5L3CKadnefEAY84"
    async with create_http_client(proxy) as client:
        try:
            # Step 1: Get assertion
            device_api_url = "https://disney.api.edge.bamgrid.com/devices"
            device_req_body = {
                "deviceFamily": "browser",
                "applicationRuntime": "chrome",
                "deviceProfile": "windows",
                "attributes": {}
            }
            res_device = await client.post(device_api_url, json=device_req_body, headers={"authorization": auth_header})

            if res_device.status_code == 403:
                return TestResultItem("Disney+", "No (IP Banned By Disney+)")
            res_device.raise_for_status()

            device_body = res_device.json()
            assertion = device_body.get("assertion")
            if not assertion:
                return TestResultItem("Disney+", "Failed (Cannot extract assertion)")

            # Step 2: Get token
            token_url = "https://disney.api.edge.bamgrid.com/token"
            token_body = {
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "latitude": "0",
                "longitude": "0",
                "platform": "browser",
                "subject_token": assertion,
                "subject_token_type": "urn:bamtech:params:oauth:token-type:device",
            }
            res_token = await client.post(token_url, data=token_body, headers={"authorization": auth_header})

            # 确保完整读取token响应
            token_text = res_token.text
            if "forbidden-location" in token_text or "403 ERROR" in token_text:
                 return TestResultItem("Disney+", "No (IP Banned By Disney+)")

            res_token.raise_for_status()
            token_json = res_token.json()
            refresh_token = token_json.get("refresh_token")
            if not refresh_token:
                return TestResultItem("Disney+", f"Failed (Cannot extract refresh token, status: {res_token.status_code})")

            # Step 3: GraphQL for region info
            graphql_url = "https://disney.api.edge.bamgrid.com/graph/v1/device/graphql"
            graphql_payload = {
                "query": "mutation refreshToken($input: RefreshTokenInput!) { refreshToken(refreshToken: $input) { activeSession { sessionId } } }",
                "variables": {"input": {"refreshToken": refresh_token}}
            }
            res_graphql = await client.post(graphql_url, json=graphql_payload, headers={"authorization": auth_header})

            # 确保完整读取GraphQL响应
            graphql_body_text = res_graphql.text

            if res_graphql.status_code >= 400:
                 # Fallback to main page check
                try:
                    res_main = await client.get("https://www.disneyplus.com/")
                    res_main.raise_for_status()
                    body_main = res_main.text
                    match_main = re.search(r'"region"\s*:\s*"([^"]+)"', body_main)
                    if match_main:
                        region = match_main.group(1)
                        emoji = country_code_to_emoji(region)
                        return TestResultItem("Disney+", "Yes", region=f"{emoji}{region} (from main page)")
                except (httpx.RequestError, httpx.HTTPStatusError, ValueError):
                    pass
                return TestResultItem("Disney+", f"Failed (GraphQL error: {res_graphql.status_code})")

            match_country = re.search(r'"countryCode"\s*:\s*"([^"]+)"', graphql_body_text)
            region = match_country.group(1) if match_country else None

            if not region:
                return TestResultItem("Disney+", "No")

            if region == "JP":
                emoji = country_code_to_emoji("JP")
                return TestResultItem("Disney+", "Yes", region=f"{emoji}JP")

            match_supported = re.search(r'"inSupportedLocation"\s*:\s*(true|false)', graphql_body_text)
            in_supported_location = match_supported and match_supported.group(1) == "true"

            res_preview = await client.get("https://disneyplus.com")
            is_unavailable = "preview" in str(res_preview.url) or "unavailable" in str(res_preview.url)

            if is_unavailable:
                return TestResultItem("Disney+", "No")

            emoji = country_code_to_emoji(region)
            if in_supported_location:
                return TestResultItem("Disney+", "Yes", region=f"{emoji}{region}")
            else:
                return TestResultItem("Disney+", "Soon", region=f"{emoji}{region}（即将上线）")

        except (httpx.RequestError, httpx.HTTPStatusError, ValueError, KeyError) as e:
            return TestResultItem("Disney+", f"Failed (Error: {e})")



class DisneyPlusChecker(ServiceChecker):
    service_name = "disney_plus"
    display_name = "Disney+"
    host_patterns = ServiceHostPatterns(
        trigger_hosts=("disneyplus.com", "bamgrid.com"),
        extra_connection_hosts=("disneyplus.com", "bamgrid.com", "disney.api.edge.bamgrid.com"),
    )

    async def check(self, proxy: Optional[str] = None) -> ServiceCheckResult:
        return await check_disney_plus(proxy)
