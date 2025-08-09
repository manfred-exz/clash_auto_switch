import asyncio
import argparse
import json
from typing import Optional, Tuple, List
from dataclasses import dataclass

import httpx

from clash_auto_switch.clash_api import ClashClient
from clash_auto_switch.storage import NodeHistoryStorage
from clash_auto_switch.project import (
    get_config_file_path,
    load_config,
    save_config,
    has_config,
)
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


@dataclass
class ClashConfig:
    """Clash controller configuration."""
    controller: str = "127.0.0.1:9097"
    secret: Optional[str] = None
    http_proxy: str = "http://127.0.0.1:7890"


@dataclass
class MonitoringConfig:
    """Monitoring behavior configuration."""
    interval_sec: float = 30.0
    max_rotations: int = 0
    once: bool = False


@dataclass
class TaskConfig:
    """Individual monitoring task configuration."""
    name: str
    proxy_group_name: str
    service_name: str
    enabled: bool = True


@dataclass
class AppConfig:
    """Complete application configuration."""
    clash: ClashConfig
    monitoring: MonitoringConfig
    tasks: List[TaskConfig]


def load_app_config() -> Optional[AppConfig]:
    """Load configuration from the standard location."""
    data = load_config()
    if not data:
        return None
    return parse_config_data(data)


