import asyncio
import argparse
import time
from typing import Optional, Set, Tuple, Dict

import httpx

from clash_auto_switch.clash_api import ClashClient
from clash_auto_switch.unlock_tester import (
    check_bilibili_china_mainland,
    check_bilibili_hk_mc_tw,
    check_chatgpt_combined,
    check_gemini,
    check_youtube_premium,
    check_bahamut_anime,
    check_netflix,
    check_disney_plus,
    check_prime_video,
)


async def probe_service(
    service_name: str,
    http_client: httpx.AsyncClient,
    proxy_url: Optional[str],
) -> Tuple[bool, str]:
    """Return (is_unlocked, human_status).

    The service is considered unlocked only when status == "Yes".
    For ChatGPT, unlocked if either iOS/Web returns Yes.
    """

    key = service_name.strip().lower()
    # Normalize common aliases
    alias = {
        "bilibili_cn": "bilibili_mainland",
        "bilibili_mainland": "bilibili_mainland",
        "bilibili_hk": "bilibili_hk_mc_tw",
        "bilibili_hk_mc_tw": "bilibili_hk_mc_tw",
        "chatgpt": "chatgpt",
        "openai": "chatgpt",
        "gemini": "gemini",
        "youtube": "youtube_premium",
        "youtube_premium": "youtube_premium",
        "bahamut": "bahamut_anime",
        "bahamut_anime": "bahamut_anime",
        "netflix": "netflix",
        "disney": "disney_plus",
        "disney+": "disney_plus",
        "disney_plus": "disney_plus",
        "prime": "prime_video",
        "prime_video": "prime_video",
        "amazon_prime": "prime_video",
    }

    norm = alias.get(key, key)

    if norm == "bilibili_mainland":
        result = await check_bilibili_china_mainland(http_client)
        return result.status == "Yes", f"{result.name}: {result.status}"
    if norm == "bilibili_hk_mc_tw":
        result = await check_bilibili_hk_mc_tw(http_client)
        return result.status == "Yes", f"{result.name}: {result.status}"
    if norm == "chatgpt":
        items = await check_chatgpt_combined(http_client)
        unlocked = any(item.status == "Yes" for item in items)
        status_text = ", ".join(f"{i.name}: {i.status}{(' (' + i.region + ')') if i.region else ''}" for i in items)
        return unlocked, status_text
    if norm == "gemini":
        result = await check_gemini(http_client)
        region = f" ({result.region})" if result.region else ""
        return result.status == "Yes", f"{result.name}: {result.status}{region}"
    if norm == "youtube_premium":
        result = await check_youtube_premium(http_client)
        region = f" ({result.region})" if result.region else ""
        return result.status == "Yes", f"{result.name}: {result.status}{region}"
    if norm == "bahamut_anime":
        result = await check_bahamut_anime(http_client, proxy_url)
        region = f" ({result.region})" if result.region else ""
        return result.status == "Yes", f"{result.name}: {result.status}{region}"
    if norm == "netflix":
        result = await check_netflix(http_client)
        region = f" ({result.region})" if result.region else ""
        return result.status == "Yes", f"{result.name}: {result.status}{region}"
    if norm == "disney_plus":
        result = await check_disney_plus(http_client)
        region = f" ({result.region})" if result.region else ""
        return result.status == "Yes", f"{result.name}: {result.status}{region}"
    if norm == "prime_video":
        result = await check_prime_video(http_client)
        region = f" ({result.region})" if result.region else ""
        return result.status == "Yes", f"{result.name}: {result.status}{region}"

    return False, f"未知服务: {service_name}"


async def select_next_proxy_in_group(
    client: ClashClient,
    proxy_group_name: str,
    *,
    exclude: Optional[Set[str]] = None,
) -> str:
    """Select the next eligible proxy in a group.

    Eligibility rules:
    - Skip proxies present in the provided `exclude` set (e.g., recently failed nodes)
    - Skip proxies whose own `alive` field is explicitly False

    Strategy:
    1) GET /proxies/:group to obtain `all` and `now` of the group
    2) Iterate forward from the item after `now` with wrap-around
    3) For each candidate, GET /proxies/:candidate and check `alive` != False
    4) PUT /proxies/:group with body {"name": candidate} on the first eligible candidate
    5) Return the selected candidate name
    """
    group_info = await client.get_proxy(proxy_group_name)
    candidates = group_info.get("all") or []
    if not isinstance(candidates, list) or not candidates:
        raise ValueError(
            f"Proxy group '{proxy_group_name}' has no candidates in 'all'"
        )

    exclude = exclude or set()
    current = group_info.get("now")
    try:
        current_index = candidates.index(current) if current in candidates else -1
    except ValueError:
        current_index = -1

    total = len(candidates)
    for offset in range(1, total + 1):
        idx = (current_index + offset) % total
        candidate = candidates[idx]

        if candidate in exclude:
            continue

        try:
            candidate_info = await client.get_proxy(candidate)
        except httpx.HTTPError:
            # If cannot fetch details, try next
            continue

        # Skip if explicitly dead
        if candidate_info.get("alive") is False:
            continue

        await client.select_proxy(proxy_group_name, candidate)
        return candidate

    raise RuntimeError(
        f"No eligible proxy found in group '{proxy_group_name}' after applying filters."
    )


