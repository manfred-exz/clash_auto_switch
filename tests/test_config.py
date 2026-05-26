import unittest
from unittest.mock import patch

from clash_auto_switch.app_context import AppContext
from clash_auto_switch.config import (
    add_task_to_config,
    parse_config_data,
)
from clash_auto_switch.core.task import ServiceTask
from clash_auto_switch.defs import AppConfig, ClashConfig, MonitoringConfig, ProxyServicePair


def make_task(config: AppConfig, pair: ProxyServicePair) -> ServiceTask:
    return ServiceTask.from_pair(pair, AppContext(config, storage=object(), diagnostics=object(), check_scheduler=object()))


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

        task = make_task(config, config.tasks[0])
        self.assertEqual(task.disabled_node_names(), {"node-a"})

    def test_service_task_toggle_node_disabled_disables_and_enables(self) -> None:
        task = ProxyServicePair("Youtube", "youtube_music")
        config = AppConfig(ClashConfig(), MonitoringConfig(), [task])
        service_task = make_task(config, task)

        with patch("clash_auto_switch.core.task.save_app_config", return_value=True):
            self.assertTrue(service_task.toggle_node_disabled("node-a"))
            self.assertEqual(service_task.disabled_node_names(), {"node-a"})

            self.assertTrue(service_task.toggle_node_disabled("node-a"))
            self.assertEqual(service_task.disabled_node_names(), set())

    def test_add_task_to_config_updates_and_persists_config(self) -> None:
        config = AppConfig(ClashConfig(), MonitoringConfig(), [])
        task = ProxyServicePair("Youtube", "youtube_music")

        with patch("clash_auto_switch.config.save_config", return_value=True) as save:
            self.assertTrue(add_task_to_config(config, task))

        self.assertEqual(config.tasks, [task])
        saved_data = save.call_args.args[0]
        self.assertEqual(saved_data["tasks"][0]["service_name"], "youtube_music")


if __name__ == "__main__":
    unittest.main()
