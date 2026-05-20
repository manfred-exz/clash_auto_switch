import unittest

from clash_auto_switch.core.clash_state import ProxyGroupState
from clash_auto_switch.defs import ProxyServicePair
from clash_auto_switch.tui.monitor import (
    MonitorTui,
    build_connection_rows,
    build_node_scores,
    _node_table_signature,
)
from textual.widgets import DataTable


class FakeStorage:
    def get_node_service_record(self, _node: str, _service_name: str, _proxy_group_name: str):
        return None


class TuiConnectionRowsTest(unittest.TestCase):
    def test_build_connection_rows_filters_formats_and_sorts_service_connections(self) -> None:
        rows = build_connection_rows(
            {
                "connections": [
                    {
                        "metadata": {"host": "example.com", "network": "tcp"},
                        "upload": 999999,
                        "download": 999999,
                    },
                    {
                        "metadata": {"host": "music.youtube.com", "network": "tcp"},
                        "rule": "DOMAIN-SUFFIX",
                        "rulePayload": "youtube.com",
                        "chains": ["Youtube", "node-a"],
                        "upload": 1024,
                        "download": 2048,
                    },
                    {
                        "metadata": {"host": "rr1---sn.example.googlevideo.com", "network": "udp"},
                        "rule": "MATCH",
                        "chains": ["Youtube", "node-b"],
                        "upload": 100,
                        "download": 100,
                    },
                ]
            },
            "youtube_music",
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].host, "music.youtube.com")
        self.assertEqual(rows[0].rule, "DOMAIN-SUFFIX youtube.com")
        self.assertEqual(rows[0].chain, "Youtube > node-a")
        self.assertEqual(rows[0].traffic, "U 1.0KiB / D 2.0KiB")
        self.assertEqual(rows[0].network, "tcp")

    def test_build_node_scores_sorts_disabled_nodes_last(self) -> None:
        rows = build_node_scores(
            ["node-a", "node-b"],
            current_node="node-b",
            service_name="youtube_music",
            proxy_group_name="Youtube",
            storage=FakeStorage(),
            disabled_node_names={"node-a"},
        )

        self.assertEqual([row.name for row in rows], ["node-b", "node-a"])
        self.assertFalse(rows[0].disabled)
        self.assertTrue(rows[1].disabled)

    def test_node_table_signature_is_stable_for_equivalent_refresh(self) -> None:
        rows = build_node_scores(
            ["node-a", "node-b"],
            current_node="node-b",
            service_name="youtube_music",
            proxy_group_name="Youtube",
            storage=FakeStorage(),
        )

        self.assertEqual(
            _node_table_signature(rows, "node-b"),
            _node_table_signature(list(rows), "node-b"),
        )


