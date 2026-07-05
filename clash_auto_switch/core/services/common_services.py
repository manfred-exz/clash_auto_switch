from __future__ import annotations

from typing import Optional

import httpx

from .base import ServiceChecker, ServiceCheckResult, ServiceHostPatterns
from .common import TestResultItem, create_http_client


COMMON_SERVICE_ENDPOINTS = (
    ("GitHub", "https://github.com/favicon.ico", {200}),
    ("Google", "https://www.google.com/generate_204", {204}),
)


async def check_common_services(proxy: Optional[str] = None) -> TestResultItem:
    failed_services: list[str] = []

    async with create_http_client(proxy) as client:
        for service_label, url, expected_statuses in COMMON_SERVICE_ENDPOINTS:
            try:
                response = await client.get(url)
            except httpx.RequestError:
                failed_services.append(service_label)
                continue

            if response.status_code not in expected_statuses:
                failed_services.append(f"{service_label} HTTP {response.status_code}")

    if failed_services:
        status = f"Failed ({', '.join(failed_services)})"
    else:
        status = "Yes"

    return TestResultItem("Common Services", status)


class CommonServicesChecker(ServiceChecker):
    service_name = "common_services"
    trigger_mode = "periodic"
    close_connections_on_switch = False
    periodic_interval_sec = 60.0
    host_patterns = ServiceHostPatterns(
        trigger_hosts=(),
        extra_connection_hosts=("github.com", "githubusercontent.com", "google.com", "gstatic.com"),
    )

    async def check(self, proxy: Optional[str] = None) -> ServiceCheckResult:
        return await check_common_services(proxy)
