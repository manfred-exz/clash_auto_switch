import unittest

from clash_auto_switch.proxy_switcher import select_next_proxy_in_group


class FakeStorage:
    def get_records_by_node(self, _node: str, _proxy_group: str) -> list:
        return []


class FakeClashClient:
    def __init__(self, *, verified_now: str) -> None:
        self.verified_now = verified_now
        self.selected: tuple[str, str] | None = None
        self.group_reads = 0

    async def get_proxy(self, name: str) -> dict:
        if name == "Group":
            self.group_reads += 1
            if self.group_reads == 1:
                return {"all": ["node-a", "node-b"], "now": "node-a"}
            return {"all": ["node-a", "node-b"], "now": self.verified_now}
        return {"alive": True}

    async def select_proxy(self, selector_name: str, proxy_name: str) -> None:
        self.selected = (selector_name, proxy_name)


class ProxySwitcherTest(unittest.IsolatedAsyncioTestCase):
    async def test_select_next_proxy_verifies_switch(self) -> None:
        client = FakeClashClient(verified_now="node-b")

        selected = await select_next_proxy_in_group(
            client,
            "Group",
            "youtube_music",
            FakeStorage(),
        )

        self.assertEqual(selected, "node-b")
        self.assertEqual(client.selected, ("Group", "node-b"))

    async def test_select_next_proxy_fails_when_verification_mismatches(self) -> None:
        client = FakeClashClient(verified_now="node-a")

        with self.assertRaisesRegex(RuntimeError, "switch verification failed"):
            await select_next_proxy_in_group(
                client,
                "Group",
                "youtube_music",
                FakeStorage(),
            )


if __name__ == "__main__":
    unittest.main()
