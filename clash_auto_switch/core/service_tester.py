import asyncio
import html
import json
import re
import argparse
from dataclasses import dataclass
from datetime import datetime
import httpx
from typing import Awaitable, Callable, Dict, List, Optional, Tuple


# 定义解锁测试项目的结构
@dataclass
class TestResultItem:
    name: str
    status: str
    region: Optional[str] = None
    check_time: Optional[str] = None

    def __post_init__(self) -> None:
        if self.check_time is None:
            self.check_time = get_local_date_string()

    def to_dict(self) -> Dict[str, Optional[str]]:
        return {
            "name": self.name,
            "status": self.status,
            "region": self.region,
            "check_time": self.check_time,
        }

# 获取当前本地时间字符串
def get_local_date_string() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# 将国家代码转换为对应的emoji
def country_code_to_emoji(country_code: str) -> str:
    country_code = country_code.upper()
    if len(country_code) < 2:
        return ""

    c1 = 0x1F1E6 + ord(country_code[0]) - ord('A')
    c2 = 0x1F1E6 + ord(country_code[1]) - ord('A')

    return chr(c1) + chr(c2)

# 创建新的HTTP客户端
def create_http_client(proxy: Optional[str] = None, custom_headers: Optional[Dict[str, str]] = None) -> httpx.AsyncClient:
    default_headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    if custom_headers:
        default_headers.update(custom_headers)

    return httpx.AsyncClient(
        proxy=proxy,
        headers=default_headers,
        timeout=30.0,
        verify=False,
        http2=True
    )


def format_result_status(result: TestResultItem) -> str:
    region = f" ({result.region})" if result.region else ""
    return f"{result.name}: {result.status}{region}"


def parse_trace_country(body: str) -> Optional[str]:
    for line in body.splitlines():
        if line.startswith("loc="):
            country_code = line.removeprefix("loc=").strip().upper()
            return country_code or None
    return None


def normalize_response_text(body: str) -> str:
    """Normalize escaped page text before keyword matching."""
    body = html.unescape(body)
    return re.sub(
        r"\\u([0-9a-fA-F]{4})",
        lambda match: chr(int(match.group(1), 16)),
        body,
    )

# 测试哔哩哔哩中国大陆
async def check_bilibili_china_mainland(proxy: Optional[str] = None) -> TestResultItem:
    url = "https://api.bilibili.com/pgc/player/web/playurl?avid=82846771&qn=0&type=&otype=json&ep_id=307247&fourk=1&fnver=0&fnval=16&module=bangumi"
    async with create_http_client(proxy) as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
            body = response.json()
            code = body.get("code")
            if code == 0:
                status = "Yes"
            elif code == -10403:
                status = "No"
            else:
                status = "Failed"
        except (httpx.RequestError, httpx.HTTPStatusError, ValueError):
            status = "Failed"

    return TestResultItem("哔哩哔哩大陆", status)

# 测试哔哩哔哩港澳台
async def check_bilibili_hk_mc_tw(proxy: Optional[str] = None) -> TestResultItem:
    url = "https://api.bilibili.com/pgc/player/web/playurl?avid=18281381&cid=29892777&qn=0&type=&otype=json&ep_id=183799&fourk=1&fnver=0&fnval=16&module=bangumi"
    async with create_http_client(proxy) as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
            body = response.json()
            code = body.get("code")
            if code == 0:
                status = "Yes"
            elif code == -10403:
                status = "No"
            else:
                status = "Failed"
        except (httpx.RequestError, httpx.HTTPStatusError, ValueError):
            status = "Failed"

    return TestResultItem("哔哩哔哩港澳台", status)

