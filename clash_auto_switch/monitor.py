import asyncio
import platform
import shutil
import subprocess
import time
from typing import Optional

import httpx

from clash_auto_switch.defs import ClashConfig, MonitoringConfig, ProxyServicePair, AppConfig, ServiceRecord
from clash_auto_switch.clash_api import ClashClient, ClashLogEntry
from clash_auto_switch.storage import NodeHistoryStorage
from clash_auto_switch.project import load_config
from clash_auto_switch.service_tester import normalize_service_name, probe_service, probe_service_multi


AUTO_SWITCH_COOLDOWN_SEC = 5.0

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


def load_app_config() -> Optional[AppConfig]:
    """Load configuration from the standard location."""
    data = load_config()
    if not data:
        return None
    return parse_config_data(data)


def parse_config_data(data: dict) -> AppConfig:
    """Parse configuration data into AppConfig object."""
    # Parse configuration sections
    clash_data = data.get("clash", {})
    monitoring_data = data.get("monitoring", {})
    tasks_data = data.get("tasks", [])

    clash_config = ClashConfig(
        controller=clash_data.get("controller", "127.0.0.1:9097"),
        secret=clash_data.get("secret"),
        http_proxy=clash_data.get("http_proxy", "http://127.0.0.1:7890")
    )

    monitoring_config = MonitoringConfig(
        interval_sec=monitoring_data.get("interval_sec", 30.0),
        max_rotations=monitoring_data.get("max_rotations", 0),
        once=monitoring_data.get("once", False)
    )

    tasks = []
    for task_data in tasks_data:
        task = ProxyServicePair(
            proxy_group_name=task_data["proxy_group_name"],
            service_name=task_data["service_name"],
            enabled=task_data.get("enabled", True)
        )
        tasks.append(task)

    return AppConfig(
        clash=clash_config,
        monitoring=monitoring_config,
        tasks=tasks
    )


def get_service_nodes_by_reliability(
    candidate_nodes: list[str],
    service_name: str,
    proxy_group_name: str,
    storage: NodeHistoryStorage,
) -> list[ServiceRecord]:
    records = [
        record
        for node in candidate_nodes
        for record in storage.get_records_by_node(node, proxy_group_name)
        if record.service_name == service_name
    ]

    if not records:
        return []

    # Sort by reliability score, highest first
    sorted_records = sorted(records, key=lambda x: x.reliability_score, reverse=True)
    return sorted_records


async def select_next_proxy_in_group(
    client: ClashClient,
    proxy_group_name: str,
    service_name: str,
    storage: NodeHistoryStorage,
) -> str:
    """Select the next eligible proxy in a group based on reliability scores.

    Strategy:
    1) Get all available proxies in the group
    2) Filter out dead proxies
    3) Use storage's intelligent recommendation system based on reliability scores
    4) Select the most reliable available proxy

    Args:
        client: ClashClient instance
        proxy_group_name: Name of the proxy group
        service_name: Service being tested (for reliability lookup)
        storage: NodeHistoryStorage instance for reliability data

    Returns:
        Selected proxy name

    Raises:
        RuntimeError: If no eligible proxy found
    """
    group_info = await client.get_proxy(proxy_group_name)
    candidates = group_info.get("all") or []
    if not isinstance(candidates, list) or not candidates:
        raise ValueError(
            f"Proxy group '{proxy_group_name}' has no candidates in 'all'"
        )

    current = group_info.get("now")

    # Filter candidates: check if alive and remove explicitly dead ones
    alive_candidates = []
    for candidate in candidates:
        try:
            candidate_info = await client.get_proxy(candidate)
            # Skip if explicitly dead
            if candidate_info.get("alive") is False:
                continue
            alive_candidates.append(candidate)
        except httpx.HTTPError:
            # If cannot fetch details, assume it might work and include it
            alive_candidates.append(candidate)

    if not alive_candidates:
        raise RuntimeError(
            f"No alive proxies found in group '{proxy_group_name}'."
        )

    # Find the most reliable node for the service
    records = [
        (node, record)
        for node in alive_candidates if node != current
        for record in storage.get_records_by_node(node, proxy_group_name)
        if record.service_name == service_name
    ]
    sorted_records = sorted(records, key=lambda x: x[1].reliability_score, reverse=True)

    if not sorted_records:
        fallback_candidates = [node for node in alive_candidates if node != current]
        if not fallback_candidates:
            raise RuntimeError(
                f"No suitable proxy found in group '{proxy_group_name}'."
            )
        recommended = fallback_candidates[0]
        selected_score = 0.0
    else:
        recommended = sorted_records[0][0]
        selected_score = sorted_records[0][1].reliability_score

    # Switch to the recommended proxy
    await client.select_proxy(proxy_group_name, recommended)

    print(f"    └── 推荐节点: {recommended:<20} | 可靠性评分: {selected_score:.3f}")

    return recommended


