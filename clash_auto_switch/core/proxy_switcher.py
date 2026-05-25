from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

import httpx

from clash_auto_switch.core.clash_state import ClashProxyState
from clash_auto_switch.defs import ClashConfig, ProxyServicePair
from clash_auto_switch.core.service_tester import (
    check_proxy_connectivity,
    probe_service,
    service_debug_event_handler,
)
from clash_auto_switch.core.storage import NodeHistoryStorage


ProbeFunc = Callable[[str, Optional[str]], Awaitable[tuple[bool, str]]]
ConnectivityFunc = Callable[[Optional[str]], Awaitable[tuple[bool, str]]]
EventFunc = Callable[[str, str], None]
UNTESTED_NODE_SCORE = 0.5


@dataclass(frozen=True)
class ProxyCandidate:
    name: str
    score: float
    status: str = "untested"
    total_checks: int = 0
    successful_checks: int = 0
    is_current: bool = False
    is_alive: bool = True


@dataclass(frozen=True)
class SwitchAttemptResult:
    ok: bool
    switched: bool
    attempts: int


@dataclass(frozen=True)
class NodeProbeResult:
    ok: bool
    status_text: str
    connectivity_ok: bool
    connectivity_status: str
    recorded: bool


async def list_alive_proxy_candidates(
    client: ClashProxyState,
    proxy_group_name: str,
    service_name: str,
    storage: NodeHistoryStorage,
    *,
    disabled_node_names: Optional[set[str]] = None,
) -> list[ProxyCandidate]:
    """Build switch candidates from every alive node in a proxy group."""
    disabled_node_names = disabled_node_names or set()
    group_info = await client.get_proxy(proxy_group_name)
    candidates = group_info.get("all") or []
    if not isinstance(candidates, list) or not candidates:
        raise ValueError(f"Proxy group '{proxy_group_name}' has no candidates in 'all'")

    current = group_info.get("now")
    switch_candidates = []
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue

        is_alive = True
        try:
            candidate_info = await client.get_proxy(candidate)
            is_alive = candidate_info.get("alive") is not False
        except httpx.HTTPError:
            is_alive = True

        if not is_alive:
            continue

        if candidate in disabled_node_names:
            continue

        record = storage.get_node_service_record(candidate, service_name, proxy_group_name)
        if record is None:
            switch_candidates.append(
                ProxyCandidate(
                    name=candidate,
                    score=UNTESTED_NODE_SCORE,
                    is_current=candidate == current,
                    is_alive=True,
                )
            )
            continue

        switch_candidates.append(
            ProxyCandidate(
                name=candidate,
                score=record.reliability_score,
                status=record.status,
                total_checks=record.total_checks,
                successful_checks=record.successful_checks,
                is_current=candidate == current,
                is_alive=True,
            )
        )

    return sorted(
        switch_candidates,
        key=lambda candidate: (-candidate.score, candidate.is_current, candidate.name),
    )


async def switch_to_next_ranked_proxy(
    client: ClashProxyState,
    proxy_group_name: str,
    service_name: str,
    storage: NodeHistoryStorage,
    *,
    disabled_node_names: Optional[set[str]] = None,
    event_handler: Optional[EventFunc] = None,
) -> str:
    """Switch to the highest-ranked alive proxy that is not currently selected."""
    candidates = await list_alive_proxy_candidates(
        client,
        proxy_group_name,
        service_name,
        storage,
        disabled_node_names=disabled_node_names,
    )
    if not candidates:
        raise RuntimeError(f"No alive proxies found in group '{proxy_group_name}'.")

    proxy_to_try = next((item for item in candidates if not item.is_current), None)
    if proxy_to_try is None:
        raise RuntimeError(f"No suitable proxy found in group '{proxy_group_name}'.")

    selected_proxy = proxy_to_try.name
    selected_score = proxy_to_try.score

    await switch_proxy_group_and_verify(client, proxy_group_name, selected_proxy)

    if event_handler is not None:
        event_handler(service_name, f"尝试节点: {selected_proxy} | 历史评分: {selected_score:.3f}")

    return selected_proxy


