import unittest

from clash_auto_switch.defs import ClashConfig, ProxyServicePair, ServiceRecord
from clash_auto_switch.core.proxy_switcher import (
    list_alive_proxy_candidates,
    switch_to_next_ranked_proxy,
    switch_until_service_available,
)
from clash_auto_switch.tui.monitor import (
    MonitorTui,
    NodeScore,
    _node_display_name,
    _visible_node_window,
    build_node_scores,
)


class FakeStorage:
    def get_records_by_node(self, _node: str, _proxy_group: str) -> list:
        return []

    def get_node_service_record(
        self,
        _node: str,
        _service_name: str,
        _proxy_group: str,
    ) -> ServiceRecord | None:
        return None

    def record_node_status(
        self,
        node_name: str,
        service_name: str,
        proxy_group: str,
        is_available: bool,
    ) -> None:
        pass

class FakeScoreStorage:
    def __init__(self) -> None:
        self.records = {
            "node-a": ServiceRecord(
                service_name="youtube_music",
                last_available_time=None,
                last_check_time=1.0,
                status="failed",
                proxy_group="Youtube-Music",
                reliability_score=0.2,
                total_checks=3,
                successful_checks=1,
            ),
            "node-b": ServiceRecord(
                service_name="youtube_music",
                last_available_time=1.0,
                last_check_time=1.0,
                status="available",
                proxy_group="Youtube-Music",
                reliability_score=0.9,
                total_checks=5,
                successful_checks=5,
            ),
        }

    def get_node_service_record(
        self,
        node: str,
        _service_name: str,
        _proxy_group: str,
    ) -> ServiceRecord | None:
        return self.records.get(node)

class FakeLowScoreStorage:
    def get_records_by_node(self, _node: str, _proxy_group: str) -> list:
        return []

    def get_node_service_record(
        self,
        node: str,
        _service_name: str,
        _proxy_group: str,
    ) -> ServiceRecord | None:
        if node == "node-b":
            return ServiceRecord(
                service_name="youtube_music",
                last_available_time=None,
                last_check_time=1.0,
                status="failed",
                proxy_group="Group",
                reliability_score=0.1,
                total_checks=3,
                successful_checks=0,
            )
        return None

class FakeClashClient:
    def __init__(self, *, verified_now: str) -> None:
        self.verified_now = verified_now
        self.selected: tuple[str, str] | None = None
        self.group_reads = 0

    async def get_proxy(self, name: str) -> dict:
        if name == "Group":
            self.group_reads += 1
            if self.group_reads == 1:
                return {"all": ["node-a", "node-b", "node-c"], "now": "node-a"}
            return {"all": ["node-a", "node-b"], "now": self.verified_now}
        return {"alive": True}

    async def select_proxy(self, selector_name: str, proxy_name: str) -> None:
        self.selected = (selector_name, proxy_name)


class StatefulFakeClashClient:
    def __init__(self) -> None:
        self.current = "node-a"
        self.selected = None

    async def get_proxy(self, name: str) -> dict:
        if name == "Group":
            return {"all": ["node-a", "node-b"], "now": self.current}
        return {"alive": True}

    async def select_proxy(self, selector_name: str, proxy_name: str) -> None:
        self.selected = (selector_name, proxy_name)
        self.current = proxy_name


class ProxySwitcherTest(unittest.IsolatedAsyncioTestCase):
    async def test_select_next_proxy_verifies_switch(self) -> None:
        client = FakeClashClient(verified_now="node-b")

        selected = await switch_to_next_ranked_proxy(
            client,
            "Group",
            "youtube_music",
            FakeStorage(),
        )

        self.assertEqual(selected, "node-b")
        self.assertEqual(client.selected, ("Group", "node-b"))

    async def test_switch_candidates_include_untested_nodes(self) -> None:
        client = FakeClashClient(verified_now="node-c")

        candidates = await list_alive_proxy_candidates(
            client,
            "Group",
            "youtube_music",
            FakeLowScoreStorage(),
        )

        self.assertEqual([candidate.name for candidate in candidates[:2]], ["node-c", "node-a"])
        self.assertEqual(candidates[0].score, 0.5)
        self.assertEqual(candidates[0].status, "untested")

    async def test_select_next_prefers_untested_over_failed_low_score_node(self) -> None:
        client = FakeClashClient(verified_now="node-c")

        selected = await switch_to_next_ranked_proxy(
            client,
            "Group",
            "youtube_music",
            FakeLowScoreStorage(),
        )

        self.assertEqual(selected, "node-c")
        self.assertEqual(client.selected, ("Group", "node-c"))

    async def test_switch_candidates_skip_disabled_nodes(self) -> None:
        client = FakeClashClient(verified_now="node-c")

        candidates = await list_alive_proxy_candidates(
            client,
            "Group",
            "youtube_music",
            FakeStorage(),
            disabled_node_names={"node-b"},
        )

        self.assertNotIn("node-b", [candidate.name for candidate in candidates])

    async def test_switch_until_service_available_rechecks_after_switch(self) -> None:
        client = StatefulFakeClashClient()
        probe_results = iter([(False, "No"), (True, "Yes")])
        switched_nodes = []

        async def probe(_service_name: str, _proxy_url: str | None) -> tuple[bool, str]:
            return next(probe_results)

        async def after_switch(node_name: str) -> None:
            switched_nodes.append(node_name)

        result = await switch_until_service_available(
            client,
            ProxyServicePair("Group", "youtube_music"),
            ClashConfig(),
            FakeStorage(),
            probe_func=probe,
            after_switch=after_switch,
        )

        self.assertTrue(result.ok)
        self.assertTrue(result.switched)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(switched_nodes, ["node-b"])

    async def test_select_next_proxy_fails_when_verification_mismatches(self) -> None:
        client = FakeClashClient(verified_now="node-a")

        with self.assertRaisesRegex(RuntimeError, "switch verification failed"):
            await switch_to_next_ranked_proxy(
                client,
                "Group",
                "youtube_music",
                FakeStorage(),
            )


