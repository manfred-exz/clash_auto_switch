import unittest
from unittest.mock import AsyncMock, patch

from clash_auto_switch.app_context import AppContext
from clash_auto_switch.core.task import ServiceTask
from clash_auto_switch.defs import AppConfig, ClashConfig, MonitoringConfig, ProxyServicePair, ServiceRecord


class FakeStorage:
    def __init__(self) -> None:
        self.records = []

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
        self.records.append((node_name, service_name, proxy_group, is_available))

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

    async def get_proxy_group(self, name: str):
        if name == "Group":
            return type("GroupState", (), {"now": self.current})()
        raise KeyError(name)

    async def get_proxy(self, name: str) -> dict:
        if name == "Group":
            return {"all": ["node-a", "node-b"], "now": self.current}
        return {"alive": True}

    async def select_proxy(self, selector_name: str, proxy_name: str) -> None:
        self.selected = (selector_name, proxy_name)
        self.current = proxy_name


class NoopDiagnostics:
    def write(self, *args, **kwargs) -> None:
        pass


def make_service_task(storage: FakeStorage, clash: StatefulFakeClashClient) -> ServiceTask:
    pair = ProxyServicePair("Group", "youtube_music")
    config = AppConfig(ClashConfig(), MonitoringConfig(), [pair])
    app = AppContext(config, storage=storage, diagnostics=NoopDiagnostics(), check_scheduler=object(), _clash=clash)
    return ServiceTask.from_pair(pair, app)


class ProxySwitcherTest(unittest.IsolatedAsyncioTestCase):
    async def test_switch_candidates_prefer_untested_over_failed_low_score_node(self) -> None:
        client = FakeClashClient(verified_now="node-c")

        candidates = await make_service_task(FakeLowScoreStorage(), client).list_alive_proxy_candidates()

        self.assertEqual(candidates[0].name, "node-c")
        self.assertEqual(candidates[0].score, 0.5)

    async def test_service_task_switch_until_available_rechecks_after_switch(self) -> None:
        client = StatefulFakeClashClient()
        probe_results = iter([(False, "No"), (True, "Yes")])
        switched_nodes = []

        async def fake_probe(_service_name: str, _proxy_url: str | None) -> tuple[bool, str]:
            return next(probe_results)

        async def fake_connectivity(_proxy_url: str | None) -> tuple[bool, str]:
            return True, "ok"

        async def after_switch(node_name: str) -> None:
            switched_nodes.append(node_name)

        with patch("clash_auto_switch.core.task.probe_service", fake_probe), patch(
            "clash_auto_switch.core.task.check_proxy_connectivity",
            fake_connectivity,
        ):
            result = await make_service_task(FakeStorage(), client).switch_until_available(
                after_switch=after_switch,
            )

        self.assertTrue(result.ok)
        self.assertTrue(result.switched)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(switched_nodes, ["node-b"])

    async def test_connectivity_failure_switches_without_recording_score(self) -> None:
        client = StatefulFakeClashClient()
        storage = FakeStorage()

        async def fake_connectivity(_proxy_url: str | None) -> tuple[bool, str]:
            return False, "Cloudflare connectivity failed"

        probe = AsyncMock(return_value=(True, "Yes"))
        with patch("clash_auto_switch.core.task.probe_service", probe), patch(
            "clash_auto_switch.core.task.check_proxy_connectivity",
            fake_connectivity,
        ):
            result = await make_service_task(storage, client).switch_until_available(max_attempts=1)

        self.assertFalse(result.ok)
        self.assertTrue(result.switched)
        self.assertEqual(client.current, "node-b")
        self.assertEqual(storage.records, [])
        probe.assert_not_called()

if __name__ == "__main__":
    unittest.main()