async def switch_proxy_group_and_verify(
    client: ClashProxyState,
    proxy_group_name: str,
    proxy_name: str,
) -> None:
    """Switch a Clash proxy group and verify the selected node was applied."""
    await client.select_proxy(proxy_group_name, proxy_name)
    verified_group_info = await client.get_proxy(proxy_group_name)
    verified_current = verified_group_info.get("now")
    if verified_current != proxy_name:
        raise RuntimeError(
            f"Proxy group '{proxy_group_name}' switch verification failed: "
            f"expected '{proxy_name}', got '{verified_current}'"
        )


async def probe_current_node_and_switch_if_unavailable(
    clash: ClashProxyState,
    task: ProxyServicePair,
    clash_config: ClashConfig,
    storage: NodeHistoryStorage,
    *,
    probe_func: ProbeFunc = probe_service,
    connectivity_func: ConnectivityFunc = check_proxy_connectivity,
    switch_allowed: bool = True,
    switch_block_reason: Optional[str] = None,
    disabled_node_names: Optional[set[str]] = None,
    event_handler: Optional[EventFunc] = None,
) -> tuple[bool, bool]:
    """Check one service once and switch proxy if unavailable."""
    proxy_group_name = task.proxy_group_name
    service_name = task.service_name

    current_node = None
    try:
        group_state = await clash.get_proxy(proxy_group_name)
        current_node = group_state.get("now")
    except Exception:
        pass

    connectivity_ok, connectivity_status = await run_connectivity_check(
        connectivity_func,
        clash_config.http_proxy,
    )
    if not connectivity_ok:
        node_display = current_node if current_node else "未知"
        if event_handler is not None:
            event_handler(service_name, f"节点连通性失败 | {connectivity_status} | 跳过节点: {node_display}")
        if not switch_allowed:
            reason = switch_block_reason or "切换被限流"
            if event_handler is not None:
                event_handler(service_name, f"暂不切换 | {reason}")
            return False, False
        return False, await switch_to_next_available_proxy(
            clash,
            proxy_group_name,
            service_name,
            storage,
            disabled_node_names=disabled_node_names,
            event_handler=event_handler,
        )

    try:
        with service_debug_event_handler(event_handler):
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

    if ok:
        if event_handler is not None:
            event_handler(service_name, f"服务可用 | {status_text} | 节点: {node_display}")
        return True, False

    if event_handler is not None:
        event_handler(service_name, f"服务不可用 | {status_text} | 节点: {node_display}")

    if not switch_allowed:
        reason = switch_block_reason or "切换被限流"
        if event_handler is not None:
            event_handler(service_name, f"暂不切换 | {reason}")
        return False, False

    return False, await switch_to_next_available_proxy(
        clash,
        proxy_group_name,
        service_name,
        storage,
        disabled_node_names=disabled_node_names,
        event_handler=event_handler,
    )


async def probe_current_node_once(
    clash: ClashProxyState,
    task: ProxyServicePair,
    clash_config: ClashConfig,
    storage: NodeHistoryStorage,
    *,
    probe_func: ProbeFunc = probe_service,
    connectivity_func: ConnectivityFunc = check_proxy_connectivity,
    event_handler: Optional[EventFunc] = None,
) -> NodeProbeResult:
    proxy_group_name = task.proxy_group_name
    service_name = task.service_name
    current_node = None
    try:
        group_state = await clash.get_proxy(proxy_group_name)
        current_node = group_state.get("now")
    except Exception:
        pass

    connectivity_ok, connectivity_status = await run_connectivity_check(
        connectivity_func,
        clash_config.http_proxy,
    )
    if not connectivity_ok:
        node_display = current_node if current_node else "未知"
        if event_handler is not None:
            event_handler(service_name, f"节点连通性失败 | {connectivity_status} | 跳过节点: {node_display}")
        return NodeProbeResult(
            ok=False,
            status_text=connectivity_status,
            connectivity_ok=False,
            connectivity_status=connectivity_status,
            recorded=False,
        )

    try:
        with service_debug_event_handler(event_handler):
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

    return NodeProbeResult(
        ok=ok,
        status_text=status_text,
        connectivity_ok=True,
        connectivity_status=connectivity_status,
        recorded=isinstance(current_node, str) and bool(current_node),
    )


