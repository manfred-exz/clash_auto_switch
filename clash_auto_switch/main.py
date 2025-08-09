import asyncio
import argparse
from typing import Optional, Tuple

import httpx

from clash_auto_switch.clash_api import ClashClient
from clash_auto_switch.storage import NodeHistoryStorage
from clash_auto_switch.unlock_tester import (
    check_bilibili_china_mainland,
    check_bilibili_hk_mc_tw,
    check_chatgpt_combined,
    check_gemini,
    check_youtube_premium,
    check_bahamut_anime,
    check_netflix,
    check_disney_plus,
    check_prime_video,
)


async def probe_service(
    service_name: str,
    proxy_url: Optional[str],
) -> Tuple[bool, str]:
    """Return (is_unlocked, human_status).

    The service is considered unlocked only when status == "Yes".
    For ChatGPT, unlocked if either iOS/Web returns Yes.
    """

    key = service_name.strip().lower()
    # Normalize common aliases
    alias = {
        "bilibili_cn": "bilibili_mainland",
        "bilibili_mainland": "bilibili_mainland",
        "bilibili_hk": "bilibili_hk_mc_tw",
        "bilibili_hk_mc_tw": "bilibili_hk_mc_tw",
        "chatgpt": "chatgpt",
        "openai": "chatgpt",
        "gemini": "gemini",
        "youtube": "youtube_premium",
        "youtube_premium": "youtube_premium",
        "bahamut": "bahamut_anime",
        "bahamut_anime": "bahamut_anime",
        "netflix": "netflix",
        "disney": "disney_plus",
        "disney+": "disney_plus",
        "disney_plus": "disney_plus",
        "prime": "prime_video",
        "prime_video": "prime_video",
        "amazon_prime": "prime_video",
    }

    norm = alias.get(key, key)

    if norm == "bilibili_mainland":
        result = await check_bilibili_china_mainland(proxy_url)
        return result.status == "Yes", f"{result.name}: {result.status}"
    if norm == "bilibili_hk_mc_tw":
        result = await check_bilibili_hk_mc_tw(proxy_url)
        return result.status == "Yes", f"{result.name}: {result.status}"
    if norm == "chatgpt":
        items = await check_chatgpt_combined(proxy_url)
        unlocked = any(item.status == "Yes" for item in items)
        status_text = ", ".join(f"{i.name}: {i.status}{(' (' + i.region + ')') if i.region else ''}" for i in items)
        return unlocked, status_text
    if norm == "gemini":
        result = await check_gemini(proxy_url)
        region = f" ({result.region})" if result.region else ""
        return result.status == "Yes", f"{result.name}: {result.status}{region}"
    if norm == "youtube_premium":
        result = await check_youtube_premium(proxy_url)
        region = f" ({result.region})" if result.region else ""
        return result.status == "Yes", f"{result.name}: {result.status}{region}"
    if norm == "bahamut_anime":
        result = await check_bahamut_anime(proxy_url)
        region = f" ({result.region})" if result.region else ""
        return result.status == "Yes", f"{result.name}: {result.status}{region}"
    if norm == "netflix":
        result = await check_netflix(proxy_url)
        region = f" ({result.region})" if result.region else ""
        return result.status == "Yes", f"{result.name}: {result.status}{region}"
    if norm == "disney_plus":
        result = await check_disney_plus(proxy_url)
        region = f" ({result.region})" if result.region else ""
        return result.status == "Yes", f"{result.name}: {result.status}{region}"
    if norm == "prime_video":
        result = await check_prime_video(proxy_url)
        region = f" ({result.region})" if result.region else ""
        return result.status == "Yes", f"{result.name}: {result.status}{region}"

    return False, f"未知服务: {service_name}"