# ChatGPT Web 检测
async def check_chatgpt(proxy: Optional[str] = None) -> TestResultItem:
    async with create_http_client(proxy) as client:
        region = None

        # 1. 获取国家代码
        try:
            response_country = await client.get("https://chat.openai.com/cdn-cgi/trace")
            if response_country.status_code == 200:
                trace_data = {line.split('=')[0]: line.split('=')[1] for line in response_country.text.splitlines() if '=' in line}
                loc = trace_data.get("loc")
                if loc:
                    emoji = country_code_to_emoji(loc)
                    region = f"{emoji}{loc}"
        except httpx.RequestError:
            pass

        # 2. 测试 ChatGPT Web
        web_status = "Failed"
        try:
            response_web = await client.get("https://api.openai.com/compliance/cookie_requirements")
            response_web.raise_for_status()
            body_lower = response_web.text.lower()
            if "unsupported_country" in body_lower:
                web_status = "Unsupported Country/Region"
            else:
                web_status = "Yes"
        except (httpx.RequestError, httpx.HTTPStatusError):
            pass

    return TestResultItem("ChatGPT Web", web_status, region=region)


CLAUDE_BLOCKED_CODES = {"AF", "BY", "CN", "CU", "HK", "IR", "KP", "MO", "RU", "SY"}


# Claude 检测
async def check_claude(proxy: Optional[str] = None) -> TestResultItem:
    url = "https://claude.ai/cdn-cgi/trace"
    async with create_http_client(proxy) as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
            country_code = parse_trace_country(response.text)
            if not country_code:
                return TestResultItem("Claude", "Failed")

            emoji = country_code_to_emoji(country_code)
            status = "No" if country_code in CLAUDE_BLOCKED_CODES else "Yes"
            return TestResultItem("Claude", status, region=f"{emoji}{country_code}")
        except (httpx.RequestError, httpx.HTTPStatusError):
            return TestResultItem("Claude", "Failed")


# 测试Gemini
async def check_gemini(proxy: Optional[str] = None) -> TestResultItem:
    url = "https://gemini.google.com"
    async with create_http_client(proxy) as client:
        status = "Failed"
        region = None
        try:
            # 等待完整响应，类似Rust版本的 response.text().await
            response = await client.get(url)
            response.raise_for_status()  # 检查HTTP状态码

            # 确保完整读取响应体
            body = response.text
            if not body:
                return TestResultItem("Gemini", "Failed", region=None)

            # 检查是否包含成功标识
            is_ok = "45631641,null,true" in body
            status = "Yes" if is_ok else "No"

            # 尝试提取国家代码
            match = re.search(r',2,1,200,"([A-Z]{3})"', body)
            if match:
                country_code = match.group(1)
                emoji = country_code_to_emoji(country_code)
                region = f"{emoji}{country_code}"

        except httpx.HTTPStatusError as e:
            status = f"Failed (HTTP {e.response.status_code})"
        except httpx.RequestError as e:
            status = f"Failed (Network: {str(e)[:50]})"
        except Exception as e:
            status = f"Failed (Error: {str(e)[:50]})"

    return TestResultItem("Gemini", status, region=region)

