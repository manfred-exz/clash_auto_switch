import unittest

from clash_auto_switch.connections import (
    close_service_connections,
    connection_matches_service,
)


class FakeClashClient:
    def __init__(self) -> None:
        self.closed = []

    async def get_connections(self) -> dict:
        return {
            "connections": [
                {
                    "id": "music",
                    "metadata": {
                        "host": "music.youtube.com",
                        "destinationIP": "1.1.1.1",
                    },
                },
                {
                    "id": "video",
                    "metadata": {
                        "host": "rr1---sn.example.googlevideo.com",
                    },
                },
                {
                    "id": "other",
                    "metadata": {
                        "host": "example.com",
                    },
                },
            ]
        }

    async def close_connection(self, connection_id: str) -> None:
        self.closed.append(connection_id)


class ConnectionCleanupTest(unittest.TestCase):
    def test_connection_matches_youtube_music_hosts(self) -> None:
        self.assertTrue(
            connection_matches_service(
                {"metadata": {"host": "music.youtube.com"}},
                "youtube_music",
            )
        )
        self.assertTrue(
            connection_matches_service(
                {"metadata": {"host": "rr1---sn.example.googlevideo.com"}},
                "youtube_music",
            )
        )
        self.assertFalse(
            connection_matches_service(
                {"metadata": {"host": "example.com"}},
                "youtube_music",
            )
        )


class ConnectionCleanupAsyncTest(unittest.IsolatedAsyncioTestCase):
    async def test_close_service_connections_only_closes_matching_connections(self) -> None:
        client = FakeClashClient()

        closed_count = await close_service_connections(client, "youtube_music")

        self.assertEqual(closed_count, 2)
        self.assertEqual(client.closed, ["music", "video"])


if __name__ == "__main__":
    unittest.main()
