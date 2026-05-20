import unittest

from clash_auto_switch.core.service_hosts import (
    SERVICE_HOST_PATTERNS,
    auto_trigger_host_patterns,
    connection_host_patterns,
)


class ServiceHostPatternsTest(unittest.TestCase):
    def test_auto_trigger_and_connection_patterns_share_registry(self) -> None:
        trigger_patterns = auto_trigger_host_patterns()

        self.assertIn("youtube_music", SERVICE_HOST_PATTERNS)
        self.assertEqual(
            trigger_patterns["youtube_music"],
            SERVICE_HOST_PATTERNS["youtube_music"].trigger_hosts,
        )

    def test_youtube_music_trigger_stays_narrow_but_connections_are_broad(self) -> None:
        self.assertNotIn("googlevideo.com", auto_trigger_host_patterns()["youtube_music"])
        self.assertIn("googlevideo.com", connection_host_patterns("youtube_music"))

    def test_emby_as174_host_patterns(self) -> None:
        self.assertEqual(auto_trigger_host_patterns()["emby_as174"], ("emby.as174.de",))
        self.assertEqual(connection_host_patterns("emby_as174"), ("emby.as174.de",))


if __name__ == "__main__":
    unittest.main()
