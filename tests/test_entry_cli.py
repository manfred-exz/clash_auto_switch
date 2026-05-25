import unittest
from io import StringIO
from unittest.mock import patch

from clash_auto_switch.entry import parse_args


class EntryCliTest(unittest.TestCase):
    def parse(self, *args: str):
        with patch("sys.argv", ["clash-auto-switch", *args]):
            return parse_args()

    def test_no_command_defaults_to_tui(self) -> None:
        self.assertIsNone(self.parse().command)

    def test_stats_detail_parses_proxy_group_and_service(self) -> None:
        args = self.parse("stats", "Youtube", "youtube_music")

        self.assertEqual(args.command, "stats")
        self.assertEqual(args.proxy_group, "Youtube")
        self.assertEqual(args.service, "youtube_music")

    def test_legacy_flags_are_not_supported(self) -> None:
        with patch("sys.argv", ["clash-auto-switch", "--auto"]), patch("sys.stderr", StringIO()):
            with self.assertRaises(SystemExit):
                parse_args()


    def test_unknown_command_exits(self) -> None:
        with patch("sys.argv", ["clash-auto-switch", "unknown"]), patch("sys.stderr", StringIO()):
            with self.assertRaises(SystemExit):
                parse_args()


if __name__ == "__main__":
    unittest.main()
