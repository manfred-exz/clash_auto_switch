"""
Command line entry point and argument parsing for clash_auto_switch.
"""

import argparse
import asyncio

from clash_auto_switch.auto_monitor import AutoMonitorRunner
from clash_auto_switch.config import load_app_config, parse_config_data, save_app_config
from clash_auto_switch.core.storage import NodeHistoryStorage
from clash_auto_switch.project import (
    get_config_file_path,
    get_data_file_path,
    has_config,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        usage="%(prog)s [COMMAND]",
        description="启动 Clash 自动切换 TUI。无子命令时直接进入 TUI。",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    stats_parser = subparsers.add_parser(
        "stats",
        help="显示节点统计信息",
        description="显示所有服务的节点统计信息，或指定 ProxyGroup/service 的详情。",
    )
    stats_parser.add_argument(
        "proxy_group",
        nargs="?",
        help="ProxyGroup 名称；提供时必须同时提供 service",
    )
    stats_parser.add_argument(
        "service",
        nargs="?",
        help="服务名称",
    )

    debug_parser = subparsers.add_parser(
        "debug-switch",
        help="调试切换候选排序，不执行切换",
        description="调试指定 ProxyGroup/service 的切换候选排序，不执行切换。",
    )
    debug_parser.add_argument("proxy_group", metavar="PROXY_GROUP")
    debug_parser.add_argument("service", metavar="SERVICE")

    subparsers.add_parser(
        "clear-stats",
        help="清除节点统计信息",
        description="清除本地节点统计信息。",
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


def _prompt(text: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{text}{suffix}: ").strip()
    return value or (default or "")


def _ensure_runtime_config_interactive() -> None:
    config_file = get_config_file_path()
    config = load_app_config()
    if config is None:
        config = parse_config_data({})

    need_prompt = (
        not config.clash.controller.strip()
        or not config.clash.http_proxy.strip()
        or not has_config()
    )
    if not need_prompt:
        return

    print(f"配置文件: {config_file}")
    print("首次启动或缺少必要参数，请输入 Clash 连接信息。")
    config.clash.controller = _prompt("external-controller", config.clash.controller).strip()
    while not config.clash.controller:
        print("controller 不能为空。")
        config.clash.controller = _prompt("external-controller", "127.0.0.1:9097").strip()

    secret_value = _prompt("secret (可留空)", config.clash.secret or "")
    config.clash.secret = secret_value or None

    config.clash.http_proxy = _prompt("http_proxy", config.clash.http_proxy).strip()
    while not config.clash.http_proxy:
        print("http_proxy 不能为空。")
        config.clash.http_proxy = _prompt("http_proxy", "http://127.0.0.1:7890").strip()

    if not save_app_config(config):
        raise RuntimeError(f"配置文件保存失败: {config_file}")
    print(f"配置已保存: {config_file}")
    print("启动后可按 t 添加 task。")


def main() -> None:
    """Main entry point for the application."""
    args = parse_args()

    if args.command == "clear-stats":
        get_data_file_path().unlink(missing_ok=True)
        print("节点统计信息已清除")
        return

    if args.command == "stats":
        if (args.proxy_group is None) != (args.service is None):
            raise SystemExit("stats 需要同时提供 PROXY_GROUP 和 SERVICE，或两个参数都不提供")
        if args.proxy_group and args.service:
            show_detailed_statistics(args.service, args.proxy_group)
            return
        show_all_statistics()
        return

    _ensure_runtime_config_interactive()
    config = load_app_config()
    if not config:
        print("错误: 配置文件为空或格式错误")
        return

    try:
        config_file = get_config_file_path()
        print(f"使用配置文件: {config_file}")
        if args.command is None:
            asyncio.run(AutoMonitorRunner(config).run())
    except KeyboardInterrupt:
        print("收到 Ctrl-C，退出。")
        raise SystemExit(130)


if __name__ == "__main__":
    main()
