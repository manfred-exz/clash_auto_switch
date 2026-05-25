from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx
import re

from clash_auto_switch.core.diagnostic_log import DiagnosticLogger
from .base import ServiceChecker, ServiceCheckResult, ServiceHostPatterns
from .common import (
    _SERVICE_DEBUG_EVENT_HANDLER,
    create_http_client,
    country_code_to_emoji,
    normalize_response_text,
    TestResultItem,
)


def parse_youtube_music_page(body: str) -> tuple[str, Optional[str]]:
    """Parse YouTube Music availability from the music.youtube.com shell page."""
    if not body:
        return "Failed", None

    normalized_body = normalize_response_text(body)
    body_lower = normalized_body.lower()
    unavailable_indicators = (
        "not available in your country",
        "not available in your region",
        "所在区域无法使用",
        "無法在你所在區域使用",
        "无法在你所在区域使用",
        "你所在的国家/地区无法使用",
        "你所在的國家/地區無法使用",
    )
    if any(indicator in body_lower for indicator in unavailable_indicators):
        return "No", None

    unsupported_browser_indicators = (
        "your browser is not supported",
        "browser is not supported",
        "請升級",
    )
    if any(indicator in body_lower for indicator in unsupported_browser_indicators):
        return "Failed (Unsupported Browser)", None

    region = None
    for pattern in (
        r'"GL":"([A-Z]{2})"',
        r'"countryCode":"([A-Z]{2})"',
        r'"regionCode":"([A-Z]{2})"',
        r"'country_code': '([A-Z]{2})'",
    ):
        match = re.search(pattern, normalized_body)
        if match:
            country_code = match.group(1)
            region = f"{country_code_to_emoji(country_code)}{country_code}"
            break

    available_indicators = (
        "music.youtube.com/youtubei/",
        "innertube_context",
        "web_remix",
    )
    if any(indicator in body_lower for indicator in available_indicators):
        return "Yes", region

    return "Failed (Unexpected Page)", region


YOUTUBE_MUSIC_DEFAULT_API_KEY = "REDACTED_YTM_API_KEY"
YOUTUBE_MUSIC_PROBE_VIDEO_IDS = (
    "kJQP7kiw5Fk",  # Luis Fonsi - Despacito
    "JGwWNGJdvx8",  # Ed Sheeran - Shape of You
    "9bZkp7q19f0",  # PSY - Gangnam Style
)


