import asyncio
from contextlib import suppress
from typing import Optional

import httpx

from clash_auto_switch.app_context import AppContext
from clash_auto_switch.config import add_task_to_config
from clash_auto_switch.core.check_scheduler import AdaptiveCheckScheduler, format_interval
from clash_auto_switch.core.clash_api import ClashClient, ClashLogEntry
from clash_auto_switch.core.clash_state import ClashProxyState
from clash_auto_switch.core.notifier import notify_user
from clash_auto_switch.core.services.registry import SERVICE_CHECKERS, SERVICE_HOST_PATTERNS
from clash_auto_switch.core.task import ServiceTask
from clash_auto_switch.defs import AppConfig, ProxyServicePair
from clash_auto_switch.tui import MonitorTui


LOG_RECONNECT_DELAY_SEC = 3.0
TUI_REFRESH_INTERVAL_SEC = 5.0
SELF_TRIGGER_PROCESSES = {"python.exe", "pythonw.exe"}


def match_auto_trigger_service(log_entry: ClashLogEntry) -> Optional[str]:
    if log_entry.source and log_entry.source.process:
        process = log_entry.source.process.lower()
        if process in SELF_TRIGGER_PROCESSES or process.startswith("python"):
            return None

    candidates = []
    if log_entry.destination:
        candidates.append(log_entry.destination.host)
    candidates.append(log_entry.payload)

    haystack = " ".join(value for value in candidates if value).lower()
    for service_name, patterns in SERVICE_HOST_PATTERNS.items():
        if any(pattern in haystack for pattern in patterns.trigger_hosts):
            return service_name
    return None


