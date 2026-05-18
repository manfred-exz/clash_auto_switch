import asyncio
import time
from contextlib import suppress
from typing import Optional

import httpx

from clash_auto_switch.clash_api import ClashClient, ClashLogEntry
from clash_auto_switch.connections import close_task_service_connections_best_effort
from clash_auto_switch.defs import AppConfig, ClashConfig, ProxyServicePair
from clash_auto_switch.notifier import notify_user
from clash_auto_switch.proxy_switcher import switch_proxy_group_and_verify, switch_until_service_available
from clash_auto_switch.service_tester import normalize_service_name
from clash_auto_switch.storage import NodeHistoryStorage
from clash_auto_switch.tui import MonitorTui


AUTO_SWITCH_COOLDOWN_SEC = 10.0
LOG_RECONNECT_DELAY_SEC = 3.0

SERVICE_LOG_HOST_PATTERNS = {
    "bilibili_mainland": (
        "bilibili.com",
        "bilibili.cn",
        "bilivideo.com",
        "biligame.net",
    ),
    "bilibili_hk_mc_tw": (
        "bilibili.com",
        "bilibili.tv",
        "bilivideo.com",
    ),
    "chatgpt": (
        "chat.openai.com",
        "chatgpt.com",
        "api.openai.com",
        "ios.chat.openai.com",
        "oaistatic.com",
        "oaiusercontent.com",
    ),
    "claude": (
        "claude.ai",
        "anthropic.com",
    ),
    "gemini": (
        "gemini.google.com",
        "generativelanguage.googleapis.com",
        "aistudio.google.com",
        "ai.google.dev",
    ),
    "youtube_music": (
        "music.youtube.com",
        "youtubei.googleapis.com",
    ),
    "youtube_premium": (
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "youtubei.googleapis.com",
        "googlevideo.com",
        "ytimg.com",
    ),
    "bahamut_anime": (
        "ani.gamer.com.tw",
        "gamer.com.tw",
    ),
    "netflix": (
        "netflix.com",
        "nflxvideo.net",
        "nflximg.net",
        "nflxext.com",
        "fast.com",
    ),
    "disney_plus": (
        "disneyplus.com",
        "disney.api.edge.bamgrid.com",
        "bamgrid.com",
        "disney-plus.net",
    ),
    "prime_video": (
        "primevideo.com",
        "amazonvideo.com",
        "media-amazon.com",
        "pv-cdn.net",
    ),
}


def match_auto_trigger_service(log_entry: ClashLogEntry) -> Optional[str]:
    """Return normalized service name when a Clash log entry should trigger auto probing."""
    candidates = []
    if log_entry.destination:
        candidates.append(log_entry.destination.host)
    candidates.append(log_entry.payload)

    haystack = " ".join(value for value in candidates if value).lower()
    for service_name, patterns in SERVICE_LOG_HOST_PATTERNS.items():
        if any(pattern in haystack for pattern in patterns):
            return service_name
    return None


