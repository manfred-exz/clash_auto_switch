import unittest
from unittest.mock import patch

from clash_auto_switch.config import (
    disable_node_for_task,
    disabled_node_names_for_task,
    dump_config_data,
    parse_config_data,
    toggle_node_disabled_for_task,
)
from clash_auto_switch.defs import AppConfig, ClashConfig, MonitoringConfig, ProxyServicePair


class ConfigTest(unittest.TestCase):
    def test_parse_disabled_nodes(self) -> None:
        config = parse_config_data(
            {
                "tasks": [
                    {
                        "proxy_group_name": "Youtube",
                        "service_name": "youtube_music",
                        "enabled": True,
                    }
                ],
                "disabled_nodes": [
                    {
                        "proxy_group_name": "Youtube",
                        "service_name": "youtube_music",
                        "node_name": "node-a",
                    }
                ],
            }
        )

        self.assertEqual(
            disabled_node_names_for_task(config, config.tasks[0]),
            {"node-a"},
        )

    def test_disable_node_for_task_updates_and_persists_config(self) -> None:
        task = ProxyServicePair("Youtube", "youtube_music")
        config = AppConfig(ClashConfig(), MonitoringConfig(), [task])

        with patch("clash_auto_switch.config.save_config", return_value=True) as save:
            self.assertTrue(disable_node_for_task(config, task, "node-a"))

        self.assertEqual(disabled_node_names_for_task(config, task), {"node-a"})
        saved_data = save.call_args.args[0]
        self.assertEqual(saved_data["disabled_nodes"][0]["node_name"], "node-a")

    def test_dump_config_includes_disabled_nodes(self) -> None:
        task = ProxyServicePair("Youtube", "youtube_music")
        config = AppConfig(ClashConfig(), MonitoringConfig(), [task])
        with patch("clash_auto_switch.config.save_config", return_value=True):
            disable_node_for_task(config, task, "node-a")

        data = dump_config_data(config)

        self.assertEqual(data["disabled_nodes"][0]["node_name"], "node-a")

    def test_toggle_node_disabled_for_task_disables_and_enables(self) -> None:
        task = ProxyServicePair("Youtube", "youtube_music")
        config = AppConfig(ClashConfig(), MonitoringConfig(), [task])

        with patch("clash_auto_switch.config.save_config", return_value=True):
            self.assertTrue(toggle_node_disabled_for_task(config, task, "node-a"))
            self.assertEqual(disabled_node_names_for_task(config, task), {"node-a"})

            self.assertTrue(toggle_node_disabled_for_task(config, task, "node-a"))
            self.assertEqual(disabled_node_names_for_task(config, task), set())


if __name__ == "__main__":
    unittest.main()
