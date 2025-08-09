"""
Command line entry point and argument parsing for clash_auto_switch.
"""

import argparse
import asyncio
import json

from clash_auto_switch.monitor import (
    load_app_config,
    run_multiple_tasks,
)
from clash_auto_switch.storage import NodeHistoryStorage
from clash_auto_switch.project import (
    get_config_file_path,
    load_config,
    save_config,
    has_config,
    get_template_config,
)


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
    """Main entry point for the application."""
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