async def run_auto_monitor_tasks(config: AppConfig) -> None:
    """Trigger checks from realtime Clash connection logs."""
    storage = NodeHistoryStorage()
    storage.startup_cleanup()

    enabled_tasks = [task for task in config.tasks if task.enabled]
    tasks_by_service = {
        normalize_service_name(task.service_name): task
        for task in enabled_tasks
    }

    watched_services = sorted(set(tasks_by_service) & set(SERVICE_LOG_HOST_PATTERNS))
    if not watched_services:
        print("没有可自动触发的启用任务。")
        return

    watched_tasks = [tasks_by_service[service_name] for service_name in watched_services]

    last_switch_at: dict[str, float] = {}
    running: dict[str, asyncio.Task] = {}

    with MonitorTui(watched_tasks) as tui:
        tui.event("system", f"启动 auto 模式 | 监听 {len(watched_tasks)} 个服务")
        async with ClashClient.from_external_controller(config.clash.controller, secret=config.clash.secret) as clash:
            for task in watched_tasks:
                await tui.refresh_service(clash, task, storage)

            async def switch_node(task: ProxyServicePair, node_name: str) -> None:
                await switch_proxy_group_and_verify(clash, task.proxy_group_name, node_name)
                tui.event(task.service_name, f"手动切换 | {task.proxy_group_name} -> {node_name}")
                await close_task_service_connections_best_effort(clash, task, tui.event)
                await tui.refresh_service(clash, task, storage)

            async def consume_logs() -> None:
                while True:
                    try:
                        async for log_entry in clash.iter_logs(level="info"):
                            service_name = match_auto_trigger_service(log_entry)
                            if service_name is None or service_name not in tasks_by_service:
                                continue

                            running_task = running.get(service_name)
                            task_config = tasks_by_service[service_name]
                            if running_task and not running_task.done():
                                tui.event(task_config.service_name, "跳过检测 | 上次检测仍在运行")
                                continue

                            now = time.monotonic()
                            host = log_entry.destination.host if log_entry.destination else "unknown"
                            switch_elapsed = now - last_switch_at.get(service_name, 0.0)
                            switch_allowed = switch_elapsed >= AUTO_SWITCH_COOLDOWN_SEC
                            switch_block_reason = None
                            if not switch_allowed:
                                remaining = AUTO_SWITCH_COOLDOWN_SEC - switch_elapsed
                                switch_block_reason = f"切换冷却中，剩余 {remaining:.0f} 秒"
                            tui.event(task_config.service_name, f"触发检测 | 目标: {host}")
                            running[service_name] = asyncio.create_task(
                                run_auto_check(
                                    clash,
                                    task_config,
                                    config.clash,
                                    storage,
                                    service_name,
                                    last_switch_at,
                                    switch_allowed=switch_allowed,
                                    switch_block_reason=switch_block_reason,
                                    tui=tui,
                                )
                            )
                        tui.event("system", f"日志流结束，{LOG_RECONNECT_DELAY_SEC:.0f} 秒后重连")
                    except httpx.HTTPError as exc:
                        tui.event(
                            "system",
                            f"日志流断开，{LOG_RECONNECT_DELAY_SEC:.0f} 秒后重连 | {type(exc).__name__}",
                        )
                    await asyncio.sleep(LOG_RECONNECT_DELAY_SEC)

            log_task = asyncio.create_task(consume_logs(), name="auto_log_consumer")
            interaction_task = asyncio.create_task(tui.run_interaction(switch_node), name="tui_interaction")
            pending: set[asyncio.Task] = set()
            try:
                done, pending = await asyncio.wait(
                    [log_task, interaction_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for completed in done:
                    completed.result()
            finally:
                for pending_task in [*pending, *running.values()]:
                    if not pending_task.done():
                        pending_task.cancel()
                for pending_task in [*pending, *running.values()]:
                    with suppress(asyncio.CancelledError):
                        await pending_task


async def run_auto_check(
    clash: ClashClient,
    task_config: ProxyServicePair,
    clash_config: ClashConfig,
    storage: NodeHistoryStorage,
    service_name: str,
    last_switch_at: dict[str, float],
    *,
    switch_allowed: bool,
    switch_block_reason: Optional[str],
    tui: Optional[MonitorTui] = None,
) -> None:
    async def after_switch(_node_name: str) -> None:
        await close_task_service_connections_best_effort(
            clash,
            task_config,
            tui.event if tui is not None else None,
        )
        if tui is not None:
            await tui.refresh_service(clash, task_config, storage)

    result = await switch_until_service_available(
        clash,
        task_config,
        clash_config,
        storage,
        switch_allowed=switch_allowed,
        switch_block_reason=switch_block_reason,
        event_handler=tui.event if tui is not None else None,
        after_switch=after_switch,
    )
    if tui is not None:
        await tui.refresh_service(clash, task_config, storage)

    if result.switched:
        last_switch_at[service_name] = time.monotonic()
        message = f"{task_config.service_name} 不可用，已切换 {task_config.proxy_group_name}"
        notify_user("Clash Auto Switch", message)