# 测试 YouTube Premium
async def check_youtube_premium(proxy: Optional[str] = None) -> TestResultItem:
    url = "https://www.youtube.com/premium"
    async with create_http_client(proxy) as client:
        status = "Failed"
        region = None
        try:
            response = await client.get(url)
            response.raise_for_status()

            # 确保完整读取响应体
            body = response.text
            if not body:
                return TestResultItem("Youtube Premium", "Failed", region=None)

            body_lower = body.lower()

            if "youtube premium is not available in your country" in body_lower:
                status = "No"
            elif "ad-free" in body_lower:
                status = "Yes"
                match = re.search(r'id="country-code"[^>]*>([^<]+)<', body)
                if match:
                    country_code = match.group(1).strip()
                    emoji = country_code_to_emoji(country_code)
                    region = f"{emoji}{country_code}"
        except httpx.HTTPStatusError as e:
            status = f"Failed (HTTP {e.response.status_code})"
        except httpx.RequestError as e:
            status = f"Failed (Network: {str(e)[:50]})"
        except Exception as e:
            status = f"Failed (Error: {str(e)[:50]})"

    return TestResultItem("Youtube Premium", status, region=region)


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
    async with create_http_client(proxy, custom_headers) as client:
        try:
            response = await client.get(url, follow_redirects=True)
            response.raise_for_status()
            status, region = parse_youtube_music_page(response.text)
            if status != "Yes":
                return TestResultItem("Youtube Music", status, region=region)

            api_key, client_version, gl = extract_youtube_music_api_config(response.text)
            if not api_key or not client_version:
                return TestResultItem("Youtube Music", "Failed (Missing API Config)", region=region)

            player_response = await client.post(
                f"https://music.youtube.com/youtubei/v1/player?key={api_key}",
                json={
                    "context": {
                        "client": {
                            "clientName": "WEB_REMIX",
                            "clientVersion": client_version,
                            "hl": "zh-CN",
                            "gl": gl or "US",
                        }
                    },
                    "videoId": "kJQP7kiw5Fk",
                    "playbackContext": {
                        "contentPlaybackContext": {
                            "html5Preference": "HTML5_PREF_WANTS",
                        }
                    },
                },
                headers={
                    "Origin": "https://music.youtube.com",
                    "Referer": "https://music.youtube.com/",
                },
            )
            player_response.raise_for_status()
            status = parse_youtube_music_player_response(player_response.json())
        except httpx.HTTPStatusError as e:
            status = f"Failed (HTTP {e.response.status_code})"
            region = None
        except ValueError:
            status = "Failed (Invalid Player Response)"
            region = None
        except httpx.RequestError as e:
            status = f"Failed (Network: {str(e)[:50]})"
            region = None

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
        if not api_key or not client_version:
            print("player: skipped (missing api config)")
            return

        player_response = await client.post(
            f"https://music.youtube.com/youtubei/v1/player?key={api_key}",
            json={
                "context": {
                    "client": {
                        "clientName": "WEB_REMIX",
                        "clientVersion": client_version,
                        "hl": "zh-CN",
                        "gl": gl or "US",
                    }
                },
                "videoId": "kJQP7kiw5Fk",
                "playbackContext": {
                    "contentPlaybackContext": {
                        "html5Preference": "HTML5_PREF_WANTS",
                    }
                },
            },
            headers={
                "Origin": "https://music.youtube.com",
                "Referer": "https://music.youtube.com/",
            },
        )
        print(f"player_status_code: {player_response.status_code}")
        try:
            player_data = player_response.json()
        except ValueError:
            print(f"player_text: {player_response.text[:500]}")
            return
        print(f"player_parse: {parse_youtube_music_player_response(player_data)}")
        for key, value in youtube_music_player_debug(player_data).items():
            print(f"player_{key}: {value}")


# 测试动画疯(Bahamut Anime)
async def check_bahamut_anime(proxy: Optional[str] = None) -> TestResultItem:
    status = "Failed"
    region = None
    try:
        # 使用独立的带Windows User-Agent的客户端
        custom_headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"}
        async with create_http_client(proxy, custom_headers) as anime_client:
            # 第一步：获取设备ID
            device_url = "https://ani.gamer.com.tw/ajax/getdeviceid.php"
            device_id_res = await anime_client.get(device_url)
            device_id_res.raise_for_status()
            device_id_json = device_id_res.json()
            device_id = device_id_json.get("deviceid")

            if not device_id:
                return TestResultItem("Bahamut Anime", "Failed")

            # 第二步：使用设备ID检查访问权限
            token_url = f"https://ani.gamer.com.tw/ajax/token.php?adID=89422&sn=37783&device={device_id}"
            token_res = await anime_client.get(token_url)
            token_res.raise_for_status()

            # 确保完整读取响应
            token_body = token_res.text
            if "animeSn" not in token_body:
                return TestResultItem("Bahamut Anime", "No")

            # 第三步：访问主页获取区域信息
            main_page_res = await anime_client.get("https://ani.gamer.com.tw/")
            main_page_res.raise_for_status()
            body = main_page_res.text
            match = re.search(r'data-geo="([^"]+)"', body)
            if match:
                country_code = match.group(1)
                emoji = country_code_to_emoji(country_code)
                region = f"{emoji}{country_code}"

            status = "Yes"

    except (httpx.RequestError, httpx.HTTPStatusError, ValueError):
        status = "Failed"

    return TestResultItem("Bahamut Anime", status, region=region)


