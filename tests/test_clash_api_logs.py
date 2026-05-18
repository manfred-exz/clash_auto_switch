import unittest

from clash_auto_switch.core.clash_api import ClashClient, ClashLogEntry


class FakeStreamResponse:
    def raise_for_status(self) -> None:
        pass

    async def aiter_lines(self):
        yield '{"type":"info","payload":"[TCP] 127.0.0.1:1(app.exe) --> example.com:443 match Match using DIRECT"}'


class FakeStreamContext:
    async def __aenter__(self) -> FakeStreamResponse:
        return FakeStreamResponse()

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        pass


class FakeHttpClient:
    def __init__(self) -> None:
        self.timeout = object()

    def stream(self, method: str, url: str, **kwargs):
        self.method = method
        self.url = url
        self.kwargs = kwargs
        return FakeStreamContext()


class ClashLogEntryTest(unittest.TestCase):
    def test_parse_warning_dial_error_log(self) -> None:
        entry = ClashLogEntry.from_api_item(
            {
                "type": "warning",
                "payload": "[TCP] dial DIRECT (match GeoIP/cn) 198.18.0.1:18825(svchost.exe) --> 111.49.206.213:7680 error: dial tcp 111.49.206.213:7680: i/o timeout",
            }
        )

        self.assertTrue(entry.is_connection_log)
        self.assertTrue(entry.is_error)
        self.assertEqual(entry.network, "TCP")
        self.assertEqual(entry.source.host, "198.18.0.1")
        self.assertEqual(entry.source.port, 18825)
        self.assertEqual(entry.source.process, "svchost.exe")
        self.assertEqual(entry.destination.host, "111.49.206.213")
        self.assertEqual(entry.destination.port, 7680)
        self.assertEqual(entry.rule.rule_type, "GeoIP")
        self.assertEqual(entry.rule.rule_value, "cn")
        self.assertEqual(entry.outbound.policy, "DIRECT")
        self.assertEqual(entry.error, "dial tcp 111.49.206.213:7680: i/o timeout")

    def test_parse_success_log_with_policy_node(self) -> None:
        entry = ClashLogEntry.from_api_item(
            {
                "type": "info",
                "payload": "[TCP] 198.18.0.1:18916(OneDrive.Sync.Service.exe) --> accounts.google.com:443 match GeoSite(google) using Google[yushe |  狮城 02]",
            }
        )

        self.assertFalse(entry.is_error)
        self.assertEqual(entry.source.process, "OneDrive.Sync.Service.exe")
        self.assertEqual(entry.destination.host, "accounts.google.com")
        self.assertEqual(entry.destination.port, 443)
        self.assertEqual(entry.rule.raw, "GeoSite(google)")
        self.assertEqual(entry.rule.rule_type, "GeoSite")
        self.assertEqual(entry.rule.rule_value, "google")
        self.assertEqual(entry.outbound.policy, "Google")
        self.assertEqual(entry.outbound.selected, "yushe |  狮城 02")

    def test_parse_ipv6_log(self) -> None:
        entry = ClashLogEntry.from_api_item(
            {
                "type": "info",
                "payload": "[TCP] [fdfe:dcba:9876::1]:18960(svchost.exe) --> [2001:0:14c9:d502:1421:2d31:20a0:8dbb]:7680 match Match using 其他[yushe |  狮城 02]",
            }
        )

        self.assertEqual(entry.source.host, "fdfe:dcba:9876::1")
        self.assertEqual(entry.source.port, 18960)
        self.assertEqual(entry.destination.host, "2001:0:14c9:d502:1421:2d31:20a0:8dbb")
        self.assertEqual(entry.destination.port, 7680)
        self.assertEqual(entry.rule.rule_type, "Match")
        self.assertEqual(entry.rule.rule_value, None)
        self.assertEqual(entry.outbound.policy, "其他")
        self.assertEqual(entry.outbound.selected, "yushe |  狮城 02")


class ClashClientLogStreamTest(unittest.IsolatedAsyncioTestCase):
    async def test_iter_logs_disables_read_timeout_for_long_lived_stream(self) -> None:
        client = ClashClient.__new__(ClashClient)
        fake_http_client = FakeHttpClient()
        client._client = fake_http_client

        entry = await anext(client.iter_logs(level="info"))

        self.assertEqual(entry.destination.host, "example.com")
        self.assertIsNone(fake_http_client.kwargs["timeout"])
        self.assertEqual(fake_http_client.kwargs["params"], {"level": "info"})


if __name__ == "__main__":
    unittest.main()
