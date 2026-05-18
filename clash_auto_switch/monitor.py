import asyncio

from clash_auto_switch.clash_api import ClashClient
from clash_auto_switch.defs import AppConfig, ClashConfig, MonitoringConfig, ProxyServicePair
from clash_auto_switch.proxy_switcher import check_and_switch_once
from clash_auto_switch.service_tester import probe_service, probe_service_multi
from clash_auto_switch.storage import NodeHistoryStorage


async def run_task(
    task: ProxyServicePair,
    clash_config: ClashConfig,
    monitoring_config: MonitoringConfig,
    storage: NodeHistoryStorage,
) -> None:
    """Run a single periodic monitoring task."""
    task_name = task.service_name
    task_name_padded = f"{task_name:<15}"

    print(f"[{task_name_padded}] 开始监控: 代理组={task.proxy_group_name}, 服务={task.service_name}")

    async with ClashClient.from_external_controller(clash_config.controller, secret=clash_config.secret) as clash:
        rotations = 0
        is_new_proxy = True

        while True:
            probe_func = probe_service_multi if is_new_proxy else probe_service
            ok, switched = await check_and_switch_once(
                clash,
                task,
                clash_config,
                storage,
                probe_func=probe_func,
            )
            is_new_proxy = False

            if switched:
                rotations += 1
            elif ok:
                rotations = 0

            if monitoring_config.once:
                return

            if ok:
                await asyncio.sleep(monitoring_config.interval_sec)
                continue

            if monitoring_config.max_rotations > 0 and rotations >= monitoring_config.max_rotations:
                print(f"[{task_name_padded}] ⏸ 暂停监控   | 已达到最大切换次数 ({monitoring_config.max_rotations})")
                rotations = 0
                await asyncio.sleep(max(monitoring_config.interval_sec, 30.0))
                continue

            if not switched:
                await asyncio.sleep(monitoring_config.interval_sec)


async def run_multiple_tasks(config: AppConfig) -> None:
    """Run periodic monitoring tasks concurrently."""
    storage = NodeHistoryStorage()
    storage.startup_cleanup()

    enabled_tasks = [task for task in config.tasks if task.enabled]
    if not enabled_tasks:
        print("没有启用的监控任务。")
        return

    print(f"🚀 启动 {len(enabled_tasks)} 个监控任务:")
    print("=" * 80)
    for task in enabled_tasks:
        task_name_padded = f"{task.service_name:<15}"
        print(f"  📋 [{task_name_padded}] 代理组: {task.proxy_group_name:<20} | 服务: {task.service_name}")
    print("=" * 80)
    print()

    tasks = [
        asyncio.create_task(
            run_task(task_config, config.clash, config.monitoring, storage),
            name=task_config.service_name,
        )
        for task_config in enabled_tasks
    ]

    try:
        await asyncio.gather(*tasks)
    except Exception as e:
        print(f"监控任务异常: {e}")
        for task in tasks:
            if not task.done():
                task.cancel()
        raise