# 使用Fast.com API检测Netflix CDN区域
async def check_netflix_cdn(proxy: Optional[str] = None) -> TestResultItem:
    url = "https://api.fast.com/netflix/speedtest/v2?https=true&token=YXNkZmFzZGxmbnNkYWZoYXNkZmhrYWxm&urlCount=5"
    async with create_http_client(proxy) as client:
        try:
            response = await client.get(url, timeout=30)
            if response.status_code == 403:
                return TestResultItem("Netflix", "No (IP Banned By Netflix)")

            response.raise_for_status()
            data = response.json()
            targets = data.get("targets", [])
            if targets:
                location = targets[0].get("location", {})
                country = location.get("country")
                if country:
                    emoji = country_code_to_emoji(country)
                    return TestResultItem("Netflix", "Yes", region=f"{emoji}{country}")

            return TestResultItem("Netflix", "Unknown")

        except (httpx.RequestError, httpx.HTTPStatusError, ValueError) as e:
            return TestResultItem("Netflix", f"Failed (CDN API: {e})")

# 测试 Netflix
async def check_netflix(proxy: Optional[str] = None) -> TestResultItem:
    cdn_result = await check_netflix_cdn(proxy)
    if cdn_result.status == "Yes":
        return cdn_result

    async with create_http_client(proxy) as client:
        url1 = "https://www.netflix.com/title/81280792"  # LEGO Ninjago
        url2 = "https://www.netflix.com/title/70143836"  # Breaking Bad

        try:
            res1 = await client.get(url1, timeout=30, follow_redirects=True)
            res2 = await client.get(url2, timeout=30, follow_redirects=True)

            status1 = res1.status_code
            status2 = res2.status_code

            if status1 == 404 and status2 == 404:
                return TestResultItem("Netflix", "Originals Only")

            if status1 == 403 or status2 == 403:
                 return TestResultItem("Netflix", "No")

            if status1 in [200, 301, 302] or status2 in [200, 301, 302]:
                test_url = "https://www.netflix.com/title/80018499"
                try:
                    test_res = await client.get(test_url, timeout=30, follow_redirects=False) # Do not follow redirects to get location
                    if 'location' in test_res.headers:
                        location_str = test_res.headers['location']
                        parts = location_str.split('/')
                        if len(parts) >= 4:
                            region_code = parts[3].split('-')[0]
                            emoji = country_code_to_emoji(region_code)
                            return TestResultItem("Netflix", "Yes", region=f"{emoji}{region_code.upper()}")
                except httpx.RequestError:
                     pass # Fallback to US

                emoji = country_code_to_emoji("us")
                return TestResultItem("Netflix", "Yes", region=f"{emoji}US")

            return TestResultItem("Netflix", f"Failed (Status: {status1}_{status2})")

        except httpx.RequestError as e:
            return TestResultItem("Netflix", f"Failed (Request Error: {e})")