class MonitorTuiTextualTest(unittest.IsolatedAsyncioTestCase):
    async def test_bindings_move_selection_and_switch_selected_node(self) -> None:
        task = ProxyServicePair("Youtube", "youtube_music")
        app = MonitorTui([task])
        switched = []

        async def switch_node(task_arg: ProxyServicePair, node_name: str) -> None:
            switched.append((task_arg, node_name))

        app.configure_callbacks(switch_node)

        async with app.run_test() as pilot:
            app.update_service(
                task,
                ProxyGroupState("Youtube", "node-b", ["node-a", "node-b"], {}),
                FakeStorage(),
            )
            await pilot.press("j")
            await pilot.press("enter")

        self.assertEqual(switched, [(task, "node-a")])

    async def test_enter_switches_node_when_table_has_focus(self) -> None:
        task = ProxyServicePair("Youtube", "youtube_music")
        app = MonitorTui([task])
        switched = []

        async def switch_node(task_arg: ProxyServicePair, node_name: str) -> None:
            switched.append((task_arg, node_name))

        app.configure_callbacks(switch_node)

        async with app.run_test() as pilot:
            app.update_service(
                task,
                ProxyGroupState("Youtube", "node-a", ["node-a", "node-b"], {}),
                FakeStorage(),
            )
            pilot.app.query_one("#service-0", DataTable).focus()
            await pilot.press("enter")

        self.assertEqual(switched, [(task, "node-a")])

    async def test_bindings_move_selected_service_and_disable_selected_node(self) -> None:
        youtube = ProxyServicePair("Youtube", "youtube_music")
        ai = ProxyServicePair("AI", "gemini")
        app = MonitorTui([youtube, ai])
        disabled = []

        async def disable_node(task_arg: ProxyServicePair, node_name: str) -> None:
            disabled.append((task_arg, node_name))

        app.configure_callbacks(lambda _task, _node: None, disable_node)

        async with app.run_test() as pilot:
            app.update_service(
                youtube,
                ProxyGroupState("Youtube", "node-y", ["node-y"], {}),
                FakeStorage(),
            )
            app.update_service(
                ai,
                ProxyGroupState("AI", "node-g", ["node-g"], {}),
                FakeStorage(),
            )
            await pilot.press("l")
            self.assertEqual(app.selected_task(), ai)
            await pilot.press("d")

        self.assertEqual(disabled, [(ai, "node-g")])

    async def test_events_pane_can_be_maximized(self) -> None:
        task = ProxyServicePair("Youtube", "youtube_music")
        app = MonitorTui([task])

        async with app.run_test() as pilot:
            await pilot.press("e")
            self.assertTrue(pilot.app.query_one("#events").has_class("events-maximized"))
            self.assertTrue(pilot.app.query_one("#main").has_class("events-maximized"))
            await pilot.press("e")
            self.assertFalse(pilot.app.query_one("#events").has_class("events-maximized"))
            self.assertFalse(pilot.app.query_one("#main").has_class("events-maximized"))

    async def test_status_line_shows_last_refresh_time(self) -> None:
        task = ProxyServicePair("Youtube", "youtube_music")
        app = MonitorTui([task])

        async with app.run_test() as pilot:
            app.update_service(
                task,
                ProxyGroupState("Youtube", "node-a", ["node-a"], {}),
                FakeStorage(),
            )

            status_text = str(pilot.app.query_one("#status").content)
            self.assertIn("最近刷新:", status_text)
            self.assertNotIn("最近刷新: -", status_text)

    async def test_auto_detection_toggle_updates_status_and_callback(self) -> None:
        task = ProxyServicePair("Youtube", "youtube_music")
        app = MonitorTui([task])
        toggles = []

        async def toggle_auto_detection(enabled: bool) -> None:
            toggles.append(enabled)

        app.configure_callbacks(
            lambda _task, _node: None,
            toggle_auto_detection=toggle_auto_detection,
        )

        async with app.run_test() as pilot:
            await pilot.press("a")
            status_text = str(pilot.app.query_one("#status").content)

        self.assertEqual(toggles, [False])
        self.assertIn("自动检测: 关", status_text)

    async def test_manual_check_binding_uses_selected_service(self) -> None:
        youtube = ProxyServicePair("Youtube", "youtube_music")
        ai = ProxyServicePair("AI", "gemini")
        app = MonitorTui([youtube, ai])
        checked = []

        async def check_service(task_arg: ProxyServicePair) -> None:
            checked.append(task_arg)

        app.configure_callbacks(
            lambda _task, _node: None,
            check_service=check_service,
        )

        async with app.run_test() as pilot:
            await pilot.press("l")
            await pilot.press("c")

        self.assertEqual(checked, [ai])

    async def test_event_text_with_brackets_is_rendered_as_plain_text(self) -> None:
        task = ProxyServicePair("Youtube", "youtube_music")
        app = MonitorTui([task])

        async with app.run_test():
            app.event(
                "youtube_music",
                "YTMusic检测 | player=[abc=Yes/LOGIN_REQUIRED] | final=Yes",
            )

        self.assertIn("LOGIN_REQUIRED", app._events[-1])


if __name__ == "__main__":
    unittest.main()
