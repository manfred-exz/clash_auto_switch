"""
Command line entry point and argument parsing for clash_auto_switch.
"""

import argparse
import asyncio
import json

from clash_auto_switch.auto_monitor import AutoMonitorRunner
from clash_auto_switch.config import load_app_config
from clash_auto_switch.core.debug_tools import debug_switch_candidates
from clash_auto_switch.monitor import PeriodicMonitorRunner
from clash_auto_switch.core.storage import NodeHistoryStorage
from clash_auto_switch.project import (
    get_config_file_path,
    get_data_file_path,
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
        "--auto",
        action="store_true",
        help="根据 Clash 实时连接日志自动触发服务检测",
        default=False,
    )
    parser.add_argument(
        "--show-stats",
        action="store_true",
        help="显示所有有数据的服务统计信息并退出",
        default=False,
    )
    parser.add_argument(
        "--show-stats-detail",
        type=str,
        nargs=2,
        metavar=("PROXY_GROUP", "SERVICE"),
        help="显示指定代理组和服务的详细节点统计信息并退出",
    )
    parser.add_argument(
        "--debug-switch",
        type=str,
        nargs=2,
        metavar=("PROXY_GROUP", "SERVICE"),
        help="调试指定代理组和服务的切换候选节点排序，不执行切换",
    )
    parser.add_argument(
        "--clear-stats",
        action="store_true",
        help="清除节点统计信息",
        default=False,
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


def show_all_statistics() -> None:
    """Display simple statistics for all services with data."""
    storage = NodeHistoryStorage()

    # Since we removed complex statistics, show basic info
    print("\n📊 节点记录统计 (简化版)")
    print("=" * 60)

    # Collect services data from new storage format
    services_data = {}
    for node_name, service_records in storage._records_by_node.items():
        for record in service_records:
            service_name = record.service_name
            if service_name not in services_data:
                services_data[service_name] = []
            services_data[service_name].append((node_name, record))

    if not services_data:
        print("\n暂无任何服务的统计数据。")
        print("运行监控任务后将自动记录节点性能数据。")
        print("=" * 60)
        return

    for service_name, node_records in services_data.items():
        print(f"\n📋 服务: {service_name}")
        print(f"    节点数: {len(node_records)}")

        # Sort by reliability score and show top 3
        node_records.sort(key=lambda x: x[1].reliability_score, reverse=True)

        print("    🏆 前3个最可靠节点:")
        for i, (node_name, record) in enumerate(node_records[:3], 1):
            success_rate = (record.successful_checks / record.total_checks
                           if record.total_checks > 0 else 0.0)
            status_emoji = "✅" if record.status == "available" else "❌"
            print(f"       {i}. {node_name[:30]:<30} | "
                  f"可靠性: {record.reliability_score:.3f} | "
                  f"成功率: {success_rate:6.2%} | "
                  f"检测: {record.total_checks:3d}次 {status_emoji}")

    print("=" * 60)


def show_detailed_statistics(service_name: str, proxy_group_name: str | None = None) -> None:
    """Display detailed statistics for the given proxy group and service."""
    storage = NodeHistoryStorage()
    node_records = storage.get_records_by_service(service_name, proxy_group_name)

    if not node_records:
        print(f"\n❌ 没有找到服务 {service_name} 的数据")
        return

    print(f"\n=== 详细统计信息: {service_name} ===")
    print(f"总节点数: {len(node_records)}")

    total_checks = sum(record.total_checks for record in node_records)
    total_successful = sum(record.successful_checks for record in node_records)
    overall_success_rate = total_successful / total_checks if total_checks > 0 else 0.0

    print(f"总检测次数: {total_checks}")
    print(f"整体成功率: {overall_success_rate:.2%}")

    if node_records:
        best_node_name, best_record = node_records[0]  # Already sorted by reliability
        print(f"最可靠节点: {best_node_name} (可靠性评分: {best_record.reliability_score:.3f})")

    print("\n📊 节点可靠性排名:")
    for i, (node_name, record) in enumerate(node_records[:10], 1):  # Show top 10
        success_rate = (record.successful_checks / record.total_checks
                       if record.total_checks > 0 else 0.0)
        status_emoji = "✅" if record.status == "available" else "❌"
        print(f"  {i:2d}. {node_name:<30} "
              f"可靠性: {record.reliability_score:.3f} "
              f"成功率: {success_rate:.2%} "
              f"检测次数: {record.total_checks:3d} "
              f"{status_emoji}")


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

    if args.clear_stats:
        get_data_file_path().unlink(missing_ok=True)
        print("节点统计信息已清除")
        return

    if args.show_stats:
        show_all_statistics()
        return

    if args.show_stats_detail:
        proxy_group_name, service_name = args.show_stats_detail
        show_detailed_statistics(service_name, proxy_group_name)
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

    if args.debug_switch:
        proxy_group_name, service_name = args.debug_switch
        asyncio.run(debug_switch_candidates(config.clash, proxy_group_name, service_name))
        return

    # Override monitor setting if specified
    if args.once:
        config.monitoring.once = True

    try:
        config_file = get_config_file_path()
        print(f"使用配置文件: {config_file}")
        if args.auto:
            asyncio.run(AutoMonitorRunner(config).run())
        else:
            asyncio.run(PeriodicMonitorRunner(config).run())
    except KeyboardInterrupt:
        print("收到 Ctrl-C，退出。")
        raise SystemExit(130)


if __name__ == "__main__":
    main()