# 测试 Disney+
async def check_disney_plus(proxy: Optional[str] = None) -> TestResultItem:
    auth_header = "Bearer ZGlzbmV5JmJyb3dzZXImMS4wLjA.Cu56AgSfBTDag5NiRA81oLHkDZfu5L3CKadnefEAY84"
    async with create_http_client(proxy) as client:
        try:
            # Step 1: Get assertion
            device_api_url = "https://disney.api.edge.bamgrid.com/devices"
            device_req_body = {
                "deviceFamily": "browser",
                "applicationRuntime": "chrome",
                "deviceProfile": "windows",
                "attributes": {}
            }
            res_device = await client.post(device_api_url, json=device_req_body, headers={"authorization": auth_header})

            if res_device.status_code == 403:
                return TestResultItem("Disney+", "No (IP Banned By Disney+)")
            res_device.raise_for_status()

            device_body = res_device.json()
            assertion = device_body.get("assertion")
            if not assertion:
                return TestResultItem("Disney+", "Failed (Cannot extract assertion)")

            # Step 2: Get token
            token_url = "https://disney.api.edge.bamgrid.com/token"
            token_body = {
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "latitude": "0",
                "longitude": "0",
                "platform": "browser",
                "subject_token": assertion,
                "subject_token_type": "urn:bamtech:params:oauth:token-type:device",
            }
            res_token = await client.post(token_url, data=token_body, headers={"authorization": auth_header})

            # 确保完整读取token响应
            token_text = res_token.text
            if "forbidden-location" in token_text or "403 ERROR" in token_text:
                 return TestResultItem("Disney+", "No (IP Banned By Disney+)")

            res_token.raise_for_status()
            token_json = res_token.json()
            refresh_token = token_json.get("refresh_token")
            if not refresh_token:
                return TestResultItem("Disney+", f"Failed (Cannot extract refresh token, status: {res_token.status_code})")

            # Step 3: GraphQL for region info
            graphql_url = "https://disney.api.edge.bamgrid.com/graph/v1/device/graphql"
            graphql_payload = {
                "query": "mutation refreshToken($input: RefreshTokenInput!) { refreshToken(refreshToken: $input) { activeSession { sessionId } } }",
                "variables": {"input": {"refreshToken": refresh_token}}
            }
            res_graphql = await client.post(graphql_url, json=graphql_payload, headers={"authorization": auth_header})

            # 确保完整读取GraphQL响应
            graphql_body_text = res_graphql.text

            if res_graphql.status_code >= 400:
                 # Fallback to main page check
                try:
                    res_main = await client.get("https://www.disneyplus.com/")
                    res_main.raise_for_status()
                    body_main = res_main.text
                    match_main = re.search(r'"region"\s*:\s*"([^"]+)"', body_main)
                    if match_main:
                        region = match_main.group(1)
                        emoji = country_code_to_emoji(region)
                        return TestResultItem("Disney+", "Yes", region=f"{emoji}{region} (from main page)")
                except (httpx.RequestError, httpx.HTTPStatusError, ValueError):
                    pass
                return TestResultItem("Disney+", f"Failed (GraphQL error: {res_graphql.status_code})")

            match_country = re.search(r'"countryCode"\s*:\s*"([^"]+)"', graphql_body_text)
            region = match_country.group(1) if match_country else None

            if not region:
                return TestResultItem("Disney+", "No")

            if region == "JP":
                emoji = country_code_to_emoji("JP")
                return TestResultItem("Disney+", "Yes", region=f"{emoji}JP")

            match_supported = re.search(r'"inSupportedLocation"\s*:\s*(true|false)', graphql_body_text)
            in_supported_location = match_supported and match_supported.group(1) == "true"

            res_preview = await client.get("https://disneyplus.com")
            is_unavailable = "preview" in str(res_preview.url) or "unavailable" in str(res_preview.url)

            if is_unavailable:
                return TestResultItem("Disney+", "No")

            emoji = country_code_to_emoji(region)
            if in_supported_location:
                return TestResultItem("Disney+", "Yes", region=f"{emoji}{region}")
            else:
                return TestResultItem("Disney+", "Soon", region=f"{emoji}{region}（即将上线）")

        except (httpx.RequestError, httpx.HTTPStatusError, ValueError, KeyError) as e:
            return TestResultItem("Disney+", f"Failed (Error: {e})")

# 测试 Amazon Prime Video
async def check_prime_video(proxy: Optional[str] = None) -> TestResultItem:
    url = "https://www.primevideo.com"
    async with create_http_client(proxy) as client:
        try:
            response = await client.get(url)
            response.raise_for_status()

            # 确保完整读取响应
            body = response.text
            if not body:
                return TestResultItem("Prime Video", "Failed (Empty Response)")

            if "isServiceRestricted" in body:
                return TestResultItem("Prime Video", "No (Service Not Available)")

            match_region = re.search(r'"currentTerritory":"([^"]+)"', body)
            if match_region:
                region = match_region.group(1)
                emoji = country_code_to_emoji(region)
                return TestResultItem("Prime Video", "Yes", region=f"{emoji}{region}")

            return TestResultItem("Prime Video", "Failed (Error: PAGE ERROR)")

        except httpx.HTTPStatusError as e:
            return TestResultItem("Prime Video", f"Failed (HTTP {e.response.status_code})")
        except httpx.RequestError as e:
            return TestResultItem("Prime Video", f"Failed (Network: {str(e)[:50]})")


ServiceChecker = Callable[[Optional[str]], Awaitable[TestResultItem]]

