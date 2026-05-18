import unittest

from clash_auto_switch.auto_monitor import match_auto_trigger_service
from clash_auto_switch.core.clash_api import ClashLogEntry


class AutoTriggerTest(unittest.TestCase):
    def make_entry(self, host: str) -> ClashLogEntry:
        return ClashLogEntry.from_api_item(
            {
                "type": "info",
                "payload": f"[TCP] 127.0.0.1:12345(browser.exe) --> {host}:443 match Match using Proxy[node-a]",
            }
        )

    def test_youtube_music_log_triggers_youtube_music(self) -> None:
        self.assertEqual(
            match_auto_trigger_service(self.make_entry("music.youtube.com")),
            "youtube_music",
        )

    def test_common_service_logs_trigger_expected_service(self) -> None:
        cases = {
            "api.bilibili.com": "bilibili_mainland",
            "chatgpt.com": "chatgpt",
            "claude.ai": "claude",
            "gemini.google.com": "gemini",
            "www.youtube.com": "youtube_premium",
            "ani.gamer.com.tw": "bahamut_anime",
            "www.netflix.com": "netflix",
            "www.disneyplus.com": "disney_plus",
            "www.primevideo.com": "prime_video",
        }

        for host, expected_service in cases.items():
            with self.subTest(host=host):
                self.assertEqual(
                    match_auto_trigger_service(self.make_entry(host)),
                    expected_service,
                )

    def test_unknown_log_does_not_trigger(self) -> None:
        self.assertIsNone(match_auto_trigger_service(self.make_entry("example.com")))

    def test_youtube_music_has_priority_over_youtube_premium(self) -> None:
        self.assertEqual(
            match_auto_trigger_service(self.make_entry("music.youtube.com")),
            "youtube_music",
        )


if __name__ == "__main__":
    unittest.main()
