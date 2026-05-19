import asyncio
from contextlib import suppress

from clash_auto_switch.config import disable_node_for_task, disabled_node_names_for_task
from clash_auto_switch.core.clash_api import ClashClient
from clash_auto_switch.core.clash_state import ClashProxyState
from clash_auto_switch.core.connections import close_task_service_connections_best_effort
from clash_auto_switch.core.proxy_switcher import (
    probe_current_node_and_switch_if_unavailable,
    switch_proxy_group_and_verify,
    switch_until_service_available,
)
from clash_auto_switch.core.service_tester import probe_service, probe_service_multi
from clash_auto_switch.core.storage import NodeHistoryStorage
from clash_auto_switch.defs import AppConfig, ProxyServicePair
from clash_auto_switch.tui import MonitorTui


TUI_REFRESH_INTERVAL_SEC = 5.0


class PeriodicMonitorRunner:
    """Periodic service monitor runner."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.storage = NodeHistoryStorage()
        self.enabled_tasks = [task for task in config.tasks if task.enabled]
        self.tui = MonitorTui(self.enabled_tasks)

    async def run(self) -> None:
        self.storage.startup_cleanup()
        if not self.enabled_tasks:
            print("没有启用的监控任务。")
            return

        self.tui.configure_callbacks(self.switch_node, self.disable_node)
        await self.run_background_tasks()

    async def run_background_tasks(self) -> None:
        monitor_tasks = [
            asyncio.create_task(self.run_task(task), name=task.service_name)
            for task in self.enabled_tasks
        ]
        tui_task = asyncio.create_task(self.tui.run_async(), name="tui")
        self.tui.event("system", f"启动 {len(self.enabled_tasks)} 个监控任务")

        refresh_task = None
        if not self.config.monitoring.once:
            refresh_task = asyncio.create_task(self.refresh_loop(), name="tui_refresh")

        try:
            if self.config.monitoring.once:
                await asyncio.gather(*monitor_tasks)
                self.tui.exit()
                with suppress(asyncio.CancelledError):
                    await tui_task
                return

            active_tasks = [*monitor_tasks, tui_task]
            if refresh_task is not None:
                active_tasks.append(refresh_task)
            done, pending = await asyncio.wait(
                active_tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for completed in done:
                completed.result()
            for task in pending:
                task.cancel()
            for task in pending:
                with suppress(asyncio.CancelledError):
                    await task
        except Exception as exc:
            self.tui.event("system", f"监控任务异常: {exc}")
            self.tui.exit()
            for task in [*monitor_tasks, refresh_task, tui_task]:
                if task is not None and not task.done():
                    task.cancel()
            for task in [*monitor_tasks, refresh_task, tui_task]:
                if task is not None:
                    with suppress(asyncio.CancelledError):
                        await task
            raise

    async def run_task(self, task: ProxyServicePair) -> None:
        async with self.open_clash() as clash:
            rotations = 0
            is_new_proxy = True
            self.tui.event(task.service_name, f"开始监控 | 代理组: {task.proxy_group_name}")
            await self.update_tui_service(clash, task)
            await self.update_tui_connections_if_selected(clash)

            while True:
                probe_func = probe_service_multi if is_new_proxy else probe_service
                ok, switched = await self.check_task(clash, task, probe_func)
                await self.update_tui_service(clash, task)
                await self.update_tui_connections_if_selected(clash)
                is_new_proxy = False

                if switched:
                    rotations += 1
                elif ok:
                    rotations = 0

                if self.config.monitoring.once:
                    return
                if ok:
                    await asyncio.sleep(self.config.monitoring.interval_sec)
                    continue
                if self.should_pause_after_rotations(rotations):
                    self.tui.event(task.service_name, f"暂停监控 | 已达到最大切换次数 ({self.config.monitoring.max_rotations})")
                    rotations = 0
                    await asyncio.sleep(max(self.config.monitoring.interval_sec, 30.0))
                    continue
                if not switched:
                    await asyncio.sleep(self.config.monitoring.interval_sec)

    async def check_task(self, clash: ClashProxyState, task: ProxyServicePair, probe_func) -> tuple[bool, bool]:
        async def after_switch(_node_name: str) -> None:
            await close_task_service_connections_best_effort(clash, task, self.tui.event)
            await self.update_tui_service(clash, task)
            await self.update_tui_connections_if_selected(clash)

        if self.config.monitoring.once:
            result = await switch_until_service_available(
                clash,
                task,
                self.config.clash,
                self.storage,
                probe_func=probe_func,
                disabled_node_names=disabled_node_names_for_task(self.config, task),
                event_handler=self.tui.event,
                after_switch=after_switch,
            )
            return result.ok, result.switched

        ok, switched = await probe_current_node_and_switch_if_unavailable(
            clash,
            task,
            self.config.clash,
            self.storage,
            probe_func=probe_func,
            disabled_node_names=disabled_node_names_for_task(self.config, task),
            event_handler=self.tui.event,
        )
        if switched:
            await after_switch("")
        return ok, switched

    def should_pause_after_rotations(self, rotations: int) -> bool:
        return self.config.monitoring.max_rotations > 0 and rotations >= self.config.monitoring.max_rotations

    async def switch_node(self, task: ProxyServicePair, node_name: str) -> None:
        async with self.open_clash() as clash:
            await switch_proxy_group_and_verify(clash, task.proxy_group_name, node_name)
            self.tui.event(task.service_name, f"手动切换 | {task.proxy_group_name} -> {node_name}")
            await close_task_service_connections_best_effort(clash, task, self.tui.event)

            ok, status_text = await probe_service(task.service_name, self.config.clash.http_proxy)
            self.storage.record_node_status(node_name, task.service_name, task.proxy_group_name, ok)
            status = "服务可用" if ok else "服务不可用"
            self.tui.event(task.service_name, f"{status} | {status_text} | 节点: {node_name}")
            await self.update_tui_service(clash, task)
            await self.update_tui_connections(clash, task)

    async def disable_node(self, task: ProxyServicePair, node_name: str) -> None:
        if not disable_node_for_task(self.config, task, node_name):
            self.tui.event(task.service_name, f"禁用节点失败 | {node_name}")
            return
        self.tui.event(task.service_name, f"禁用节点 | {node_name}")
        async with self.open_clash() as clash:
            await self.update_tui_service(clash, task)
            await self.update_tui_connections_if_selected(clash)

    async def refresh_loop(self) -> None:
        async with self.open_clash() as clash:
            while True:
                for task in self.enabled_tasks:
                    await self.update_tui_service(clash, task)
                await self.update_tui_connections_if_selected(clash)
                await asyncio.sleep(TUI_REFRESH_INTERVAL_SEC)

    async def update_tui_service(self, clash: ClashProxyState, task: ProxyServicePair) -> None:
        try:
            group_state = await clash.get_proxy_group(task.proxy_group_name)
        except Exception as exc:
            self.tui.event(task.service_name, f"读取代理组失败: {exc}", level="warning")
            return

        self.tui.update_service(
            task,
            group_state,
            self.storage,
            disabled_node_names=disabled_node_names_for_task(self.config, task),
        )

    async def update_tui_connections_if_selected(self, clash: ClashProxyState) -> None:
        task = self.tui.selected_task()
        if task is None:
            return
        await self.update_tui_connections(clash, task)

    async def update_tui_connections(self, clash: ClashProxyState, task: ProxyServicePair) -> None:
        try:
            connections_payload = await clash.get_connections()
        except Exception as exc:
            self.tui.update_connections(task, error=str(exc))
            return

        self.tui.update_connections(task, connections_payload)

    def open_clash(self):
        return ClashStateContext(self.config)


class ClashStateContext:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.client = None

    async def __aenter__(self) -> ClashProxyState:
        self.client = ClashClient.from_external_controller(
            self.config.clash.controller,
            secret=self.config.clash.secret,
        )
        await self.client.__aenter__()
        return ClashProxyState(self.client)

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        if self.client is not None:
            await self.client.__aexit__(exc_type, exc, traceback)
