from __future__ import annotations

import asyncio
from typing import Optional, Tuple

from clash_auto_switch.core.services.common import format_result_status
from clash_auto_switch.core.services.registry import SERVICE_CHECKERS


async def probe_service(
    service_name: str,
    proxy_url: Optional[str],
) -> Tuple[bool, str]:
    """Return (is_unlocked, human_status)."""

    checker = SERVICE_CHECKERS.get(service_name)
    if checker:
        result = await checker(proxy_url)
        return result.status == "Yes", format_result_status(result)

    return False, f"未知服务: {service_name}"


async def probe_service_multi(
    service_name: str,
    proxy_url: Optional[str],
    count: int = 3,
) -> Tuple[bool, str]:
    """Run the same service probe multiple times; any failure fails the probe."""

    status = ""
    for i in range(count):
        try:
            is_unlocked, status = await probe_service(service_name, proxy_url)
            if not is_unlocked:
                return False, f"第{i + 1}次检测失败: {status}"
            if i < count - 1:
                await asyncio.sleep(1.0)
        except Exception as e:
            return False, f"第{i + 1}次检测异常: {e}"

    return True, status