async def run(
    proxy_group_name: str,
    service_name: str,
    controller: str,
    secret: Optional[str],
    http_proxy: str,
    interval_sec: float,
    max_rotations: int,
    monitor: bool,
) -> None:
    # Clash controller client
    async with ClashClient.from_external_controller(controller, secret=secret) as clash:
        # Probe HTTP client routed via Clash HTTP proxy
        async with httpx.AsyncClient(
            proxy=http_proxy,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                )
            },
            timeout=30.0,
            verify=False,
            http2=True,
        ) as probe_client:
            rotations = 0
            recent_failures: Dict[str, float] = {}
            while True:
                try:
                    ok, status_text = await probe_service(
                        service_name, probe_client, http_proxy
                    )
                except Exception as e:
                    ok, status_text = False, f"检测异常: {e}"

                if ok:
                    if rotations != 0:
                        rotations = 0
                    print(f"服务可用 ✔ - {status_text}")
                    if not monitor:
                        return
                    await asyncio.sleep(interval_sec)
                    continue

                print(f"服务不可用 ✖ - {status_text}")

                # 记录当前节点失败时间
                try:
                    group_state = await clash.get_proxy(proxy_group_name)
                    current_node = group_state.get("now")
                    if isinstance(current_node, str) and current_node:
                        recent_failures[current_node] = time.monotonic()
                except Exception:
                    pass

                try:
                    now_ts = time.monotonic()
                    exclude = {
                        name
                        for name, ts in list(recent_failures.items())
                        if (now_ts - ts) < 300.0
                    }

                    next_proxy = await select_next_proxy_in_group(
                        clash, proxy_group_name, exclude=exclude
                    )
                    rotations += 1
                    print(f"已切换到下一个代理: {proxy_group_name} -> {next_proxy}")
                except Exception as e:
                    print(f"切换代理失败: {e}")
                    # 等待后继续监控
                    await asyncio.sleep(interval_sec)
                    continue

                if max_rotations > 0 and rotations >= max_rotations:
                    print(
                        f"已达到最大切换次数 ({max_rotations})，暂停后继续监控。"
                    )
                    rotations = 0
                    await asyncio.sleep(max(interval_sec, 5.0))

                continue


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "持续检测某服务是否可用；若不可用则在指定Clash代理组内切换到下一个节点。"
        )
    )
    parser.add_argument("proxy_group_name", type=str, help="Clash 代理组名称（Selector）")
    parser.add_argument("service_name", type=str, help="要检测的服务名称，如 chatgpt/netflix/gemini 等")

    parser.add_argument(
        "--controller",
        type=str,
        default="127.0.0.1:9097",
        help="Clash external-controller 地址（可包含协议），默认 127.0.0.1:9097",
    )
    parser.add_argument(
        "--secret",
        type=str,
        default=None,
        help="Clash REST API 的 Secret（如有设置）",
    )
    parser.add_argument(
        "--http-proxy",
        type=str,
        default="http://127.0.0.1:7890",
        help="用于探测请求的 HTTP 代理（指向 Clash 的 HTTP/SOCKS 代理端口）",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=30.0,
        help="每次探测/切换之间的等待秒数，默认 30s",
    )
    parser.add_argument(
        "--max-rotations",
        type=int,
        default=0,
        help="最大切换次数，0 表示无限直到成功",
    )
    parser.add_argument(
        "--monitor",
        action="store_true",
        help="开启持续监控（默认关闭）。关闭时，服务一旦可用即退出。",
        default=False,
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        asyncio.run(
            run(
                proxy_group_name=args.proxy_group_name,
                service_name=args.service_name,
                controller=args.controller,
                secret=args.secret,
                http_proxy=args.http_proxy,
                interval_sec=args.interval,
                max_rotations=args.max_rotations,
                monitor=args.monitor,
            )
        )
    except KeyboardInterrupt:
        print("收到 Ctrl-C，退出。")
        raise SystemExit(130)


if __name__ == "__main__":
    main()

