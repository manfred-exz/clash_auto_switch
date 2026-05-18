from typing import Awaitable, Callable, Optional

import httpx

from clash_auto_switch.clash_api import ClashClient
from clash_auto_switch.defs import ClashConfig, ProxyServicePair, ServiceRecord
from clash_auto_switch.service_tester import probe_service
from clash_auto_switch.storage import NodeHistoryStorage


ProbeFunc = Callable[[str, Optional[str]], Awaitable[tuple[bool, str]]]


def get_service_nodes_by_reliability(
    candidate_nodes: list[str],
    service_name: str,
    proxy_group_name: str,
    storage: NodeHistoryStorage,
) -> list[ServiceRecord]:
    records = [
        record
        for node in candidate_nodes
        for record in storage.get_records_by_node(node, proxy_group_name)
        if record.service_name == service_name
    ]

    return sorted(records, key=lambda x: x.reliability_score, reverse=True)


async def select_next_proxy_in_group(
    client: ClashClient,
    proxy_group_name: str,
    service_name: str,
    storage: NodeHistoryStorage,
) -> str:
    """Select the next eligible proxy in a group based on reliability scores."""
    group_info = await client.get_proxy(proxy_group_name)
    candidates = group_info.get("all") or []
    if not isinstance(candidates, list) or not candidates:
        raise ValueError(f"Proxy group '{proxy_group_name}' has no candidates in 'all'")

    current = group_info.get("now")

    alive_candidates = []
    for candidate in candidates:
        try:
            candidate_info = await client.get_proxy(candidate)
            if candidate_info.get("alive") is False:
                continue
            alive_candidates.append(candidate)
        except httpx.HTTPError:
            alive_candidates.append(candidate)

    if not alive_candidates:
        raise RuntimeError(f"No alive proxies found in group '{proxy_group_name}'.")

    records = [
        (node, record)
        for node in alive_candidates
        if node != current
        for record in storage.get_records_by_node(node, proxy_group_name)
        if record.service_name == service_name
    ]
    sorted_records = sorted(records, key=lambda x: x[1].reliability_score, reverse=True)

    if not sorted_records:
        fallback_candidates = [node for node in alive_candidates if node != current]
        if not fallback_candidates:
            raise RuntimeError(f"No suitable proxy found in group '{proxy_group_name}'.")
        recommended = fallback_candidates[0]
        selected_score = 0.0
    else:
        recommended = sorted_records[0][0]
        selected_score = sorted_records[0][1].reliability_score

    await client.select_proxy(proxy_group_name, recommended)

    print(f"    └── 推荐节点: {recommended:<20} | 可靠性评分: {selected_score:.3f}")

    return recommended


async def check_and_switch_once(
    clash: ClashClient,
    task: ProxyServicePair,
    clash_config: ClashConfig,
    storage: NodeHistoryStorage,
    *,
    probe_func: ProbeFunc = probe_service,
    prefix: str = "",
    switch_allowed: bool = True,
    switch_block_reason: Optional[str] = None,
) -> tuple[bool, bool]:
    """Check one service once and switch proxy if unavailable."""
    task_name = task.service_name
    proxy_group_name = task.proxy_group_name
    service_name = task.service_name
    task_name_padded = f"{task_name:<15}"

    current_node = None
    try:
        group_state = await clash.get_proxy(proxy_group_name)
        current_node = group_state.get("now")
    except Exception:
        pass

    try:
        ok, status_text = await probe_func(service_name, clash_config.http_proxy)
    except Exception as e:
        ok, status_text = False, f"检测异常: {e}"

    if isinstance(current_node, str) and current_node:
        storage.record_node_status(
            node_name=current_node,
            service_name=service_name,
            proxy_group=proxy_group_name,
            is_available=ok,
        )

    node_display = current_node if current_node else "未知"
    node_display_padded = f"{node_display:<20}"

    if ok:
        print(f"{prefix}[{task_name_padded}] ✔ 服务可用   | {status_text:<35} | 节点: {node_display_padded}")
        return True, False

    print(f"{prefix}[{task_name_padded}] ✖ 服务不可用 | {status_text:<35} | 节点: {node_display_padded}")

    if not switch_allowed:
        reason = switch_block_reason or "切换被限流"
        print(f"{prefix}[{task_name_padded}] ⏳ 暂不切换   | {reason:<35}")
        return False, False

    try:
        next_proxy = await select_next_proxy_in_group(
            clash, proxy_group_name, service_name, storage
        )
        next_proxy_display = f"{next_proxy:<20}"
        print(f"{prefix}[{task_name_padded}] ➤ 切换代理   | {proxy_group_name} -> {next_proxy_display}")
        storage.record_node_status(
            node_name=next_proxy,
            service_name=service_name,
            proxy_group=proxy_group_name,
            is_available=False,
        )
        return False, True
    except Exception as e:
        print(f"{prefix}[{task_name_padded}] ⚠ 切换失败   | {str(e):<35}")
        return False, False