def parse_config_data(data: dict) -> AppConfig:
    """Parse configuration data into AppConfig object."""
    # Parse configuration sections
    clash_data = data.get("clash", {})
    monitoring_data = data.get("monitoring", {})
    tasks_data = data.get("tasks", [])
    
    clash_config = ClashConfig(
        controller=clash_data.get("controller", "127.0.0.1:9097"),
        secret=clash_data.get("secret"),
        http_proxy=clash_data.get("http_proxy", "http://127.0.0.1:7890")
    )
    
    monitoring_config = MonitoringConfig(
        interval_sec=monitoring_data.get("interval_sec", 30.0),
        max_rotations=monitoring_data.get("max_rotations", 0),
        once=monitoring_data.get("once", False)
    )
    
    tasks = []
    for task_data in tasks_data:
        task = TaskConfig(
            name=task_data["name"],
            proxy_group_name=task_data["proxy_group_name"],
            service_name=task_data["service_name"],
            enabled=task_data.get("enabled", True)
        )
        tasks.append(task)
    
    return AppConfig(
        clash=clash_config,
        monitoring=monitoring_config,
        tasks=tasks
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
    
    print(f"    └── 推荐节点: {recommended:<20} | 可靠性评分: {selected_score:.3f}")
    
    return recommended


async def run_task(
    task: TaskConfig,
    clash_config: ClashConfig,
    monitoring_config: MonitoringConfig,
    storage: NodeHistoryStorage,
) -> None:
    """Run a single monitoring task."""
    task_name = task.name
    proxy_group_name = task.proxy_group_name
    service_name = task.service_name
    
    # Calculate padding for consistent alignment
    max_task_name_width = 15  # Fixed width for task name column
    task_name_padded = f"{task_name:<{max_task_name_width}}"
    
    print(f"[{task_name_padded}] 开始监控: 代理组={proxy_group_name}, 服务={service_name}")
    
    # Clash controller client
    async with ClashClient.from_external_controller(clash_config.controller, secret=clash_config.secret) as clash:
        rotations = 0
        
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
                    service_name, clash_config.http_proxy
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

            # Format current node display
            node_display = current_node if current_node else "未知"
            node_display_padded = f"{node_display:<20}"  # Fixed width for node column

            if ok:
                if rotations != 0:
                    rotations = 0
                print(f"[{task_name_padded}] ✔ 服务可用   | {status_text:<35} | 节点: {node_display_padded}")
                if monitoring_config.once:
                    return
                await asyncio.sleep(monitoring_config.interval_sec)
                continue

            print(f"[{task_name_padded}] ✖ 服务不可用 | {status_text:<35} | 节点: {node_display_padded}")

            try:
                next_proxy = await select_next_proxy_in_group(
                    clash, proxy_group_name, service_name, storage
                )
                rotations += 1
                next_proxy_display = f"{next_proxy:<20}"
                print(f"[{task_name_padded}] ➤ 切换代理   | {proxy_group_name} -> {next_proxy_display}")
                
                # Record the switch in storage
                storage.record_node_status(
                    node_name=next_proxy,
                    service_name=service_name,
                    proxy_group=proxy_group_name,
                    is_available=False  # We haven't tested the new node yet
                )
            except Exception as e:
                print(f"[{task_name_padded}] ⚠ 切换失败   | {str(e):<35}")
                # 等待后继续监控
                await asyncio.sleep(monitoring_config.interval_sec)
                continue

            if monitoring_config.max_rotations > 0 and rotations >= monitoring_config.max_rotations:
                print(f"[{task_name_padded}] ⏸ 暂停监控   | 已达到最大切换次数 ({monitoring_config.max_rotations})")
                rotations = 0
                await asyncio.sleep(max(monitoring_config.interval_sec, 30.0))


async def run_multiple_tasks(config: AppConfig) -> None:
    """Run multiple monitoring tasks concurrently."""
    storage = NodeHistoryStorage()
    storage.startup_cleanup()
    
    # Filter enabled tasks
    enabled_tasks = [task for task in config.tasks if task.enabled]
    
    if not enabled_tasks:
        print("没有启用的监控任务。")
        return
    
    print(f"🚀 启动 {len(enabled_tasks)} 个监控任务:")
    print("=" * 80)
    for task in enabled_tasks:
        task_name_padded = f"{task.name:<15}"
        print(f"  📋 [{task_name_padded}] 代理组: {task.proxy_group_name:<20} | 服务: {task.service_name}")
    print("=" * 80)
    print()
    
    # Create tasks for concurrent execution
    tasks = []
    for task_config in enabled_tasks:
        task = asyncio.create_task(
            run_task(task_config, config.clash, config.monitoring, storage),
            name=task_config.name
        )
        tasks.append(task)
    
    try:
        # Wait for all tasks to complete (which should be never in monitor mode)
        await asyncio.gather(*tasks)
    except Exception as e:
        print(f"监控任务异常: {e}")
        # Cancel all tasks
        for task in tasks:
            if not task.done():
                task.cancel()
        raise





def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "持续检测多个服务是否可用；若不可用则在指定Clash代理组内切换到下一个节点。\n"
            "所有配置通过配置文件管理，使用 --generate-config 创建配置文件。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument(
        "--once",
        action="store_true",
        help="只运行一次，不持续监控",
        default=False,
    )
    parser.add_argument(
        "--show-stats",
        type=str,
        nargs=2,
        metavar=("PROXY_GROUP", "SERVICE"),
        help="显示指定代理组和服务的节点统计信息并退出",
    )
    parser.add_argument(
        "--show-config",
        action="store_true",
        help="显示当前配置文件位置和内容",
        default=False,
    )
    parser.add_argument(
        "--generate-config",
        action="store_true",
        help="生成配置文件模板到默认位置并退出",
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


def get_template_config() -> dict:
    """Get the default configuration template."""
    return {
        "clash": {
            "controller": "127.0.0.1:9097",
            "secret": None,
            "http_proxy": "http://127.0.0.1:7890"
        },
        "monitoring": {
            "interval_sec": 30.0,
            "max_rotations": 0,
        },
        "tasks": [
            {
                "name": "ChatGPT-US",
                "proxy_group_name": "🇺🇸美国",
                "service_name": "chatgpt",
                "enabled": False
            },
            {
                "name": "Netflix-HK", 
                "proxy_group_name": "🇭🇰香港",
                "service_name": "netflix",
                "enabled": False
            },
            {
                "name": "YouTube-JP",
                "proxy_group_name": "🇯🇵日本", 
                "service_name": "youtube_premium",
                "enabled": False
            },
        ]
    }


def generate_config_template() -> str:
    """Generate configuration template file to the standard location."""
    template_content = get_template_config()
    
    if save_config(template_content):
        config_file = get_config_file_path()
        print(f"配置文件模板已生成: {config_file}")
        print("请根据需要修改配置文件中的代理组名称、服务名称等设置。")
        return str(config_file)
    else:
        raise RuntimeError("配置文件生成失败")


def show_config_info() -> None:
    """Display current configuration file location and content."""
    config_file = get_config_file_path()
    print(f"配置文件位置: {config_file}")
    
    if has_config():
        print("配置文件内容:")
        data = load_config()
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print("配置文件不存在。使用 --generate-config 创建配置文件。")


def main() -> None:
    args = parse_args()
    
    # Handle utility operations
    if args.generate_config:
        generate_config_template()
        return
    
    if args.show_config:
        show_config_info()
        return
    
    if args.show_stats:
        proxy_group_name, service_name = args.show_stats
        show_statistics(proxy_group_name, service_name)
        return
    
    # Load configuration file
    if not has_config():
        print("错误: 配置文件不存在")
        print("使用 --generate-config 创建配置文件")
        print("使用 --show-config 查看配置文件信息")
        return
    
    config = load_app_config()
    if not config:
        print("错误: 配置文件为空或格式错误")
        return
    
    # Override monitor setting if specified
    if args.once:
        config.monitoring.once = True
    
    try:
        config_file = get_config_file_path()
        print(f"使用配置文件: {config_file}")
        asyncio.run(run_multiple_tasks(config))
    except KeyboardInterrupt:
        print("收到 Ctrl-C，退出。")
        raise SystemExit(130)


if __name__ == "__main__":
    main()

