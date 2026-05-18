import asyncio
import time
from contextlib import suppress
from typing import Optional

import httpx

from clash_auto_switch.config import disable_node_for_task, disabled_node_names_for_task
from clash_auto_switch.core.clash_api import ClashClient, ClashLogEntry
from clash_auto_switch.core.clash_state import ClashProxyState
from clash_auto_switch.core.connections import close_task_service_connections_best_effort
from clash_auto_switch.core.notifier import notify_user
from clash_auto_switch.core.proxy_switcher import switch_proxy_group_and_verify, switch_until_service_available
from clash_auto_switch.core.service_tester import normalize_service_name, probe_service
from clash_auto_switch.core.storage import NodeHistoryStorage
from clash_auto_switch.defs import AppConfig, ProxyServicePair
from clash_auto_switch.tui import MonitorTui


AUTO_SWITCH_COOLDOWN_SEC = 60.0
LOG_RECONNECT_DELAY_SEC = 3.0
TUI_REFRESH_INTERVAL_SEC = 5.0

SERVICE_LOG_HOST_PATTERNS = {
    "bilibili_mainland": ("bilibili.com", "bilibili.cn", "bilivideo.com", "biligame.net"),
    "bilibili_hk_mc_tw": ("bilibili.com", "bilibili.tv", "bilivideo.com"),
    "chatgpt": ("chat.openai.com", "chatgpt.com", "api.openai.com", "oaistatic.com", "oaiusercontent.com"),
    "claude": ("claude.ai", "anthropic.com"),
    "gemini": ("gemini.google.com", "generativelanguage.googleapis.com", "aistudio.google.com", "ai.google.dev"),
    "youtube_music": ("music.youtube.com", "youtubei.googleapis.com"),
    "youtube_premium": ("youtube.com", "www.youtube.com", "m.youtube.com", "youtubei.googleapis.com", "googlevideo.com", "ytimg.com"),
    "bahamut_anime": ("ani.gamer.com.tw", "gamer.com.tw"),
    "netflix": ("netflix.com", "nflxvideo.net", "nflximg.net", "nflxext.com", "fast.com"),
    "disney_plus": ("disneyplus.com", "disney.api.edge.bamgrid.com", "bamgrid.com", "disney-plus.net"),
    "prime_video": ("primevideo.com", "amazonvideo.com", "media-amazon.com", "pv-cdn.net"),
}


def match_auto_trigger_service(log_entry: ClashLogEntry) -> Optional[str]:
    candidates = []
    if log_entry.destination:
        candidates.append(log_entry.destination.host)
    candidates.append(log_entry.payload)

    haystack = " ".join(value for value in candidates if value).lower()
    for service_name, patterns in SERVICE_LOG_HOST_PATTERNS.items():
        if any(pattern in haystack for pattern in patterns):
            return service_name
    return None


