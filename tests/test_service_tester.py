import unittest

from clash_auto_switch.core.services.registry import get_service
from clash_auto_switch.core.services.youtube_music import (
    parse_youtube_music_api_response,
    parse_youtube_music_page,
    parse_youtube_music_player_response,
    summarize_youtube_music_player_statuses,
)
from clash_auto_switch.core.services.youtube_premium import parse_youtube_premium_page
from clash_auto_switch.core.services.tiktok import parse_tiktok_region


class ServiceTesterTest(unittest.TestCase):
    def test_parse_youtube_music_region_available_page(self) -> None:
        status, region = parse_youtube_music_page(
            """
            <script>window.dataLayer.push({'country_code': 'TW'});</script>
            <script>ytcfg.set({"INNERTUBE_CONTEXT":{"client":{"GL":"TW"}}});</script>
            <a href="https://music.youtube.com/youtubei/v1/search">YouTube Music</a>
            """
        )

        self.assertEqual(status, "Yes")
        self.assertEqual(region, "🇹🇼TW")

    def test_parse_youtube_music_region_unavailable_page(self) -> None:
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

    def test_service_checker_registration_defaults(self) -> None:
        self.assertEqual(get_service("chatgpt").service_name, "chatgpt")
        self.assertEqual(get_service("claude").service_name, "claude")
        self.assertEqual(get_service("bilibili_mainland").service_name, "bilibili_mainland")
        self.assertEqual(get_service("bilibili_hk_mc_tw").service_name, "bilibili_hk_mc_tw")
        self.assertEqual(get_service("tiktok").service_name, "tiktok")

    def test_parse_tiktok_region_page(self) -> None:
        self.assertEqual(parse_tiktok_region('{"region":"US"}'), "US")
        self.assertEqual(parse_tiktok_region('some random text "region" : "JP" more text'), "JP")
        self.assertIsNone(parse_tiktok_region('{"no_region":"US"}'))

    def test_parse_youtube_premium_available_page(self) -> None:
        self.assertEqual(
            parse_youtube_premium_page('ytcfg.set({"INNERTUBE_API_KEY":"key","GL":"SG"});'),
            ("Yes", "🇸🇬SG"),
        )

    def test_youtube_music_player_status_controls_final_result(self) -> None:
        self.assertEqual(
            summarize_youtube_music_player_statuses(["No", "Yes"]),
            "Yes",
        )
        self.assertEqual(
            summarize_youtube_music_player_statuses(["No", "No"]),
            "No",
        )
        self.assertEqual(
            summarize_youtube_music_player_statuses(
                ["No", "Failed (Player ERROR)"]
            ),
            "Failed (Player ERROR)",
        )

    def test_youtube_music_api_and_player_responses(self) -> None:
        self.assertEqual(
            parse_youtube_music_api_response({"contents": {"sectionListRenderer": {}}}),
            "Yes",
        )
        self.assertEqual(
            parse_youtube_music_api_response(
                {"error": {"code": 403, "status": "PERMISSION_DENIED"}}
            ),
            "Failed (API 403)",
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