class AutoMonitorRunner:
    """Auto mode runner driven by realtime Clash logs."""

    def __init__(self, config: AppConfig) -> None:
        self.app = AppContext.initialize(config)
        self.config = self.app.config
        self.storage = self.app.storage
        self.enabled_tasks = [ServiceTask.from_pair(task, self.app) for task in config.tasks if task.enabled]
        self.watched_tasks = self._watched_tasks()
        self.tui = MonitorTui(self.watched_tasks or self.enabled_tasks)
        self.running_checks: dict[str, asyncio.Task] = {}
        self.diagnostics = self.app.diagnostics
        self.auto_detection_enabled: dict[str, bool] = {
            task.service_name: True for task in (self.watched_tasks or self.enabled_tasks)
        }
        self.check_scheduler: AdaptiveCheckScheduler = self.app.check_scheduler

    async def run(self) -> None:
        self.storage.startup_cleanup()
        self.tui.configure_callbacks(
            self.switch_node,
            self.disable_node,
            self.toggle_auto_detection,
            self.check_service,
            self.add_task,
        )
        async with ClashClient.from_external_controller(
            self.config.clash.controller,
            secret=self.config.clash.secret,
        ) as client:
            self.app.set_clash(ClashProxyState(client))
            try:
                clash = self.app.clash
                self.tui.set_add_task_options(
                    proxy_groups=await clash.list_proxy_group_names(),
                    services=self.addable_service_names(),
                )
                self.event("system", f"启动 auto 模式 | 监听 {len(self.watched_tasks)} 个服务")
                self.event("system", f"诊断日志: {self.diagnostics.path}")
                for task in self.watched_tasks:
                    self.tui.set_auto_detection_enabled(task, self.auto_detection_enabled.get(task.service_name, True))
                await self.refresh_all_services()
                await self.run_background_tasks()
            finally:
                self.app.clear_clash()

    def event(self, service_name: str, message: str, *, level: str = "info") -> None:
        self.diagnostics.write("event", service_name=service_name, message=message, level=level)
        self.tui.event(service_name, message, level=level)

    def _watched_tasks(self) -> list[ServiceTask]:
        tasks_by_service = {
            task.service_name: task
            for task in self.enabled_tasks
        }
        watched_services = sorted(set(tasks_by_service) & set(SERVICE_HOST_PATTERNS))
        return [tasks_by_service[service_name] for service_name in watched_services]

    @property
    def tasks_by_service(self) -> dict[str, ServiceTask]:
        return {
            task.service_name: task
            for task in self.watched_tasks
        }

    def addable_service_names(self) -> list[str]:
        configured = {task.service_name for task in self.watched_tasks}
        return sorted(
            service_name
            for service_name in SERVICE_CHECKERS
            if service_name in SERVICE_HOST_PATTERNS and service_name not in configured
        )

    async def run_background_tasks(self) -> None:
        tasks = [
            asyncio.create_task(self.tui.run_async(), name="tui"),
            asyncio.create_task(self.consume_logs(), name="auto_log_consumer"),
            asyncio.create_task(self.refresh_loop(), name="tui_refresh"),
        ]
        pending = set()
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for completed in done:
                completed.result()
        except Exception:
            self.tui.exit()
            raise
        finally:
            for task in [*pending, *self.running_checks.values()]:
                if not task.done():
                    task.cancel()
            for task in [*pending, *self.running_checks.values()]:
                with suppress(asyncio.CancelledError):
                    await task

    async def consume_logs(self) -> None:
        clash = self.app.clash
        while True:
            try:
                async for log_entry in clash.iter_logs(level="info"):
                    await self.handle_log_entry(log_entry)
                self.event("system", f"日志流结束，{LOG_RECONNECT_DELAY_SEC:.0f} 秒后重连")
            except httpx.HTTPError as exc:
                self.event("system", f"日志流断开，{LOG_RECONNECT_DELAY_SEC:.0f} 秒后重连 | {type(exc).__name__}")
            await asyncio.sleep(LOG_RECONNECT_DELAY_SEC)

    async def handle_log_entry(self, log_entry: ClashLogEntry) -> None:
        service_name = match_auto_trigger_service(log_entry)
        task = self.tasks_by_service.get(service_name or "")
        if service_name is None or task is None:
            return

        if not self.auto_detection_enabled.get(task.service_name, True):
            return

        running_task = self.running_checks.get(service_name)
        if running_task and not running_task.done():
            self.event(task.service_name, "跳过检测 | 上次检测仍在运行")
            self.diagnostics.write(
                "auto_trigger_skipped",
                service_name=task.service_name,
                reason="check_already_running",
                payload=log_entry.payload,
            )
            return

        if not self.check_scheduler.can_check(service_name):
            remaining = self.check_scheduler.remaining_sec(service_name)
            self.event(task.service_name, f"跳过检测 | 频率控制，剩余 {format_interval(remaining)}")
            self.diagnostics.write(
                "auto_trigger_skipped",
                service_name=task.service_name,
                reason="adaptive_interval",
                remaining_sec=remaining,
                interval_sec=self.check_scheduler.state(service_name).interval_sec,
                payload=log_entry.payload,
            )
            return

        host = log_entry.destination.host if log_entry.destination else "unknown"
        current_node = await self.current_node_for_task(task)
        self.event(task.service_name, f"触发检测 | 目标: {host} | 当前节点: {current_node or '未知'}")
        self.diagnostics.write(
            "auto_trigger",
            service_name=task.service_name,
            proxy_group_name=task.proxy_group_name,
            host=host,
            current_node=current_node,
            payload=log_entry.payload,
        )
        self.running_checks[service_name] = asyncio.create_task(
            self.run_auto_check(
                task,
                service_name,
            )
        )

    async def check_service(self, task: ServiceTask) -> None:
        service_name = task.service_name
        running_task = self.running_checks.get(service_name)
        if running_task and not running_task.done():
            self.event(task.service_name, "手动检测已跳过 | 自动检测仍在运行")
            return
        self.event(task.service_name, "手动触发检测")
        self.running_checks[service_name] = asyncio.create_task(
            self.run_auto_check(task, service_name, force=True)
        )

    async def run_auto_check(
        self,
        task: ServiceTask,
        service_name: str,
        *,
        force: bool = False,
    ) -> None:
        async def after_switch(_node_name: str) -> None:
            await task.close_connections_best_effort(self.event)
            await self.update_tui_service(task)
            await self.update_tui_connections_if_selected()

        before_node = await self.current_node_for_task(task)
        self.diagnostics.write(
            "auto_check_start",
            service_name=task.service_name,
            proxy_group_name=task.proxy_group_name,
            current_node=before_node,
            http_proxy=self.config.clash.http_proxy,
            force=force,
        )
        result = await task.switch_until_available(
            event_handler=self.event,
            after_switch=after_switch,
        )
        schedule = self.check_scheduler.record_result(service_name, result.ok and not result.switched)
        await self.update_tui_service(task)
        await self.update_tui_connections_if_selected()
        after_node = await self.current_node_for_task(task)
        self.diagnostics.write(
            "auto_check_end",
            service_name=task.service_name,
            proxy_group_name=task.proxy_group_name,
            before_node=before_node,
            after_node=after_node,
            ok=result.ok,
            switched=result.switched,
            attempts=result.attempts,
            next_interval_sec=schedule.interval_sec,
            success_streak=schedule.success_streak,
            failure_streak=schedule.failure_streak,
        )
        self.event(
            task.service_name,
            f"下次自动检测最短间隔: {format_interval(schedule.interval_sec)}",
        )

        if result.switched:
            notify_user("Clash Auto Switch", f"{task.service_name} 不可用，已切换 {task.proxy_group_name}")

    async def toggle_auto_detection(self, task: ServiceTask, enabled: bool) -> None:
        self.auto_detection_enabled[task.service_name] = enabled
        self.tui.set_auto_detection_enabled(task, enabled)
        status = "开启" if enabled else "关闭"
        self.event(task.service_name, f"{status}自动检测与自动切换")
        self.diagnostics.write(
            "auto_detection_toggled",
            service_name=task.service_name,
            proxy_group_name=task.proxy_group_name,
            enabled=enabled,
        )

    async def add_task(self, proxy_group_name: str, service_name: str) -> Optional[ServiceTask]:
        if service_name not in SERVICE_CHECKERS or service_name not in SERVICE_HOST_PATTERNS:
            self.event("system", f"未知或不可自动触发的服务: {service_name}")
            return None
        if any(task.service_name == service_name for task in self.watched_tasks):
            self.event(service_name, "添加任务已跳过 | 服务已存在")
            return None

        pair = ProxyServicePair(proxy_group_name, service_name, enabled=True)
        if not add_task_to_config(self.config, pair):
            self.event(service_name, "添加任务失败 | 配置保存失败")
            return None

        task = ServiceTask.from_pair(pair, self.app)
        self.enabled_tasks.append(task)
        self.watched_tasks.append(task)
        self.watched_tasks.sort(key=lambda item: item.service_name)
        self.auto_detection_enabled[task.service_name] = True
        self.tui.set_add_task_options(services=self.addable_service_names())
        self.tui.set_auto_detection_enabled(task, True)
        self.diagnostics.write(
            "task_added",
            service_name=task.service_name,
            proxy_group_name=task.proxy_group_name,
        )
        await self.update_tui_service(task)
        await self.update_tui_connections_if_selected()
        return task

    async def switch_node(self, task: ServiceTask, node_name: str) -> None:
        service_name = task.service_name
        running_task = self.running_checks.get(service_name)
        if running_task and not running_task.done():
            self.event(task.service_name, "手动切换已跳过 | 自动检测仍在运行")
            self.diagnostics.write(
                "manual_switch_skipped",
                service_name=task.service_name,
                proxy_group_name=task.proxy_group_name,
                node_name=node_name,
                reason="auto_check_running",
            )
            return

        await task.switch_to_node(node_name)
        self.event(task.service_name, f"手动切换 | {task.proxy_group_name} -> {node_name}")
        await task.close_connections_best_effort(self.event)

        probe_result = await task.probe_current_node_once(
            event_handler=self.event,
        )
        ok = probe_result.ok
        status_text = probe_result.status_text
        schedule = self.check_scheduler.record_result(service_name, ok)
        status = "服务可用" if ok else "服务不可用"
        self.event(task.service_name, f"{status} | {status_text} | 节点: {node_name}")
        self.event(task.service_name, f"下次自动检测最短间隔: {format_interval(schedule.interval_sec)}")
        self.diagnostics.write(
            "manual_switch_probe",
            service_name=task.service_name,
            proxy_group_name=task.proxy_group_name,
            node_name=node_name,
            ok=ok,
            status_text=status_text,
            next_interval_sec=schedule.interval_sec,
        )
        await self.update_tui_service(task)
        await self.update_tui_connections(task)

    async def disable_node(self, task: ServiceTask, node_name: str) -> None:
        was_disabled = node_name in task.disabled_node_names()
        if not task.toggle_node_disabled(node_name):
            self.event(task.service_name, f"切换禁用状态失败 | {node_name}")
            return
        action = "取消禁用节点" if was_disabled else "禁用节点"
        self.event(task.service_name, f"{action} | {node_name}")
        await self.update_tui_service(task)
        await self.update_tui_connections_if_selected()

    async def refresh_loop(self) -> None:
        while True:
            await self.refresh_all_services()
            await asyncio.sleep(TUI_REFRESH_INTERVAL_SEC)

    async def refresh_all_services(self) -> None:
        for task in self.watched_tasks:
            await self.update_tui_service(task)
        await self.update_tui_connections_if_selected()

    async def update_tui_service(self, task: ServiceTask) -> None:
        clash = self.app.clash
        try:
            group_state = await clash.get_proxy_group(task.proxy_group_name)
        except Exception as exc:
            self.event(task.service_name, f"读取代理组失败: {exc}", level="warning")
            return

        self.tui.update_service(
            task,
            group_state,
            self.storage,
            disabled_node_names=task.disabled_node_names(),
        )

    async def update_tui_connections_if_selected(self) -> None:
        task = self.tui.selected_task()
        if task is None:
            return
        await self.update_tui_connections(task)

    async def update_tui_connections(self, task: ServiceTask) -> None:
        clash = self.app.clash
        try:
            connections_payload = await clash.get_connections()
        except Exception as exc:
            self.tui.update_connections(task, error=str(exc))
            self.diagnostics.write(
                "connections_read_failed",
                service_name=task.service_name,
                proxy_group_name=task.proxy_group_name,
                error=str(exc),
            )
            return

        self.tui.update_connections(task, connections_payload)
        connections = connections_payload.get("connections") or []
        self.diagnostics.write(
            "connections_snapshot",
            service_name=task.service_name,
            proxy_group_name=task.proxy_group_name,
            total_connections=len(connections) if isinstance(connections, list) else None,
        )

    async def current_node_for_task(self, task: ServiceTask) -> Optional[str]:
        return await task.current_node()
