import asyncio
import math
import time
from contextlib import suppress
from typing import Optional

import httpx

from clash_auto_switch.app_context import AppContext
from clash_auto_switch.config import add_task_to_config
from clash_auto_switch.core.clash_api import ClashApi, ClashLogEntry
from clash_auto_switch.core.clash_api_raw import ClashClientRaw
from clash_auto_switch.core.notifier import notify_user
from clash_auto_switch.core.services.registry import get_service, get_all_services
from clash_auto_switch.core.task import ServiceTaskRuntime
from clash_auto_switch.defs import AppConfig, ProxyServicePair
from clash_auto_switch.tui import MonitorTui


LOG_RECONNECT_DELAY_SEC = 3.0
TUI_REFRESH_INTERVAL_SEC = 5.0
CONNECTIVITY_REFRESH_INTERVAL_SEC = 300.0
CONNECTIVITY_TEST_URL = "https://cp.cloudflare.com/generate_204"
CONNECTIVITY_TEST_TIMEOUT_MS = 5000
PERIODIC_CHECK_IDLE_DELAY_SEC = 5.0
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
    for service in get_all_services():
        if service.trigger_mode != "traffic":
            continue
        patterns = service.host_patterns
        if patterns is None:
            continue
        if any(pattern in haystack for pattern in patterns.trigger_hosts):
            return service.service_name
    return None


