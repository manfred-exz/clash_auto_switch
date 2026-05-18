import asyncio
import time
from typing import Optional

from clash_auto_switch.clash_api import ClashClient, ClashLogEntry
from clash_auto_switch.defs import AppConfig, ClashConfig, ProxyServicePair
from clash_auto_switch.notifier import notify_user
from clash_auto_switch.proxy_switcher import check_and_switch_once
from clash_auto_switch.service_tester import normalize_service_name
from clash_auto_switch.storage import NodeHistoryStorage


AUTO_SWITCH_COOLDOWN_SEC = 60.0

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


async def run_auto_tasks(config: AppConfig) -> None:
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

    print("🚀 启动 auto 模式:")
    print("=" * 80)
    for service_name in watched_services:
        task = tasks_by_service[service_name]
        print(f"  📋 [{task.service_name:<15}] 代理组: {task.proxy_group_name:<20} | 日志触发")
    print("=" * 80)
    print()

    last_switch_at: dict[str, float] = {}
    running: dict[str, asyncio.Task] = {}

    async with ClashClient.from_external_controller(config.clash.controller, secret=config.clash.secret) as clash:
        async for log_entry in clash.iter_logs(level="info"):
            service_name = match_auto_trigger_service(log_entry)
            if service_name is None or service_name not in tasks_by_service:
                continue

            running_task = running.get(service_name)
            if running_task and not running_task.done():
                print(f"[auto           ] 跳过检测   | 服务: {service_name:<15} | 上次检测仍在运行")
                continue

            now = time.monotonic()
            task_config = tasks_by_service[service_name]
            host = log_entry.destination.host if log_entry.destination else "unknown"
            switch_elapsed = now - last_switch_at.get(service_name, 0.0)
            switch_allowed = switch_elapsed >= AUTO_SWITCH_COOLDOWN_SEC
            switch_block_reason = None
            if not switch_allowed:
                remaining = AUTO_SWITCH_COOLDOWN_SEC - switch_elapsed
                switch_block_reason = f"切换冷却中，剩余 {remaining:.0f} 秒"
            print(f"[auto           ] 触发检测   | 服务: {task_config.service_name:<15} | 目标: {host}")
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
                )
            )


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
) -> None:
    _ok, switched = await check_and_switch_once(
        clash,
        task_config,
        clash_config,
        storage,
        prefix="[auto] ",
        switch_allowed=switch_allowed,
        switch_block_reason=switch_block_reason,
    )

    if switched:
        last_switch_at[service_name] = time.monotonic()
        message = f"{task_config.service_name} 不可用，已切换 {task_config.proxy_group_name}"
        notify_user("Clash Auto Switch", message)