async def select_next_proxy_in_group(
    client: ClashClient,
    proxy_group_name: str,
    service_name: str,
    storage: NodeHistoryStorage,
) -> str:
    """Select the next eligible proxy in a group based on reliability scores.

    Strategy:
    1) Get all available proxies in the group
    2) Filter out dead proxies
    3) Use storage's intelligent recommendation system based on reliability scores
    4) Select the most reliable available proxy

    Args:
        client: ClashClient instance
        proxy_group_name: Name of the proxy group
        service_name: Service being tested (for reliability lookup)
        storage: NodeHistoryStorage instance for reliability data

    Returns:
        Selected proxy name

    Raises:
        RuntimeError: If no eligible proxy found
    """
    group_info = await client.get_proxy(proxy_group_name)
    candidates = group_info.get("all") or []
    if not isinstance(candidates, list) or not candidates:
        raise ValueError(
            f"Proxy group '{proxy_group_name}' has no candidates in 'all'"
        )

    current = group_info.get("now")
    
    # Filter candidates: check if alive and remove explicitly dead ones
    alive_candidates = []
    for candidate in candidates:
        try:
            candidate_info = await client.get_proxy(candidate)
            # Skip if explicitly dead
            if candidate_info.get("alive") is False:
                continue
            alive_candidates.append(candidate)
        except httpx.HTTPError:
            # If cannot fetch details, assume it might work and include it
            alive_candidates.append(candidate)
    
    if not alive_candidates:
        raise RuntimeError(
            f"No alive proxies found in group '{proxy_group_name}'."
        )
    
    # Use storage's intelligent recommendation system
    recommended = storage.get_recommended_node(
        proxy_group=proxy_group_name,
        service_name=service_name,
        available_nodes=alive_candidates,
        current_node=current
    )
    
    if recommended is None:
        raise RuntimeError(
            f"No suitable proxy found in group '{proxy_group_name}'."
        )
    
    # Switch to the recommended proxy
    await client.select_proxy(proxy_group_name, recommended)
    
    # Get reliability info for logging
    reliable_nodes = storage.get_nodes_by_reliability(
        proxy_group_name, service_name, min_reliability=0.0, limit=len(candidates)
    )
    reliability_map = {node['node']: node['reliability_score'] for node in reliable_nodes}
    selected_score = reliability_map.get(recommended, 0.0)
    
    print(f"选择代理: {recommended} (可靠性评分: {selected_score:.3f})")
    
    return recommended


async def run(
    proxy_group_name: str,
    service_name: str,
    controller: str,
    secret: Optional[str],
    http_proxy: str,
    interval_sec: float,
    max_rotations: int,
    monitor: bool,
    storage: Optional[NodeHistoryStorage] = None,
) -> None:
    # Use provided storage or create new one
    if storage is None:
        storage = NodeHistoryStorage()
    
    # Clash controller client
    async with ClashClient.from_external_controller(controller, secret=secret) as clash:
        # Probe HTTP client routed via Clash HTTP proxy
        rotations = 0
        print(f"开始监控: 代理组={proxy_group_name}, 服务={service_name}")
        
        while True:
            # Get current node before testing
            current_node = None
            try:
                group_state = await clash.get_proxy(proxy_group_name)
                current_node = group_state.get("now")
            except Exception:
                pass

            try:
                ok, status_text = await probe_service(
                    service_name, http_proxy
                )
            except Exception as e:
                ok, status_text = False, f"检测异常: {e}"

            # Record node status in persistent storage
            if isinstance(current_node, str) and current_node:
                storage.record_node_status(
                    node_name=current_node,
                    service_name=service_name,
                    proxy_group=proxy_group_name,
                    is_available=ok
                )

            if ok:
                if rotations != 0:
                    rotations = 0
                print(f"服务可用 ✔ - {status_text} [节点: {current_node}]")
                if not monitor:
                    return
                await asyncio.sleep(interval_sec)
                continue

            print(f"服务不可用 ✖ - {status_text} [节点: {current_node}]")

            try:
                next_proxy = await select_next_proxy_in_group(
                    clash, proxy_group_name, service_name, storage
                )
                rotations += 1
                print(f"已切换到下一个代理: {proxy_group_name} -> {next_proxy}")
                
                # Record the switch in storage
                storage.record_node_status(
                    node_name=next_proxy,
                    service_name=service_name,
                    proxy_group=proxy_group_name,
                    is_available=False  # We haven't tested the new node yet
                )
            except Exception as e:
                print(f"切换代理失败: {e}")
                # 等待后继续监控
                await asyncio.sleep(interval_sec)
                continue

            if max_rotations > 0 and rotations >= max_rotations:
                print(
                    f"已达到最大切换次数 ({max_rotations})，暂停后继续监控。"
                )
                rotations = 0
                await asyncio.sleep(max(interval_sec, 30.0))
            # else:
            #     await asyncio.sleep(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "持续检测某服务是否可用；若不可用则在指定Clash代理组内切换到下一个节点。"
        )
    )
    parser.add_argument("proxy_group_name", type=str, help="Clash 代理组名称（Selector）")
    parser.add_argument("service_name", type=str, help="要检测的服务名称，如 chatgpt/netflix/gemini 等")

    parser.add_argument(
        "--controller",
        type=str,
        default="127.0.0.1:9097",
        help="Clash external-controller 地址（可包含协议），默认 127.0.0.1:9097",
    )
    parser.add_argument(
        "--secret",
        type=str,
        default=None,
        help="Clash REST API 的 Secret（如有设置）",
    )
    parser.add_argument(
        "--http-proxy",
        type=str,
        default="http://127.0.0.1:7890",
        help="用于探测请求的 HTTP 代理（指向 Clash 的 HTTP/SOCKS 代理端口）",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=30.0,
        help="每次探测/切换之间的等待秒数，默认 30s",
    )
    parser.add_argument(
        "--max-rotations",
        type=int,
        default=0,
        help="最大切换次数，0 表示无限直到成功",
    )
    parser.add_argument(
        "--monitor",
        action="store_true",
        help="开启持续监控（默认关闭）。关闭时，服务一旦可用即退出。",
        default=False,
    )
    parser.add_argument(
        "--show-stats",
        action="store_true",
        help="显示节点统计信息并退出",
        default=False,
    )
    return parser.parse_args()