SERVICE_ALIASES = {
    "bilibili_cn": "bilibili_mainland",
    "bilibili_mainland": "bilibili_mainland",
    "bilibili_hk": "bilibili_hk_mc_tw",
    "bilibili_hk_mc_tw": "bilibili_hk_mc_tw",
    "chatgpt": "chatgpt",
    "openai": "chatgpt",
    "claude": "claude",
    "anthropic": "claude",
    "gemini": "gemini",
    "youtube": "youtube_premium",
    "youtube_premium": "youtube_premium",
    "youtube_music": "youtube_music",
    "youtubemusic": "youtube_music",
    "youtube-music": "youtube_music",
    "ytmusic": "youtube_music",
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

SERVICE_CHECKERS: Dict[str, ServiceChecker] = {
    # "bilibili_mainland": check_bilibili_china_mainland,
    # "bilibili_hk_mc_tw": check_bilibili_hk_mc_tw,
    "chatgpt": check_chatgpt,
    "claude": check_claude,
    "gemini": check_gemini,
    "youtube_premium": check_youtube_premium,
    "youtube_music": check_youtube_music,
    "bahamut_anime": check_bahamut_anime,
    "netflix": check_netflix,
    "disney_plus": check_disney_plus,
    "prime_video": check_prime_video,
}


def normalize_service_name(service_name: str) -> str:
    key = service_name.strip().lower().replace(" ", "_")
    return SERVICE_ALIASES.get(key, key)


async def probe_service(
    service_name: str,
    proxy_url: Optional[str],
) -> Tuple[bool, str]:
    """Return (is_unlocked, human_status).

    The service is considered unlocked only when status == "Yes".
    """

    norm = normalize_service_name(service_name)

    checker = SERVICE_CHECKERS.get(norm)
    if checker:
        result = await checker(proxy_url)
        return result.status == "Yes", format_result_status(result)

    return False, f"未知服务: {service_name}"


async def probe_service_multi(
    service_name: str,
    proxy_url: Optional[str],
    count: int = 3
) -> Tuple[bool, str]:
    """连续多次检测服务，任意失败则返回失败。

    Args:
        service_name: 服务名称
        proxy_url: 代理URL
        count: 检测次数，默认3次

    Returns:
        Tuple[bool, str]: (是否全部成功, 状态描述)
    """
    for i in range(count):
        try:
            is_unlocked, status = await probe_service(service_name, proxy_url)
            if not is_unlocked:
                return False, f"第{i+1}次检测失败: {status}"
            # 如果不是最后一次检测，等待1秒
            if i < count - 1:
                await asyncio.sleep(1.0)
        except Exception as e:
            return False, f"第{i+1}次检测异常: {e}"

    return True, status


async def main(proxy: Optional[str], service: Optional[str] = None, debug: bool = False):
    if debug:
        if service != "youtube_music":
            raise SystemExit("--debug currently supports --service youtube_music only")
        await debug_youtube_music(proxy)
        return

    if service:
        norm = normalize_service_name(service)
        checker = SERVICE_CHECKERS.get(norm)
        if checker is None:
            raise SystemExit(f"unknown service: {service}")
        result = await checker(proxy)
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        return

    # 每个检测函数现在都使用独立的客户端
    tasks = [
        check_chatgpt(proxy),
        check_claude(proxy),
        check_gemini(proxy),
        check_youtube_premium(proxy),
        check_youtube_music(proxy),
        check_bahamut_anime(proxy),
        check_netflix(proxy),
        check_disney_plus(proxy),
        check_prime_video(proxy),
    ]

    results = await asyncio.gather(*tasks)

    final_results = []
    for result in results:
        final_results.append(result.to_dict())

    print(json.dumps(final_results, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Run unlock tests for various streaming services.')
    parser.add_argument('--proxy', type=str, default='http://127.0.0.1:7890', help='Proxy to use for the requests, e.g., http://127.0.0.1:7890')
    parser.add_argument('--service', type=str, default=None, help='Only run one service checker')
    parser.add_argument('--debug', action='store_true', help='Print debug details for the selected service')
    args = parser.parse_args()

    asyncio.run(main(args.proxy, args.service, args.debug))