class AutoMonitorRunner:
    """Auto mode runner driven by realtime Clash logs."""

    def __init__(self, config: AppConfig) -> None:
        self.app = AppContext.initialize(config)
        self.config = self.app.config
        self.storage = self.app.storage
        self.tasks = [ServiceTaskRuntime.from_pair(task, self.app) for task in config.tasks if task.enabled]
        self._validate_configured_tasks()
        self.tui = MonitorTui(self.tasks)
        self.diagnostics = self.app.diagnostics
        self._periodic_next_check_at: dict[str, float] = {}

    def _validate_configured_tasks(self) -> None:
        missing_services = []
        for task in self.tasks:
            try:
                get_service(task.service_name)
            except KeyError:
                missing_services.append(task.service_name)
        if missing_services:
            missing = ", ".join(sorted(missing_services))
            raise RuntimeError(f"配置中的 task 找不到对应的 service: {missing}")

    async def run(self) -> None:
        self.storage.startup_cleanup()
        self.tui.configure_callbacks(
            self.tui_switch_node,
            self.tui_disable_node,
            self.tui_toggle_auto_detection,
            self.tui_check_service,
            self.tui_add_task,
        )
        async with ClashClientRaw.from_external_controller(
            self.config.clash.controller,
            secret=self.config.clash.secret,
        ) as client:
            self.app.set_clash(ClashApi(client))
            try:
                clash = self.app.clash
                self.tui.set_add_task_options(
                    proxy_groups=await clash.list_proxy_group_names(),
                    services=self.addable_service_names(),
                )
                self.event("system", f"启动 auto 模式 | 监听 {len(self.tasks)} 个服务")
                self.event("system", f"诊断日志: {self.diagnostics.path}")
                for task in self.tasks:
                    self.tui.set_auto_detection_enabled(task, task.auto_detection_enabled)
                await self.refresh_all_services()
                await self.refresh_all_connectivity()
                await self.run_background_tasks()
            finally:
                self.app.clear_clash()

    def event(self, service_name: str, message: str, *, level: str = "info") -> None:
        self.diagnostics.write("event", service_name=service_name, message=message, level=level)
        self.tui.event(service_name, message, level=level)

    @property
    def tasks_by_service(self) -> dict[str, ServiceTaskRuntime]:
        return {
            task.service_name: task
            for task in self.tasks
        }

    def addable_service_names(self) -> list[str]:
        configured = {task.service_name for task in self.tasks}
        return sorted(
            service.service_name
            for service in get_all_services()
            if service.service_name not in configured
        )

    async def run_background_tasks(self) -> None:
        tasks = [
            asyncio.create_task(self.tui.run_async(), name="tui"),
            asyncio.create_task(self.consume_logs(), name="auto_log_consumer"),
            asyncio.create_task(self.periodic_service_check_loop(), name="periodic_service_check"),
            asyncio.create_task(self.refresh_loop(), name="tui_refresh"),
            asyncio.create_task(self.connectivity_refresh_loop(), name="connectivity_refresh"),
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
            for task in pending:
                if not task.done():
                    task.cancel()
            for task in pending:
                with suppress(asyncio.CancelledError):
                    await task

    async def consume_logs(self) -> None:
        clash = self.app.clash
        while True:
            try:
                async for log_entry in clash.iter_logs(level="info"):
                    await self.consume_log_entry(log_entry)
                self.event("system", f"日志流结束，{LOG_RECONNECT_DELAY_SEC:.0f} 秒后重连")
            except httpx.HTTPError as exc:
                self.event("system", f"日志流断开，{LOG_RECONNECT_DELAY_SEC:.0f} 秒后重连 | {type(exc).__name__}")
            await asyncio.sleep(LOG_RECONNECT_DELAY_SEC)

    async def consume_log_entry(self, log_entry: ClashLogEntry) -> None:
        service_name = match_auto_trigger_service(log_entry)
        if not service_name:
            return

        task = self.tasks_by_service.get(service_name)
        if not (task and task.auto_detection_enabled):
            return

        if task.is_check_running:
            self.event(task.service_name, "跳过检测 | 上次检测仍在运行")
            self.diagnostics.write(
                "auto_trigger_skipped",
                service_name=task.service_name,
                reason="check_already_running",
                payload=log_entry.payload,
            )
            return

        active_connection_count = await self.active_connection_count(task)
        if active_connection_count > 0:
            self.event(task.service_name, f"跳过检测 | 服务正在使用中，活动连接 {active_connection_count} 个")
            self.diagnostics.write(
                "auto_trigger_skipped",
                service_name=task.service_name,
                reason="service_active",
                active_connection_count=active_connection_count,
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
        task.track_running_check(asyncio.create_task(self.check_and_switch_service(task)))

    async def periodic_service_check_loop(self) -> None:
        while True:
            now = time.monotonic()
            periodic_tasks = [
                task
                for task in self.tasks
                if get_service(task.service_name).trigger_mode == "periodic"
            ]
            if not periodic_tasks:
                await asyncio.sleep(PERIODIC_CHECK_IDLE_DELAY_SEC)
                continue

            next_due_in = math.inf
            for task in periodic_tasks:
                next_check_at = self._periodic_next_check_at.get(task.service_name, 0.0)
                if now < next_check_at:
                    next_due_in = min(next_due_in, max(next_check_at - now, 0.0))
                    continue

                await self.maybe_run_periodic_service_check(task)
                interval = max(
                    get_service(task.service_name).periodic_interval_sec,
                    PERIODIC_CHECK_IDLE_DELAY_SEC,
                )
                self._periodic_next_check_at[task.service_name] = time.monotonic() + interval
                next_due_in = min(next_due_in, interval)
            await asyncio.sleep(
                PERIODIC_CHECK_IDLE_DELAY_SEC
                if math.isinf(next_due_in)
                else max(next_due_in, PERIODIC_CHECK_IDLE_DELAY_SEC)
            )

    async def maybe_run_periodic_service_check(self, task: ServiceTaskRuntime) -> None:
        if not task.auto_detection_enabled:
            return
        if task.is_check_running:
            self.event(task.service_name, "跳过定时检测 | 上次检测仍在运行")
            self.diagnostics.write(
                "periodic_check_skipped",
                service_name=task.service_name,
                proxy_group_name=task.proxy_group_name,
                reason="check_already_running",
            )
            return

        current_node = await self.current_node_for_task(task)
        self.event(task.service_name, f"定时检测 | 当前节点: {current_node or '未知'}")
        self.diagnostics.write(
            "periodic_check",
            service_name=task.service_name,
            proxy_group_name=task.proxy_group_name,
            current_node=current_node,
        )
        task.track_running_check(asyncio.create_task(self.check_and_switch_service(task)))

    async def tui_check_service(self, task: ServiceTaskRuntime) -> None:
        if task.is_check_running:
            self.event(task.service_name, "手动检测已跳过 | 自动检测仍在运行")
            return
        self.event(task.service_name, "手动触发检测")
        task.track_running_check(asyncio.create_task(self.check_and_switch_service(task, force=True)))

    async def check_and_switch_service(
        self,
        task: ServiceTaskRuntime,
        *,
        force: bool = False,
    ) -> None:
        service = get_service(task.service_name)

        async def after_switch(_node_name: str) -> None:
            await task.close_connections_best_effort(self.event)
            await self.update_tui_service(task)
            if service.close_connections_on_switch:
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
        )

        if result.switched:
            notify_user("Clash Auto Switch", f"{task.service_name} 不可用，已切换 {task.proxy_group_name}")

    async def tui_toggle_auto_detection(self, task: ServiceTaskRuntime, enabled: bool) -> None:
        task.set_auto_detection_enabled(enabled)
        self.tui.set_auto_detection_enabled(task, enabled)
        status = "开启" if enabled else "关闭"
        self.event(task.service_name, f"{status}自动检测与自动切换")
        self.diagnostics.write(
            "auto_detection_toggled",
            service_name=task.service_name,
            proxy_group_name=task.proxy_group_name,
            enabled=enabled,
        )

    async def tui_add_task(self, proxy_group_name: str, service_name: str) -> Optional[ServiceTaskRuntime]:
        try:
            get_service(service_name)
        except KeyError:
            self.event("system", f"未知或不可自动触发的服务: {service_name}")
            return None
        if any(task.service_name == service_name for task in self.tasks):
            self.event(service_name, "添加任务已跳过 | 服务已存在")
            return None

        pair = ProxyServicePair(proxy_group_name, service_name, enabled=True)
        if not add_task_to_config(self.config, pair):
            self.event(service_name, "添加任务失败 | 配置保存失败")
            return None

        task = ServiceTaskRuntime.from_pair(pair, self.app)
        self.tasks.append(task)
        self.tasks.sort(key=lambda item: item.service_name)
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

    async def tui_switch_node(self, task: ServiceTaskRuntime, node_name: str) -> None:
        if task.is_check_running:
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
        status = "服务可用" if ok else "服务不可用"
        self.event(task.service_name, f"{status} | {status_text} | 节点: {node_name}")
        self.diagnostics.write(
            "manual_switch_probe",
            service_name=task.service_name,
            proxy_group_name=task.proxy_group_name,
            node_name=node_name,
            ok=ok,
            status_text=status_text,
        )
        await self.update_tui_service(task)
        await self.update_tui_connections(task)

    async def tui_disable_node(self, task: ServiceTaskRuntime, node_name: str) -> None:
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
        for task in self.tasks:
            await self.update_tui_service(task)
        await self.update_tui_connections_if_selected()

    async def connectivity_refresh_loop(self) -> None:
        await asyncio.sleep(CONNECTIVITY_REFRESH_INTERVAL_SEC)
        while True:
            await self.refresh_all_connectivity()
            await asyncio.sleep(CONNECTIVITY_REFRESH_INTERVAL_SEC)

    async def refresh_all_connectivity(self) -> None:
        clash = self.app.clash
        task_nodes, unique_nodes = await self._collect_task_nodes(clash)
        if not unique_nodes:
            return
        node_status = await self._probe_nodes_connectivity(clash, unique_nodes)
        self._dispatch_connectivity(task_nodes, node_status)

    async def _collect_task_nodes(
        self,
        clash: ClashApi,
    ) -> tuple[dict[str, list[str]], list[str]]:
        """Fetch each unique proxy group once and return per-task node lists plus deduped nodes."""
        unique_groups = {task.proxy_group_name for task in self.tasks}
        group_results = await asyncio.gather(
            *(clash.get_proxy_group(name) for name in unique_groups),
            return_exceptions=True,
        )
        group_nodes: dict[str, list[str]] = {}
        for name, result in zip(unique_groups, group_results):
            if isinstance(result, Exception):
                self.event("system", f"连通性刷新跳过 | 读取代理组失败 {name}: {result}", level="warning")
                continue
            group_nodes[name] = result.nodes

        task_nodes: dict[str, list[str]] = {}
        seen: set[str] = set()
        unique_nodes: list[str] = []
        for task in self.tasks:
            nodes = group_nodes.get(task.proxy_group_name)
            if nodes is None:
                continue
            task_nodes[task.service_name] = nodes
            for name in nodes:
                if name not in seen:
                    seen.add(name)
                    unique_nodes.append(name)
        return task_nodes, unique_nodes

    def _dispatch_connectivity(
        self,
        task_nodes: dict[str, list[str]],
        node_status: dict[str, bool],
    ) -> None:
        for node_name, ok in node_status.items():
            self.storage.record_node_connectivity(node_name, ok)
        for task in self.tasks:
            nodes = task_nodes.get(task.service_name)
            if nodes is None:
                continue
            task_status = {name: node_status[name] for name in nodes if name in node_status}
            self.tui.update_connectivity(task, task_status)
            failed = sum(1 for ok in task_status.values() if not ok)
            if failed:
                self.event(
                    task.service_name,
                    f"连通性刷新 | {len(task_status) - failed}/{len(task_status)} 正常, {failed} 失败",
                )

    async def _probe_nodes_connectivity(
        self,
        clash: ClashApi,
        nodes: list[str],
    ) -> dict[str, bool]:
        results = await asyncio.gather(
            *(self._probe_single_node(clash, name) for name in nodes),
            return_exceptions=True,
        )
        status: dict[str, bool] = {}
        for name, result in zip(nodes, results):
            status[name] = False if isinstance(result, Exception) else result
        return status

    async def _probe_single_node(self, clash: ClashApi, name: str) -> bool:
        try:
            result = await clash.get_proxy_delay(
                name,
                CONNECTIVITY_TEST_URL,
                CONNECTIVITY_TEST_TIMEOUT_MS,
            )
        except httpx.HTTPError:
            return False
        delay = result.get("delay")
        return isinstance(delay, (int, float)) and delay >= 0

    async def update_tui_service(self, task: ServiceTaskRuntime) -> None:
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

    async def update_tui_connections(self, task: ServiceTaskRuntime) -> None:
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

    async def current_node_for_task(self, task: ServiceTaskRuntime) -> Optional[str]:
        return await task.current_node()

    async def active_connection_count(self, task: ServiceTaskRuntime) -> int:
        clash = self.app.clash
        try:
            return await clash.count_active_service_connections(task.service_name)
        except Exception as exc:
            self.diagnostics.write(
                "active_connections_read_failed",
                service_name=task.service_name,
                proxy_group_name=task.proxy_group_name,
                error=str(exc),
            )
            return 0
