from clash_auto_switch.clash_api import ClashClient
from clash_auto_switch.defs import ClashConfig
from clash_auto_switch.proxy_switcher import list_alive_proxy_candidates
from clash_auto_switch.storage import NodeHistoryStorage


async def debug_switch_candidates(
    clash_config: ClashConfig,
    proxy_group_name: str,
    service_name: str,
) -> None:
    storage = NodeHistoryStorage()
    async with ClashClient.from_external_controller(
        clash_config.controller,
        secret=clash_config.secret,
    ) as clash:
        group_info = await clash.get_proxy(proxy_group_name)
        current = group_info.get("now")
        candidates = await list_alive_proxy_candidates(
            clash,
            proxy_group_name,
            service_name,
            storage,
        )

    print(f"ProxyGroup: {proxy_group_name}")
    print(f"Service:    {service_name}")
    print(f"Current:    {current}")
    print()
    print(f"{'#':>3} {'Cur':>3} {'Score':>7} {'Status':>10} {'Checks':>8} {'Success':>8}  Node")
    print("-" * 90)
    for index, candidate in enumerate(candidates, 1):
        success_rate = (
            candidate.successful_checks / candidate.total_checks
            if candidate.total_checks
            else 0.0
        )
        current_mark = "*" if candidate.is_current else ""
        print(
            f"{index:>3} {current_mark:>3} "
            f"{candidate.score:>7.3f} "
            f"{candidate.status:>10} "
            f"{candidate.total_checks:>8} "
            f"{success_rate:>7.0%}  "
            f"{candidate.name}"
        )

    next_candidate = next((candidate for candidate in candidates if not candidate.is_current), None)
    if next_candidate is None:
        print("\nNext: <none>")
    else:
        print(f"\nNext: {next_candidate.name} ({next_candidate.score:.3f}, {next_candidate.status})")
