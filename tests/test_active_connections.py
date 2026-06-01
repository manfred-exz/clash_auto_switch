import unittest

from clash_auto_switch.core.clash_api import connection_matches_patterns
from clash_auto_switch.core.services.registry import get_service


class ActiveConnectionTest(unittest.TestCase):
    def test_youtube_music_uses_googlevideo_as_active_signal(self) -> None:
        patterns = get_service("youtube_music").host_patterns
        self.assertIsNotNone(patterns)
        self.assertEqual(
            patterns.active_connection_hosts,
            ("googlevideo.com",),
        )

    def test_connection_pattern_matches_metadata_host(self) -> None:
        connection = {
            "metadata": {
                "host": "rr1---sn-i3beln7e.googlevideo.com",
                "destinationIP": "203.0.113.1",
            }
        }

        self.assertTrue(connection_matches_patterns(connection, ("googlevideo.com",)))


if __name__ == "__main__":
    unittest.main()