def show_statistics(proxy_group_name: str, service_name: str) -> None:
    """Display statistics for the given proxy group and service."""
    storage = NodeHistoryStorage()
    stats = storage.get_statistics(proxy_group_name, service_name)
    
    print(f"\n=== 统计信息: {proxy_group_name} / {service_name} ===")
    print(f"总节点数: {stats['total_nodes']}")
    print(f"总检测次数: {stats['total_checks']}")
    print(f"整体成功率: {stats['success_rate']:.2%}")
    
    if stats['most_reliable_node']:
        score = stats['highest_reliability_score']
        print(f"最可靠节点: {stats['most_reliable_node']} (可靠性评分: {score:.3f})")
    
    if stats['last_successful_node']:
        print(f"最近成功节点: {stats['last_successful_node']}")
    
    # Show reliability rankings
    rankings = stats.get('reliability_rankings', [])
    if rankings:
        print("\n📊 节点可靠性排名:")
        for i, ranking in enumerate(rankings[:10], 1):  # Show top 10
            status_emoji = "✅" if ranking['current_status'] == "available" else "❌"
            print(f"  {i:2d}. {ranking['node']:<20} "
                  f"可靠性: {ranking['reliability_score']:.3f} "
                  f"成功率: {ranking['success_rate']:.2%} "
                  f"检测次数: {ranking['total_checks']:3d} "
                  f"{status_emoji}")
    
    print("\n📈 详细统计:")
    for node, node_stats in stats.get('node_stats', {}).items():
        reliability = node_stats.get('reliability_score', 0.0)
        success_rate = node_stats['success_rate']
        status_emoji = "✅" if node_stats['current_status'] == "available" else "❌"
        print(f"  {node}: "
              f"可靠性评分 {reliability:.3f} | "
              f"成功率 {success_rate:.2%} ({node_stats['successful']}/{node_stats['total']}) | "
              f"检测次数 {node_stats.get('total_checks', 0)} {status_emoji}")


def main() -> None:
    args = parse_args()
    
    # Handle utility operations
    if args.show_stats:
        show_statistics(args.proxy_group_name, args.service_name)
        return
    
    storage = NodeHistoryStorage()
    storage.startup_cleanup()
    
    try:
        asyncio.run(
            run(
                proxy_group_name=args.proxy_group_name,
                service_name=args.service_name,
                controller=args.controller,
                secret=args.secret,
                http_proxy=args.http_proxy,
                interval_sec=args.interval,
                max_rotations=args.max_rotations,
                monitor=args.monitor,
                storage=storage,
            )
        )
    except KeyboardInterrupt:
        print("收到 Ctrl-C，退出。")
        raise SystemExit(130)


if __name__ == "__main__":
    main()