class AutoMonitorRunner:
    """Auto mode runner driven by realtime Clash logs."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.storage = NodeHistoryStorage()
        self.enabled_tasks = [task for task in config.tasks if task.enabled]
        self.watched_tasks = self._watched_tasks()
        self.tui = MonitorTui(self.watched_tasks or self.enabled_tasks)
        self.clash: ClashProxyState | None = None
        self.last_switch_at: dict[str, float] = {}
        self.running_checks: dict[str, asyncio.Task] = {}

    async def run(self) -> None:
        self.storage.startup_cleanup()
        if not self.watched_tasks:
            print("没有可自动触发的启用任务。")
            return

        with self.tui:
            async with ClashClient.from_external_controller(
                self.config.clash.controller,
                secret=self.config.clash.secret,
            ) as client:
                self.clash = ClashProxyState(client)
                self.tui.event("system", f"启动 auto 模式 | 监听 {len(self.watched_tasks)} 个服务")
                await self.refresh_all_services()
                await self.run_background_tasks()

    def _watched_tasks(self) -> list[ProxyServicePair]:
        tasks_by_service = {
            normalize_service_name(task.service_name): task
            for task in self.enabled_tasks
        }
        watched_services = sorted(set(tasks_by_service) & set(SERVICE_LOG_HOST_PATTERNS))
        return [tasks_by_service[service_name] for service_name in watched_services]

    @property
    def tasks_by_service(self) -> dict[str, ProxyServicePair]:
        return {
            normalize_service_name(task.service_name): task
            for task in self.watched_tasks
        }

    async def run_background_tasks(self) -> None:
        tasks = [
            asyncio.create_task(self.consume_logs(), name="auto_log_consumer"),
            asyncio.create_task(self.tui.run_interaction(self.switch_node, self.disable_node), name="tui_interaction"),
            asyncio.create_task(self.refresh_loop(), name="tui_refresh"),
        ]
        pending: set[asyncio.Task] = set()
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for completed in done:
                completed.result()
        finally:
            for task in [*pending, *self.running_checks.values()]:
                if not task.done():
                    task.cancel()
            for task in [*pending, *self.running_checks.values()]:
                with suppress(asyncio.CancelledError):
                    await task

    async def consume_logs(self) -> None:
        assert self.clash is not None
        while True:
            try:
                async for log_entry in self.clash.iter_logs(level="info"):
                    await self.handle_log_entry(log_entry)
                self.tui.event("system", f"日志流结束，{LOG_RECONNECT_DELAY_SEC:.0f} 秒后重连")
            except httpx.HTTPError as exc:
                self.tui.event("system", f"日志流断开，{LOG_RECONNECT_DELAY_SEC:.0f} 秒后重连 | {type(exc).__name__}")
            await asyncio.sleep(LOG_RECONNECT_DELAY_SEC)

    async def handle_log_entry(self, log_entry: ClashLogEntry) -> None:
        service_name = match_auto_trigger_service(log_entry)
        task = self.tasks_by_service.get(service_name or "")
        if service_name is None or task is None:
            return

        running_task = self.running_checks.get(service_name)
        if running_task and not running_task.done():
            self.tui.event(task.service_name, "跳过检测 | 上次检测仍在运行")
            return

        host = log_entry.destination.host if log_entry.destination else "unknown"
        switch_allowed, switch_block_reason = self.switch_cooldown_state(service_name)
        self.tui.event(task.service_name, f"触发检测 | 目标: {host}")
        self.running_checks[service_name] = asyncio.create_task(
            self.run_auto_check(
                task,
                service_name,
                switch_allowed=switch_allowed,
                switch_block_reason=switch_block_reason,
            )
        )

    def switch_cooldown_state(self, service_name: str) -> tuple[bool, Optional[str]]:
        switch_elapsed = time.monotonic() - self.last_switch_at.get(service_name, 0.0)
        if switch_elapsed >= AUTO_SWITCH_COOLDOWN_SEC:
            return True, None
        remaining = AUTO_SWITCH_COOLDOWN_SEC - switch_elapsed
        return False, f"切换冷却中，剩余 {remaining:.0f} 秒"

    async def run_auto_check(
        self,
        task: ProxyServicePair,
        service_name: str,
        *,
        switch_allowed: bool,
        switch_block_reason: Optional[str],
    ) -> None:
        assert self.clash is not None

        async def after_switch(_node_name: str) -> None:
            assert self.clash is not None
            await close_task_service_connections_best_effort(self.clash, task, self.tui.event)
            await self.update_tui_service(task)

        result = await switch_until_service_available(
            self.clash,
            task,
            self.config.clash,
            self.storage,
            switch_allowed=switch_allowed,
            switch_block_reason=switch_block_reason,
            disabled_node_names=disabled_node_names_for_task(self.config, task),
            event_handler=self.tui.event,
            after_switch=after_switch,
        )
        await self.update_tui_service(task)

        if result.switched:
            self.last_switch_at[service_name] = time.monotonic()
            notify_user("Clash Auto Switch", f"{task.service_name} 不可用，已切换 {task.proxy_group_name}")

    async def switch_node(self, task: ProxyServicePair, node_name: str) -> None:
        assert self.clash is not None
        await switch_proxy_group_and_verify(self.clash, task.proxy_group_name, node_name)
        self.tui.event(task.service_name, f"手动切换 | {task.proxy_group_name} -> {node_name}")
        await close_task_service_connections_best_effort(self.clash, task, self.tui.event)

        ok, status_text = await probe_service(task.service_name, self.config.clash.http_proxy)
        self.storage.record_node_status(node_name, task.service_name, task.proxy_group_name, ok)
        status = "服务可用" if ok else "服务不可用"
        self.tui.event(task.service_name, f"{status} | {status_text} | 节点: {node_name}")
        await self.update_tui_service(task)

    async def disable_node(self, task: ProxyServicePair, node_name: str) -> None:
        if not disable_node_for_task(self.config, task, node_name):
            self.tui.event(task.service_name, f"禁用节点失败 | {node_name}")
            return
        self.tui.event(task.service_name, f"禁用节点 | {node_name}")
        await self.update_tui_service(task)

    async def refresh_loop(self) -> None:
        while True:
            await self.refresh_all_services()
            await asyncio.sleep(TUI_REFRESH_INTERVAL_SEC)

    async def refresh_all_services(self) -> None:
        for task in self.watched_tasks:
            await self.update_tui_service(task)

    async def update_tui_service(self, task: ProxyServicePair) -> None:
        assert self.clash is not None
        try:
            group_state = await self.clash.get_proxy_group(task.proxy_group_name)
        except Exception as exc:
            self.tui.event(task.service_name, f"读取代理组失败: {exc}", level="warning")
            return

        self.tui.update_service(
            task,
            group_state,
            self.storage,
            disabled_node_names=disabled_node_names_for_task(self.config, task),
        )
