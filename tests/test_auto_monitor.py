import unittest
from unittest.mock import AsyncMock, patch

from clash_auto_switch.auto_monitor import match_auto_trigger_service, run_auto_check
from clash_auto_switch.clash_api import ClashLogEntry
from clash_auto_switch.defs import ClashConfig, ProxyServicePair
from clash_auto_switch.proxy_switcher import SwitchAttemptResult


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


class AutoSwitchNotificationTest(unittest.IsolatedAsyncioTestCase):
    async def test_auto_check_notifies_when_switched(self) -> None:
        last_switch_at = {}
        task_config = ProxyServicePair("Youtube-Music", "youtube_music")

        with (
            patch(
                "clash_auto_switch.auto_monitor.switch_until_service_available",
                new=AsyncMock(return_value=SwitchAttemptResult(ok=True, switched=True, attempts=2)),
            ),
            patch("clash_auto_switch.auto_monitor.notify_user", return_value=True) as notify,
        ):
            await run_auto_check(
                clash=object(),
                task_config=task_config,
                clash_config=ClashConfig(),
                storage=object(),
                service_name="youtube_music",
                last_switch_at=last_switch_at,
                switch_allowed=True,
                switch_block_reason=None,
            )

        self.assertIn("youtube_music", last_switch_at)
        notify.assert_called_once()

    async def test_auto_check_does_not_notify_without_switch(self) -> None:
        last_switch_at = {}
        task_config = ProxyServicePair("Youtube-Music", "youtube_music")

        with (
            patch(
                "clash_auto_switch.auto_monitor.switch_until_service_available",
                new=AsyncMock(return_value=SwitchAttemptResult(ok=False, switched=False, attempts=1)),
            ),
            patch("clash_auto_switch.auto_monitor.notify_user", return_value=True) as notify,
        ):
            await run_auto_check(
                clash=object(),
                task_config=task_config,
                clash_config=ClashConfig(),
                storage=object(),
                service_name="youtube_music",
                last_switch_at=last_switch_at,
                switch_allowed=False,
                switch_block_reason="cooldown",
            )

        self.assertEqual(last_switch_at, {})
        notify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