async def switch_to_next_available_proxy(
    clash: ClashProxyState,
    proxy_group_name: str,
    service_name: str,
    storage: NodeHistoryStorage,
    *,
    disabled_node_names: Optional[set[str]] = None,
    event_handler: Optional[EventFunc] = None,
) -> bool:
    try:
        next_proxy = await switch_to_next_ranked_proxy(
            clash,
            proxy_group_name,
            service_name,
            storage,
            disabled_node_names=disabled_node_names,
            event_handler=event_handler,
        )
        if event_handler is not None:
            event_handler(service_name, f"切换代理 | {proxy_group_name} -> {next_proxy}")
        return True
    except Exception as e:
        if event_handler is not None:
            event_handler(service_name, f"切换失败 | {e}")
        return False


async def run_connectivity_check(
    connectivity_func: ConnectivityFunc,
    proxy_url: Optional[str],
) -> tuple[bool, str]:
    try:
        return await connectivity_func(proxy_url)
    except Exception as exc:
        return False, f"Cloudflare connectivity check error: {exc}"


async def switch_until_service_available(
    clash: ClashProxyState,
    task: ProxyServicePair,
    clash_config: ClashConfig,
    storage: NodeHistoryStorage,
    *,
    probe_func: ProbeFunc = probe_service,
    connectivity_func: ConnectivityFunc = check_proxy_connectivity,
    switch_allowed: bool = True,
    switch_block_reason: Optional[str] = None,
    disabled_node_names: Optional[set[str]] = None,
    event_handler: Optional[EventFunc] = None,
    after_switch: Optional[Callable[[str], Awaitable[None]]] = None,
    max_attempts: Optional[int] = None,
) -> SwitchAttemptResult:
    """Keep checking and switching until the service is available or no switch can be made."""
    attempts = 0
    switched_any = False
    tried_nodes: set[str] = set()

    while True:
        attempts += 1
        ok, switched = await probe_current_node_and_switch_if_unavailable(
            clash,
            task,
            clash_config,
            storage,
            probe_func=probe_func,
            connectivity_func=connectivity_func,
            switch_allowed=switch_allowed,
            switch_block_reason=switch_block_reason,
            disabled_node_names=disabled_node_names,
            event_handler=event_handler,
        )
        switched_any = switched_any or switched

        if ok or not switched:
            return SwitchAttemptResult(ok=ok, switched=switched_any, attempts=attempts)

        if after_switch is not None:
            group_state = await clash.get_proxy(task.proxy_group_name)
            current_node = group_state.get("now")
            if isinstance(current_node, str) and current_node:
                await after_switch(current_node)

        if max_attempts is not None and attempts >= max_attempts:
            if event_handler is not None:
                event_handler(task.service_name, f"停止尝试 | 已达到最大尝试次数 {max_attempts}")
            return SwitchAttemptResult(ok=False, switched=switched_any, attempts=attempts)

        group_state = await clash.get_proxy(task.proxy_group_name)
        current_node = group_state.get("now")
        if not isinstance(current_node, str) or not current_node:
            return SwitchAttemptResult(ok=False, switched=switched_any, attempts=attempts)

        if current_node in tried_nodes:
            if event_handler is not None:
                event_handler(task.service_name, f"停止尝试 | 节点重复: {current_node}")
            return SwitchAttemptResult(ok=False, switched=switched_any, attempts=attempts)
        tried_nodes.add(current_node)
