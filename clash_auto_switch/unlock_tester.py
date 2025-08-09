import asyncio
import re
import argparse
from datetime import datetime
import httpx
from typing import List, Dict, Optional, Any


# 定义解锁测试项目的结构
class UnlockItem:
    def __init__(self, name: str, status: str, region: Optional[str] = None, check_time: Optional[str] = None):
        self.name = name
        self.status = status
        self.region = region
        self.check_time = check_time or get_local_date_string()

    def to_dict(self) -> Dict[str, Any]:
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

# 测试哔哩哔哩中国大陆
async def check_bilibili_china_mainland(proxy: Optional[str] = None) -> UnlockItem:
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

    return UnlockItem("哔哩哔哩大陆", status)

# 测试哔哩哔哩港澳台
async def check_bilibili_hk_mc_tw(proxy: Optional[str] = None) -> UnlockItem:
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
            
    return UnlockItem("哔哩哔哩港澳台", status)

# 合并的ChatGPT检测功能
async def check_chatgpt_combined(proxy: Optional[str] = None) -> List[UnlockItem]:
    async with create_http_client(proxy) as client:
        results = []
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

        # 2. 测试 ChatGPT iOS
        ios_status = "Failed"
        try:
            response_ios = await client.get("https://ios.chat.openai.com/")
            response_ios.raise_for_status()
            body_lower = response_ios.text.lower()
            if "you may be connected to a disallowed isp" in body_lower:
                ios_status = "Disallowed ISP"
            elif "request is not allowed. please try again later." in body_lower:
                ios_status = "Yes"
            elif "sorry, you have been blocked" in body_lower:
                ios_status = "Blocked"
        except (httpx.RequestError, httpx.HTTPStatusError):
            pass

        results.append(UnlockItem("ChatGPT iOS", ios_status, region=region))

        # 3. 测试 ChatGPT Web
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

        results.append(UnlockItem("ChatGPT Web", web_status, region=region))
        
    return results

# 测试Gemini
async def check_gemini(proxy: Optional[str] = None) -> UnlockItem:
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
                return UnlockItem("Gemini", "Failed", region=None)
            
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

    return UnlockItem("Gemini", status, region=region)

# 测试 YouTube Premium
async def check_youtube_premium(proxy: Optional[str] = None) -> UnlockItem:
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
                return UnlockItem("Youtube Premium", "Failed", region=None)
                
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

    return UnlockItem("Youtube Premium", status, region=region)


# 测试动画疯(Bahamut Anime)
async def check_bahamut_anime(proxy: Optional[str] = None) -> UnlockItem:
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
                return UnlockItem("Bahamut Anime", "Failed")

            # 第二步：使用设备ID检查访问权限
            token_url = f"https://ani.gamer.com.tw/ajax/token.php?adID=89422&sn=37783&device={device_id}"
            token_res = await anime_client.get(token_url)
            token_res.raise_for_status()
            
            # 确保完整读取响应
            token_body = token_res.text
            if "animeSn" not in token_body:
                return UnlockItem("Bahamut Anime", "No")
            
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

    return UnlockItem("Bahamut Anime", status, region=region)


# 使用Fast.com API检测Netflix CDN区域
async def check_netflix_cdn(proxy: Optional[str] = None) -> UnlockItem:
    url = "https://api.fast.com/netflix/speedtest/v2?https=true&token=YXNkZmFzZGxmbnNkYWZoYXNkZmhrYWxm&urlCount=5"
    async with create_http_client(proxy) as client:
        try:
            response = await client.get(url, timeout=30)
            if response.status_code == 403:
                return UnlockItem("Netflix", "No (IP Banned By Netflix)")

            response.raise_for_status()
            data = response.json()
            targets = data.get("targets", [])
            if targets:
                location = targets[0].get("location", {})
                country = location.get("country")
                if country:
                    emoji = country_code_to_emoji(country)
                    return UnlockItem("Netflix", "Yes", region=f"{emoji}{country}")

            return UnlockItem("Netflix", "Unknown")

        except (httpx.RequestError, httpx.HTTPStatusError, ValueError) as e:
            return UnlockItem("Netflix", f"Failed (CDN API: {e})")

# 测试 Netflix
async def check_netflix(proxy: Optional[str] = None) -> UnlockItem:
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
                return UnlockItem("Netflix", "Originals Only")
            
            if status1 == 403 or status2 == 403:
                 return UnlockItem("Netflix", "No")

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
                            return UnlockItem("Netflix", "Yes", region=f"{emoji}{region_code.upper()}")
                except httpx.RequestError:
                     pass # Fallback to US
                
                emoji = country_code_to_emoji("us")
                return UnlockItem("Netflix", "Yes", region=f"{emoji}US")

            return UnlockItem("Netflix", f"Failed (Status: {status1}_{status2})")

        except httpx.RequestError as e:
            return UnlockItem("Netflix", f"Failed (Request Error: {e})")

