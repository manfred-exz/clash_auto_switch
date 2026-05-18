import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clash_auto_switch.core.storage import NodeHistoryStorage


class NodeHistoryStorageTest(unittest.TestCase):
    def make_storage(self, data: dict | None = None) -> NodeHistoryStorage:
        self.temp_dir = tempfile.TemporaryDirectory()
        data_file = Path(self.temp_dir.name) / "node_history.json"
        if data is not None:
            data_file.write_text(json.dumps(data), encoding="utf-8")

        patcher = patch("clash_auto_switch.core.storage.get_data_file_path", return_value=data_file)
        self.addCleanup(patcher.stop)
        patcher.start()
        self.addCleanup(self.temp_dir.cleanup)
        return NodeHistoryStorage()

    def test_legacy_records_keep_proxy_group(self) -> None:
        storage = self.make_storage(
            {
                "Youtube#youtube_premium": [
                    {
                        "node_name": "node-a",
                        "service_name": "youtube_premium",
                        "proxy_group": "Youtube",
                        "last_available_time": None,
                        "last_check_time": 1.0,
                        "status": "failed",
                        "reliability_score": 0.2,
                        "total_checks": 2,
                    }
                ]
            }
        )

        self.assertEqual(storage.get_records_by_service("youtube_premium", "Youtube")[0][0], "node-a")
        self.assertEqual(storage.get_records_by_service("youtube_premium", "Youtube-Music"), [])

    def test_same_node_service_is_separated_by_proxy_group(self) -> None:
        storage = self.make_storage()

        storage.record_node_status("node-a", "youtube_premium", "Youtube", True, check_time=1.0)
        storage.record_node_status("node-a", "youtube_premium", "Youtube-Music", False, check_time=2.0)

        youtube = storage.get_node_service_record("node-a", "youtube_premium", "Youtube")
        youtube_music = storage.get_node_service_record("node-a", "youtube_premium", "Youtube-Music")

        self.assertEqual(youtube.status, "available")
        self.assertEqual(youtube_music.status, "failed")

    def test_node_disabled_state_is_separated_by_service_and_group(self) -> None:
        storage = self.make_storage()

        storage.set_node_disabled("node-a", "youtube_music", "Youtube", True)

        self.assertTrue(storage.is_node_disabled("node-a", "youtube_music", "Youtube"))
        self.assertFalse(storage.is_node_disabled("node-a", "youtube_music", "Youtube-Music"))
        self.assertFalse(storage.is_node_disabled("node-a", "gemini", "Youtube"))

    def test_toggle_node_disabled_returns_new_state(self) -> None:
        storage = self.make_storage()

        self.assertTrue(storage.toggle_node_disabled("node-a", "youtube_music", "Youtube"))
        self.assertTrue(storage.is_node_disabled("node-a", "youtube_music", "Youtube"))
        self.assertFalse(storage.toggle_node_disabled("node-a", "youtube_music", "Youtube"))
        self.assertFalse(storage.is_node_disabled("node-a", "youtube_music", "Youtube"))


if __name__ == "__main__":
    unittest.main()