async def run_task(
    task: ProxyServicePair,
    clash_config: ClashConfig,
    monitoring_config: MonitoringConfig,
    storage: NodeHistoryStorage,
) -> None:
    """Run a single monitoring task."""
    task_name = task.service_name
    proxy_group_name = task.proxy_group_name
    service_name = task.service_name

    # Calculate padding for consistent alignment
    max_task_name_width = 15  # Fixed width for task name column
    task_name_padded = f"{task_name:<{max_task_name_width}}"

    print(f"[{task_name_padded}] 开始监控: 代理组={proxy_group_name}, 服务={service_name}")

    # Clash controller client
    async with ClashClient.from_external_controller(clash_config.controller, secret=clash_config.secret) as clash:
        rotations = 0
        is_new_proxy = True

        while True:
            _probe = probe_service_multi if is_new_proxy else probe_service
            ok, switched = await check_and_switch_once(
                clash,
                task,
                clash_config,
                storage,
                probe_func=_probe,
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


async def check_and_switch_once(
    clash: ClashClient,
    task: ProxyServicePair,
    clash_config: ClashConfig,
    storage: NodeHistoryStorage,
    *,
    probe_func=probe_service,
    prefix: str = "",
    switch_allowed: bool = True,
    switch_block_reason: Optional[str] = None,
) -> tuple[bool, bool]:
    """Check one service once and switch proxy if unavailable."""
    task_name = task.service_name
    proxy_group_name = task.proxy_group_name
    service_name = task.service_name
    task_name_padded = f"{task_name:<15}"

    current_node = None
    try:
        group_state = await clash.get_proxy(proxy_group_name)
        current_node = group_state.get("now")
    except Exception:
        pass

    try:
        ok, status_text = await probe_func(service_name, clash_config.http_proxy)
    except Exception as e:
        ok, status_text = False, f"检测异常: {e}"

    if isinstance(current_node, str) and current_node:
        storage.record_node_status(
            node_name=current_node,
            service_name=service_name,
            proxy_group=proxy_group_name,
            is_available=ok,
        )

    node_display = current_node if current_node else "未知"
    node_display_padded = f"{node_display:<20}"

    if ok:
        print(f"{prefix}[{task_name_padded}] ✔ 服务可用   | {status_text:<35} | 节点: {node_display_padded}")
        return True, False

    print(f"{prefix}[{task_name_padded}] ✖ 服务不可用 | {status_text:<35} | 节点: {node_display_padded}")

    if not switch_allowed:
        reason = switch_block_reason or "切换被限流"
        print(f"{prefix}[{task_name_padded}] ⏳ 暂不切换   | {reason:<35}")
        return False, False

    try:
        next_proxy = await select_next_proxy_in_group(
            clash, proxy_group_name, service_name, storage
        )
        next_proxy_display = f"{next_proxy:<20}"
        print(f"{prefix}[{task_name_padded}] ➤ 切换代理   | {proxy_group_name} -> {next_proxy_display}")
        storage.record_node_status(
            node_name=next_proxy,
            service_name=service_name,
            proxy_group=proxy_group_name,
            is_available=False,
        )
        return False, True
    except Exception as e:
        print(f"{prefix}[{task_name_padded}] ⚠ 切换失败   | {str(e):<35}")
        return False, False


def notify_user(title: str, message: str) -> bool:
    """Best-effort desktop notification without third-party dependencies."""
    system = platform.system()

    try:
        if system == "Windows":
            script = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                "Add-Type -AssemblyName System.Drawing; "
                "$n=New-Object System.Windows.Forms.NotifyIcon; "
                "$n.Icon=[System.Drawing.SystemIcons]::Information; "
                "$n.BalloonTipTitle=$args[0]; "
                "$n.BalloonTipText=$args[1]; "
                "$n.Visible=$true; "
                "$n.ShowBalloonTip(5000); "
                "Start-Sleep -Seconds 6; "
                "$n.Dispose();"
            )
            subprocess.Popen(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script, title, message],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True

        if system == "Darwin" and shutil.which("osascript"):
            subprocess.Popen(
                ["osascript", "-e", f'display notification "{message}" with title "{title}"'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True

        if shutil.which("notify-send"):
            subprocess.Popen(
                ["notify-send", title, message],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
    except OSError:
        return False

    return False


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
        print("没有可自动触发的启用任务。当前支持: youtube_music")
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
            # print(f'Debug: Got log entry: {log_entry.destination}')
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
    ok, switched = await check_and_switch_once(
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


async def run_multiple_tasks(config: AppConfig) -> None:
    """Run multiple monitoring tasks concurrently."""
    storage = NodeHistoryStorage()
    storage.startup_cleanup()

    # Filter enabled tasks
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

    # Create tasks for concurrent execution
    tasks = []
    for task_config in enabled_tasks:
        task = asyncio.create_task(
            run_task(task_config, config.clash, config.monitoring, storage),
            name=task_config.service_name
        )
        tasks.append(task)

    try:
        # Wait for all tasks to complete (which should be never in monitor mode)
        await asyncio.gather(*tasks)
    except Exception as e:
        print(f"监控任务异常: {e}")
        # Cancel all tasks
        for task in tasks:
            if not task.done():
                task.cancel()
        raise
