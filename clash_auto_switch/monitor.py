import asyncio
from contextlib import suppress

from clash_auto_switch.clash_api import ClashClient
from clash_auto_switch.connections import close_task_service_connections_best_effort
from clash_auto_switch.defs import AppConfig, ClashConfig, MonitoringConfig, ProxyServicePair
from clash_auto_switch.proxy_switcher import (
    probe_current_node_and_switch_if_unavailable,
    switch_proxy_group_and_verify,
    switch_until_service_available,
)
from clash_auto_switch.service_tester import probe_service, probe_service_multi
from clash_auto_switch.storage import NodeHistoryStorage
from clash_auto_switch.tui import MonitorTui


async def run_periodic_monitor_task(
    task: ProxyServicePair,
    clash_config: ClashConfig,
    monitoring_config: MonitoringConfig,
    storage: NodeHistoryStorage,
    tui: MonitorTui,
) -> None:
    """Run a single periodic monitoring task."""
    async with ClashClient.from_external_controller(clash_config.controller, secret=clash_config.secret) as clash:
        rotations = 0
        is_new_proxy = True
        tui.event(task.service_name, f"开始监控 | 代理组: {task.proxy_group_name}")
        await tui.refresh_service(clash, task, storage)

        while True:
            async def after_switch(_node_name: str) -> None:
                await close_task_service_connections_best_effort(clash, task, tui.event)
                await tui.refresh_service(clash, task, storage)

            probe_func = probe_service_multi if is_new_proxy else probe_service
            if monitoring_config.once:
                result = await switch_until_service_available(
                    clash,
                    task,
                    clash_config,
                    storage,
                    probe_func=probe_func,
                    event_handler=tui.event,
                    after_switch=after_switch,
                )
                ok = result.ok
                switched = result.switched
            else:
                ok, switched = await probe_current_node_and_switch_if_unavailable(
                    clash,
                    task,
                    clash_config,
                    storage,
                    probe_func=probe_func,
                    event_handler=tui.event,
                )
                if switched:
                    await after_switch("")
            await tui.refresh_service(clash, task, storage)
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
                tui.event(task.service_name, f"暂停监控 | 已达到最大切换次数 ({monitoring_config.max_rotations})")
                rotations = 0
                await asyncio.sleep(max(monitoring_config.interval_sec, 30.0))
                continue

            if not switched:
                await asyncio.sleep(monitoring_config.interval_sec)


async def run_periodic_monitor_tasks(config: AppConfig) -> None:
    """Run periodic monitoring tasks concurrently."""
    storage = NodeHistoryStorage()
    storage.startup_cleanup()

    enabled_tasks = [task for task in config.tasks if task.enabled]
    if not enabled_tasks:
        print("没有启用的监控任务。")
        return

    with MonitorTui(enabled_tasks) as tui:
        tui.event("system", f"启动 {len(enabled_tasks)} 个监控任务")
        async def switch_node(task: ProxyServicePair, node_name: str) -> None:
            async with ClashClient.from_external_controller(
                config.clash.controller,
                secret=config.clash.secret,
            ) as clash:
                await switch_proxy_group_and_verify(clash, task.proxy_group_name, node_name)
                tui.event(task.service_name, f"手动切换 | {task.proxy_group_name} -> {node_name}")
                await close_task_service_connections_best_effort(clash, task, tui.event)
                await tui.refresh_service(clash, task, storage)

        tasks = [
            asyncio.create_task(
                run_periodic_monitor_task(task_config, config.clash, config.monitoring, storage, tui),
                name=task_config.service_name,
            )
            for task_config in enabled_tasks
        ]
        interaction_task = None
        if not config.monitoring.once:
            interaction_task = asyncio.create_task(tui.run_interaction(switch_node), name="tui_interaction")

        try:
            if interaction_task is None:
                await asyncio.gather(*tasks)
            else:
                done, pending = await asyncio.wait(
                    [*tasks, interaction_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for completed in done:
                    completed.result()
                for pending_task in pending:
                    pending_task.cancel()
                for pending_task in pending:
                    with suppress(asyncio.CancelledError):
                        await pending_task
        except Exception as e:
            tui.event("system", f"监控任务异常: {e}")
            for task in [*tasks, interaction_task]:
                if task is None:
                    continue
                if not task.done():
                    task.cancel()
            for task in [*tasks, interaction_task]:
                if task is None:
                    continue
                with suppress(asyncio.CancelledError):
                    await task
            raise
