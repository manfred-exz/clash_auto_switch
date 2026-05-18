import unittest

from clash_auto_switch.core.clash_state import ClashProxyState


class FakeClashClient:
    def __init__(self) -> None:
        self.current = "node-a"
        self.get_proxies_calls = 0

    async def get_proxies(self) -> dict:
        self.get_proxies_calls += 1
        return {
            "proxies": {
                "Group": {
                    "name": "Group",
                    "type": "Selector",
                    "all": ["node-a", "node-b"],
                    "now": self.current,
                },
                "node-a": {"name": "node-a", "alive": True},
                "node-b": {"name": "node-b", "alive": True},
            }
        }

    async def get_proxy(self, name: str) -> dict:
        if name == "Group":
            return {"name": "Group", "all": ["node-a", "node-b"], "now": self.current}
        return {"name": name, "alive": True}

    async def select_proxy(self, _selector_name: str, proxy_name: str) -> None:
        self.current = proxy_name


class ClashProxyStateTest(unittest.IsolatedAsyncioTestCase):
    async def test_get_proxy_group_uses_proxies_snapshot(self) -> None:
        client = FakeClashClient()
        state = ClashProxyState(client, ttl_sec=60.0)

        group = await state.get_proxy_group("Group")

        self.assertEqual(group.now, "node-a")
        self.assertEqual(group.nodes, ["node-a", "node-b"])
        self.assertEqual(client.get_proxies_calls, 1)

    async def test_select_proxy_refreshes_and_verifies_group_now(self) -> None:
        client = FakeClashClient()
        state = ClashProxyState(client, ttl_sec=60.0)
        await state.get_proxy_group("Group")

        await state.select_proxy("Group", "node-b")
        group = await state.get_proxy_group("Group")

        self.assertEqual(group.now, "node-b")
        self.assertEqual(client.get_proxies_calls, 2)


if __name__ == "__main__":
    unittest.main()