class TuiNodeScoreTest(unittest.TestCase):
    def test_build_node_scores_sorts_by_score_desc(self) -> None:
        scores = build_node_scores(
            ["node-a", "node-b", "node-c"],
            current_node="node-a",
            service_name="youtube_music",
            proxy_group_name="Youtube-Music",
            storage=FakeScoreStorage(),
            disabled_node_names={"node-c"},
        )

        self.assertEqual([node.name for node in scores], ["node-b", "node-a", "node-c"])
        self.assertEqual(scores[0].score, 0.9)
        self.assertTrue(scores[-1].disabled)

    def test_tui_selection_moves_with_vim_keys(self) -> None:
        tasks = [
            ProxyServicePair("AI", "gemini"),
            ProxyServicePair("Youtube-Music", "youtube_music"),
        ]
        tui = MonitorTui(tasks)
        tui._services["gemini"].nodes = [
            build_node_scores(
                ["node-a", "node-b"],
                current_node="node-a",
                service_name="youtube_music",
                proxy_group_name="Youtube-Music",
                storage=FakeScoreStorage(),
            )[0],
            build_node_scores(
                ["node-a", "node-b"],
                current_node="node-a",
                service_name="youtube_music",
                proxy_group_name="Youtube-Music",
                storage=FakeScoreStorage(),
            )[1],
        ]
        tui._services["youtube_music"].nodes = build_node_scores(
            ["node-a", "node-b"],
            current_node="node-a",
            service_name="youtube_music",
            proxy_group_name="Youtube-Music",
            storage=FakeScoreStorage(),
        )

        task, node = tui.selected_node()
        self.assertEqual((task.service_name, node), ("gemini", "node-b"))

        tui.handle_key("j")
        task, node = tui.selected_node()
        self.assertEqual((task.service_name, node), ("gemini", "node-a"))

        tui.handle_key("l")
        task, node = tui.selected_node()
        self.assertEqual((task.service_name, node), ("youtube_music", "node-b"))
        self.assertEqual(tui.handle_key("enter"), "switch")
        self.assertEqual(tui.handle_key("d"), "toggle_disabled")
        self.assertEqual(tui.handle_key("q"), "quit")

    def test_visible_node_window_keeps_selection_visible(self) -> None:
        nodes = [NodeScore(name=f"node-{index}") for index in range(10)]

        self.assertEqual(
            [index for index, _node in _visible_node_window(nodes, selected_index=0, max_rows=4)],
            [0, 1, 2, 3],
        )
        self.assertEqual(
            [index for index, _node in _visible_node_window(nodes, selected_index=5, max_rows=4)],
            [3, 4, 5, 6],
        )
        self.assertEqual(
            [index for index, _node in _visible_node_window(nodes, selected_index=9, max_rows=4)],
            [6, 7, 8, 9],
        )

    def test_current_node_marker_is_derived_from_service_current_node(self) -> None:
        nodes = [
            NodeScore("node-a", status="available"),
            NodeScore("node-b", status="available"),
            NodeScore("node-c", status="available"),
        ]
        current_node = "node-b"

        labels = [
            _node_display_name(node, current=node.name == current_node)
            for node in nodes
        ]

        self.assertEqual(labels, ["  node-a", "* node-b", "  node-c"])

if __name__ == "__main__":
    unittest.main()