def extract_youtube_music_api_config(body: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    normalized_body = normalize_response_text(body)

    api_key_match = re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', normalized_body)
    version_match = re.search(r'"INNERTUBE_CLIENT_VERSION":"([^"]+)"', normalized_body)
    gl_match = re.search(r'"gl":"([A-Z]{2})"', normalized_body, re.IGNORECASE)

    return (
        api_key_match.group(1) if api_key_match else None,
        version_match.group(1) if version_match else None,
        gl_match.group(1).upper() if gl_match else None,
    )


def extract_youtube_music_visitor_data(body: str) -> Optional[str]:
    normalized_body = normalize_response_text(body)
    for pattern in (
        r'"VISITOR_DATA":"([^"]+)"',
        r'"visitorData":"([^"]+)"',
    ):
        match = re.search(pattern, normalized_body)
        if match:
            return match.group(1)
    return None


def build_youtube_music_context(client_version: str, gl: Optional[str]) -> Dict:
    return {
        "context": {
            "client": {
                "clientName": "WEB_REMIX",
                "clientVersion": client_version,
                "hl": "zh-CN",
                "gl": gl or "US",
            },
            "user": {},
        }
    }


def parse_youtube_music_player_response(data: Dict) -> str:
    playability = data.get("playabilityStatus") or {}
    status = playability.get("status")

    if status == "OK" and data.get("streamingData"):
        return "Yes"

    if status in {"LOGIN_REQUIRED", "AGE_CHECK_REQUIRED"}:
        return "Yes"

    if status == "UNPLAYABLE":
        return "No"

    if status:
        return f"Failed (Player {status})"

    return "Failed (Unexpected Player Response)"


def summarize_youtube_music_player_statuses(statuses: List[str]) -> str:
    if any(status == "Yes" for status in statuses):
        return "Yes"
    if statuses and all(status == "No" for status in statuses):
        return "No"
    failed = next((status for status in statuses if status.startswith("Failed")), None)
    return failed or "Failed (Unexpected Player Response)"


def parse_youtube_music_api_response(data: Dict) -> str:
    """Parse a YouTube Music browse/search API response."""
    if data.get("contents") or data.get("background"):
        return "Yes"

    error = data.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        status = error.get("status")
        message = error.get("message")
        return f"Failed (API {code or status or message or 'Error'})"

    if data.get("responseContext"):
        return "Failed (API Missing Contents)"

    return "Failed (Unexpected API Response)"


async def request_youtube_music_api(
    client: httpx.AsyncClient,
    endpoint: str,
    body: Dict,
    api_key: str,
    visitor_data: Optional[str],
) -> Dict:
    headers = {
        "Origin": "https://music.youtube.com",
        "Referer": "https://music.youtube.com/",
    }
    if visitor_data:
        headers["X-Goog-Visitor-Id"] = visitor_data

    response = await client.post(
        f"https://music.youtube.com/youtubei/v1/{endpoint}?alt=json&key={api_key}",
        json=body,
        headers=headers,
        cookies={"SOCS": "CAI"},
    )
    response.raise_for_status()
    return response.json()


async def check_youtube_music_playability(
    client: httpx.AsyncClient,
    api_key: str,
    context: Dict,
    visitor_data: Optional[str],
) -> tuple[str, list[dict[str, Any]]]:
    statuses = []
    details = []
    for video_id in YOUTUBE_MUSIC_PROBE_VIDEO_IDS:
        player_response = await request_youtube_music_api(
            client,
            "player",
            {
                **context,
                "videoId": video_id,
                "playbackContext": {
                    "contentPlaybackContext": {
                        "html5Preference": "HTML5_PREF_WANTS",
                    }
                },
            },
            api_key,
            visitor_data,
        )
        player_status = parse_youtube_music_player_response(player_response)
        statuses.append(player_status)
        details.append(
            {
                "video_id": video_id,
                "result": player_status,
                **youtube_music_player_debug(player_response),
            }
        )
        if statuses[-1] == "Yes":
            break
    return summarize_youtube_music_player_statuses(statuses), details


def emit_youtube_music_probe_debug(debug: dict[str, Any]) -> None:
    DiagnosticLogger().write(
        "youtube_music_probe_debug",
        service_name="youtube_music",
        **debug,
    )
    handler = _SERVICE_DEBUG_EVENT_HANDLER.get()
    if handler is None:
        return

    page = debug.get("page") or {}
    config = debug.get("config") or {}
    api = debug.get("api") or {}
    playability = debug.get("playability") or {}
    player_items = playability.get("players") or []
    player_text = ",".join(
        f"{item.get('video_id')}={item.get('result')}/{item.get('status')}"
        for item in player_items
        if isinstance(item, dict)
    ) or "-"
    handler(
        "youtube_music",
        (
            "YTMusic检测 | "
            f"page={page.get('result', '-')} http={page.get('http_status', '-')} "
            f"region={page.get('region', '-')} len={page.get('length', '-')} | "
            f"config=key:{config.get('has_api_key', '-')} "
            f"client:{config.get('client_version', '-')} gl:{config.get('gl', '-')} "
            f"visitor:{config.get('has_visitor_data', '-')} | "
            f"api=browse:{api.get('browse_result', '-')} "
            f"search:{api.get('search_result', '-')} | "
            f"player={playability.get('result', '-')} [{player_text}] | "
            f"final={debug.get('final_status', '-')}"
        ),
    )


def youtube_music_player_debug(data: Dict) -> Dict[str, Optional[str]]:
    playability = data.get("playabilityStatus") or {}
    response_context = data.get("responseContext") or {}
    return {
        "status": playability.get("status"),
        "reason": playability.get("reason"),
        "playable_in_embed": str(playability.get("playableInEmbed"))
        if "playableInEmbed" in playability
        else None,
        "has_streaming_data": str(bool(data.get("streamingData"))),
        "visitor_data": response_context.get("visitorData"),
    }


# 测试 YouTube Music
async def check_youtube_music(proxy: Optional[str] = None) -> TestResultItem:
    url = "https://music.youtube.com/"
    custom_headers = {
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
    }
    debug: dict[str, Any] = {
        "proxy": proxy,
        "page": {},
        "config": {},
        "api": {},
        "playability": {"players": []},
    }
    status = "Failed"
    region = None
    async with create_http_client(proxy, custom_headers) as client:
        try:
            response = await client.get(url, follow_redirects=True)
            debug["page"].update(
                {
                    "url": str(response.url),
                    "http_status": response.status_code,
                    "length": len(response.text),
                }
            )
            response.raise_for_status()
            status, region = parse_youtube_music_page(response.text)
            debug["page"].update({"result": status, "region": region})
            if status != "Yes":
                debug["final_status"] = status
                emit_youtube_music_probe_debug(debug)
                return TestResultItem("Youtube Music", status, region=region)

            api_key, client_version, gl = extract_youtube_music_api_config(response.text)
            has_api_key = bool(api_key)
            visitor_data = extract_youtube_music_visitor_data(response.text)
            debug["config"].update(
                {
                    "has_api_key": has_api_key,
                    "client_version": client_version,
                    "gl": gl,
                    "has_visitor_data": bool(visitor_data),
                }
            )
            if not client_version:
                status = "Failed (Missing API Config)"
                debug["final_status"] = status
                emit_youtube_music_probe_debug(debug)
                return TestResultItem("Youtube Music", status, region=region)

            api_key = api_key or YOUTUBE_MUSIC_DEFAULT_API_KEY
            debug["config"]["used_default_api_key"] = not has_api_key
            context = build_youtube_music_context(client_version, gl)
            api_response = await request_youtube_music_api(
                client,
                "browse",
                {**context, "browseId": "FEmusic_home"},
                api_key,
                visitor_data,
            )
            status = parse_youtube_music_api_response(api_response)
            debug["api"].update(
                {
                    "browse_result": status,
                    "browse_keys": sorted(api_response.keys()),
                    "browse_has_contents": bool(api_response.get("contents")),
                    "browse_has_background": bool(api_response.get("background")),
                }
            )
            if status == "Failed (API Missing Contents)":
                search_response = await request_youtube_music_api(
                    client,
                    "search",
                    {**context, "query": "Wonderwall"},
                    api_key,
                    visitor_data,
                )
                status = parse_youtube_music_api_response(search_response)
                debug["api"].update(
                    {
                        "search_result": status,
                        "search_keys": sorted(search_response.keys()),
                        "search_has_contents": bool(search_response.get("contents")),
                    }
                )
            if status == "Yes":
                status, player_details = await check_youtube_music_playability(
                    client,
                    api_key,
                    context,
                    visitor_data,
                )
                debug["playability"].update(
                    {
                        "result": status,
                        "players": player_details,
                    }
                )
        except httpx.HTTPStatusError as e:
            status = f"Failed (HTTP {e.response.status_code})"
            debug["error"] = {"type": type(e).__name__, "message": str(e)}
            region = None
        except ValueError:
            status = "Failed (Invalid API Response)"
            debug["error"] = {"type": "ValueError", "message": "Invalid API Response"}
            region = None
        except httpx.RequestError as e:
            status = f"Failed (Network: {str(e)[:50]})"
            debug["error"] = {"type": type(e).__name__, "message": str(e)}
            region = None

    debug["final_status"] = status
    debug["final_region"] = region
    emit_youtube_music_probe_debug(debug)
    return TestResultItem("Youtube Music", status, region=region)


async def debug_youtube_music(proxy: Optional[str] = None) -> None:
    url = "https://music.youtube.com/"
    custom_headers = {
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
    }
    async with create_http_client(proxy, custom_headers) as client:
        response = await client.get(url, follow_redirects=True)
        print(f"page_url: {response.url}")
        print(f"page_status: {response.status_code}")
        print(f"page_len: {len(response.text)}")
        status, region = parse_youtube_music_page(response.text)
        print(f"page_parse: {status}")
        print(f"page_region: {region}")

        api_key, client_version, gl = extract_youtube_music_api_config(response.text)
        print(f"api_key: {'yes' if api_key else 'no'}")
        print(f"client_version: {client_version}")
        print(f"gl: {gl}")
        visitor_data = extract_youtube_music_visitor_data(response.text)
        print(f"visitor_data: {'yes' if visitor_data else 'no'}")
        if not client_version:
            print("player: skipped (missing api config)")
            return

        api_key = api_key or YOUTUBE_MUSIC_DEFAULT_API_KEY
        context = build_youtube_music_context(client_version, gl)
        api_response = await request_youtube_music_api(
            client,
            "browse",
            {**context, "browseId": "FEmusic_home"},
            api_key,
            visitor_data,
        )
        print("api_status_code: 200")
        try:
            api_data = api_response
        except ValueError:
            print("api_text: invalid json")
        else:
            print(f"api_parse: {parse_youtube_music_api_response(api_data)}")
            print(f"api_has_contents: {bool(api_data.get('contents'))}")
            if parse_youtube_music_api_response(api_data) == "Failed (API Missing Contents)":
                search_data = await request_youtube_music_api(
                    client,
                    "search",
                    {**context, "query": "Wonderwall"},
                    api_key,
                    visitor_data,
                )
                print(f"search_parse: {parse_youtube_music_api_response(search_data)}")
                print(f"search_has_contents: {bool(search_data.get('contents'))}")

        player_statuses = []
        for video_id in YOUTUBE_MUSIC_PROBE_VIDEO_IDS:
            player_data = await request_youtube_music_api(
                client,
                "player",
                {
                    **context,
                    "videoId": video_id,
                    "playbackContext": {
                        "contentPlaybackContext": {
                            "html5Preference": "HTML5_PREF_WANTS",
                        }
                    },
                },
                api_key,
                visitor_data,
            )
            player_parse = parse_youtube_music_player_response(player_data)
            player_statuses.append(player_parse)
            print(f"player_video_id: {video_id}")
            print(f"player_parse: {player_parse}")
            for key, value in youtube_music_player_debug(player_data).items():
                print(f"player_{key}: {value}")
            if player_parse == "Yes":
                break
        print(f"player_summary: {summarize_youtube_music_player_statuses(player_statuses)}")



class YouTubeMusicChecker(ServiceChecker):
    service_name = "youtube_music"
    display_name = "Youtube Music"
    host_patterns = ServiceHostPatterns(
        trigger_hosts=("music.youtube.com", "youtubei.googleapis.com"),
        extra_connection_hosts=(
            "googlevideo.com",
            "ytimg.com",
            "youtube.com",
        ),
    )

    async def check(self, proxy: Optional[str] = None) -> ServiceCheckResult:
        return await check_youtube_music(proxy)