# 测试 Disney+
async def check_disney_plus(proxy: Optional[str] = None) -> UnlockItem:
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
                return UnlockItem("Disney+", "No (IP Banned By Disney+)")
            res_device.raise_for_status()
            
            device_body = res_device.json()
            assertion = device_body.get("assertion")
            if not assertion:
                return UnlockItem("Disney+", "Failed (Cannot extract assertion)")

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
                 return UnlockItem("Disney+", "No (IP Banned By Disney+)")
            
            res_token.raise_for_status()
            token_json = res_token.json()
            refresh_token = token_json.get("refresh_token")
            if not refresh_token:
                return UnlockItem("Disney+", f"Failed (Cannot extract refresh token, status: {res_token.status_code})")
                
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
                        return UnlockItem("Disney+", "Yes", region=f"{emoji}{region} (from main page)")
                except (httpx.RequestError, httpx.HTTPStatusError, ValueError):
                    pass
                return UnlockItem("Disney+", f"Failed (GraphQL error: {res_graphql.status_code})")

            match_country = re.search(r'"countryCode"\s*:\s*"([^"]+)"', graphql_body_text)
            region = match_country.group(1) if match_country else None

            if not region:
                return UnlockItem("Disney+", "No")

            if region == "JP":
                emoji = country_code_to_emoji("JP")
                return UnlockItem("Disney+", "Yes", region=f"{emoji}JP")

            match_supported = re.search(r'"inSupportedLocation"\s*:\s*(true|false)', graphql_body_text)
            in_supported_location = match_supported and match_supported.group(1) == "true"
            
            res_preview = await client.get("https://disneyplus.com")
            is_unavailable = "preview" in str(res_preview.url) or "unavailable" in str(res_preview.url)
            
            if is_unavailable:
                return UnlockItem("Disney+", "No")
                
            emoji = country_code_to_emoji(region)
            if in_supported_location:
                return UnlockItem("Disney+", "Yes", region=f"{emoji}{region}")
            else:
                return UnlockItem("Disney+", "Soon", region=f"{emoji}{region}（即将上线）")

        except (httpx.RequestError, httpx.HTTPStatusError, ValueError, KeyError) as e:
            return UnlockItem("Disney+", f"Failed (Error: {e})")

# 测试 Amazon Prime Video
async def check_prime_video(proxy: Optional[str] = None) -> UnlockItem:
    url = "https://www.primevideo.com"
    async with create_http_client(proxy) as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
            
            # 确保完整读取响应
            body = response.text
            if not body:
                return UnlockItem("Prime Video", "Failed (Empty Response)")
            
            if "isServiceRestricted" in body:
                return UnlockItem("Prime Video", "No (Service Not Available)")

            match_region = re.search(r'"currentTerritory":"([^"]+)"', body)
            if match_region:
                region = match_region.group(1)
                emoji = country_code_to_emoji(region)
                return UnlockItem("Prime Video", "Yes", region=f"{emoji}{region}")
            
            return UnlockItem("Prime Video", "Failed (Error: PAGE ERROR)")

        except httpx.HTTPStatusError as e:
            return UnlockItem("Prime Video", f"Failed (HTTP {e.response.status_code})")
        except httpx.RequestError as e:
            return UnlockItem("Prime Video", f"Failed (Network: {str(e)[:50]})")


async def main(proxy: Optional[str]):
    # 每个检测函数现在都使用独立的客户端
    tasks = [
        check_bilibili_china_mainland(proxy),
        check_bilibili_hk_mc_tw(proxy),
        check_chatgpt_combined(proxy),
        check_gemini(proxy),
        check_youtube_premium(proxy),
        check_bahamut_anime(proxy),
        check_netflix(proxy),
        check_disney_plus(proxy),
        check_prime_video(proxy),
    ]
    
    results = await asyncio.gather(*tasks)
    
    final_results = []
    for result in results:
        if isinstance(result, list):
            final_results.extend([item.to_dict() for item in result])
        else:
            final_results.append(result.to_dict())
            
    # 打印结果
    import json
    print(json.dumps(final_results, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Run unlock tests for various streaming services.')
    parser.add_argument('--proxy', type=str, default='http://127.0.0.1:7890', help='Proxy to use for the requests, e.g., http://127.0.0.1:7890')
    args = parser.parse_args()
    
    asyncio.run(main(args.proxy))
