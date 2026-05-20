import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clash_auto_switch.core.diagnostic_log import DiagnosticLogger


class DiagnosticLoggerTest(unittest.TestCase):
    def test_write_appends_jsonl_under_data_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("clash_auto_switch.core.diagnostic_log.get_data_directory", return_value=Path(temp_dir)):
                logger = DiagnosticLogger()
                logger.write(
                    "auto_trigger",
                    service_name="youtube_music",
                    message="触发检测",
                    current_node="node-a",
                    payload={"host": "music.youtube.com"},
                )

            lines = logger.path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(len(lines), 1)
        payload = json.loads(lines[0])
        self.assertEqual(payload["kind"], "auto_trigger")
        self.assertEqual(payload["service_name"], "youtube_music")
        self.assertEqual(payload["current_node"], "node-a")
        self.assertEqual(payload["payload"]["host"], "music.youtube.com")


if __name__ == "__main__":
    unittest.main()
