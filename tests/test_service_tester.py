import unittest

from clash_auto_switch.core.service_tester import (
    CLAUDE_BLOCKED_CODES,
    SERVICE_CHECKERS,
    extract_youtube_music_api_config,
    normalize_service_name,
    parse_trace_country,
    parse_youtube_music_page,
    parse_youtube_music_player_response,
)


class ServiceTesterTest(unittest.TestCase):
    def test_parse_youtube_music_available_page(self) -> None:
        status, region = parse_youtube_music_page(
            """
            <script>window.dataLayer.push({'country_code': 'TW'});</script>
            <script>ytcfg.set({"INNERTUBE_CONTEXT":{"client":{"GL":"TW"}}});</script>
            <a href="https://music.youtube.com/youtubei/v1/search">YouTube Music</a>
            """
        )

        self.assertEqual(status, "Yes")
        self.assertEqual(region, "🇹🇼TW")

    def test_parse_youtube_music_unavailable_page(self) -> None:
        status, region = parse_youtube_music_page(
            "YouTube Music is not available in your country"
        )

        self.assertEqual(status, "No")
        self.assertIsNone(region)

    def test_parse_youtube_music_chinese_unavailable_page(self) -> None:
        status, region = parse_youtube_music_page(
            """
            <body><div class="content">
              <img class="logo" src="//music.youtube.com/img/on_platform_logo_dark.svg" alt="">
              <div class="message">YouTube Music 在你所在区域无法使用</div>
            </div></body>
            """
        )

        self.assertEqual(status, "No")
        self.assertIsNone(region)

    def test_youtube_music_name_alone_is_not_available_signal(self) -> None:
        status, region = parse_youtube_music_page(
            """
            <body><div class="content">
              <img class="logo" src="//music.youtube.com/img/on_platform_logo_dark.svg" alt="">
              <div class="message">YouTube Music</div>
            </div></body>
            """
        )

        self.assertEqual(status, "Failed (Unexpected Page)")
        self.assertIsNone(region)

    def test_parse_youtube_music_escaped_chinese_unavailable_page(self) -> None:
        status, region = parse_youtube_music_page(
            r"""
            <body><div class="message">
              YouTube Music \u5728\u4f60\u6240\u5728\u533a\u57df\u65e0\u6cd5\u4f7f\u7528
            </div></body>
            """
        )

        self.assertEqual(status, "No")
        self.assertIsNone(region)

    def test_youtube_music_aliases(self) -> None:
        self.assertEqual(normalize_service_name("YoutubeMusic"), "youtube_music")
        self.assertEqual(normalize_service_name("youtube-music"), "youtube_music")
        self.assertEqual(normalize_service_name("ytmusic"), "youtube_music")

    def test_service_checker_registration_defaults(self) -> None:
        self.assertIn("chatgpt", SERVICE_CHECKERS)
        self.assertIn("claude", SERVICE_CHECKERS)
        self.assertNotIn("bilibili_mainland", SERVICE_CHECKERS)
        self.assertNotIn("bilibili_hk_mc_tw", SERVICE_CHECKERS)

    def test_claude_aliases_and_blocked_codes(self) -> None:
        self.assertEqual(normalize_service_name("anthropic"), "claude")
        self.assertIn("CN", CLAUDE_BLOCKED_CODES)
        self.assertIn("HK", CLAUDE_BLOCKED_CODES)

    def test_parse_trace_country(self) -> None:
        self.assertEqual(parse_trace_country("ip=1.1.1.1\nloc=sg\n"), "SG")
        self.assertIsNone(parse_trace_country("ip=1.1.1.1\n"))

    def test_extract_youtube_music_api_config(self) -> None:
        self.assertEqual(
            extract_youtube_music_api_config(
                '{"INNERTUBE_API_KEY":"key","INNERTUBE_CLIENT_VERSION":"1.0","gl":"TW"}'
            ),
            ("key", "1.0", "TW"),
        )

    def test_parse_youtube_music_player_response(self) -> None:
        self.assertEqual(
            parse_youtube_music_player_response(
                {
                    "playabilityStatus": {"status": "OK"},
                    "streamingData": {"formats": []},
                }
            ),
            "Yes",
        )
        self.assertEqual(
            parse_youtube_music_player_response(
                {
                    "playabilityStatus": {"status": "UNPLAYABLE"},
                    "videoDetails": {"title": "Despacito"},
                }
            ),
            "No",
        )
        self.assertEqual(
            parse_youtube_music_player_response(
                {
                    "playabilityStatus": {
                        "status": "LOGIN_REQUIRED",
                        "reason": "Sign in to confirm you are not a bot",
                    },
                }
            ),
            "Yes",
        )


if __name__ == "__main__":
    unittest.main()
