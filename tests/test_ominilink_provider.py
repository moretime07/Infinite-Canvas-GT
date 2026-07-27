import unittest
from unittest.mock import AsyncMock, patch

import main


class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = {}

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return next(self.responses)


class OminiLinkProviderTests(unittest.TestCase):
    def test_exact_api_hosts_are_recognized(self):
        for url in (
            "https://api.aig-ai.com/v1",
            "https://vg-api.aig-ai.com/v1",
            "https://api.ominilink.ai/v1",
            "https://vg-api.ominilink.ai/v1",
        ):
            with self.subTest(url=url):
                self.assertTrue(main.is_ominilink_api_url(url))

    def test_portal_and_lookalike_hosts_are_rejected(self):
        for url in (
            "https://portal.ominilink.ai/",
            "https://aig-ai.com.evil.test/v1",
            "https://notominilink.ai/v1",
        ):
            with self.subTest(url=url):
                self.assertFalse(main.is_ominilink_api_url(url))

    def test_video_host_migrates_to_dual_urls(self):
        provider = main.normalize_provider({
            "id": "orange",
            "name": "姗欏煙",
            "base_url": "https://vg-api.aig-ai.com/v1",
        })
        self.assertEqual(provider["base_url"], "https://api.aig-ai.com/v1")
        self.assertEqual(provider["video_base_url"], "https://vg-api.aig-ai.com/v1")

    def test_explicit_video_url_wins(self):
        provider = main.normalize_provider({
            "id": "orange",
            "base_url": "https://api.aig-ai.com/v1",
            "video_base_url": "https://video-proxy.example.test/v1",
        })
        self.assertEqual(provider["video_base_url"], "https://video-proxy.example.test/v1")

    def test_exact_host_forces_openai_protocol_over_legacy_ark_setting(self):
        provider = main.normalize_provider({
            "id": "orange",
            "base_url": "https://api.ominilink.ai/v1",
            "protocol": "volcengine",
        })

        self.assertEqual(provider["protocol"], "openai")
        payload = main.TestConnectionPayload(
            base_url="https://api.ominilink.ai/v1", protocol="volcengine",
        )
        self.assertEqual(main.protocol_from_payload(payload), "openai")


class OminiLinkDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_ark_404_is_not_route_success(self):
        client = AsyncMock()
        client.get.return_value = FakeResponse(404, {"error": "not found"}, '{"error":"not found"}')
        ok, _ = await main.probe_volcengine_task_endpoint(
            client, "https://vg-api.aig-ai.com/v1", "secret"
        )
        self.assertFalse(ok)

    async def test_openai_chat_404_is_not_route_success(self):
        client = AsyncMock()
        client.post.return_value = FakeResponse(404, {"error": "not found"}, '{"error":"not found"}')
        ok, _ = await main.probe_openai_compat_bearer_endpoint(
            client, "https://api.aig-ai.com/v1", "secret"
        )
        self.assertFalse(ok)

    async def test_ark_redirect_is_not_route_success(self):
        client = AsyncMock()
        client.get.return_value = FakeResponse(302, {"error": "redirect"}, '{"error":"redirect"}')
        ok, _ = await main.probe_volcengine_task_endpoint(
            client, "https://vg-api.aig-ai.com/v1", "secret"
        )
        self.assertFalse(ok)

    async def test_openai_chat_redirect_is_not_route_success(self):
        client = AsyncMock()
        client.post.return_value = FakeResponse(302, {"error": "redirect"}, '{"error":"redirect"}')
        ok, _ = await main.probe_openai_compat_bearer_endpoint(
            client, "https://api.aig-ai.com/v1", "secret"
        )
        self.assertFalse(ok)

    def test_catalog_marks_omni_as_chat_and_video(self):
        grouped, ids = main.merge_ominilink_model_catalog(
            "https://api.aig-ai.com/v1",
            {"image": [], "chat": ["upstream-chat"], "video": []},
            ["upstream-chat"],
        )
        self.assertIn("gemini-omni-flash-preview", grouped["chat"])
        self.assertIn("gemini-omni-flash-preview", grouped["video"])
        self.assertEqual(ids, sorted(set(ids)))

    def test_catalog_keeps_video_only_models_out_of_chat_and_image(self):
        grouped, _ = main.merge_ominilink_model_catalog(
            "https://api.aig-ai.com/v1",
            {"image": ["viduq3-pro"], "chat": ["viduq3-pro"], "video": []},
            ["viduq3-pro"],
        )

        self.assertNotIn("viduq3-pro", grouped["image"])
        self.assertNotIn("viduq3-pro", grouped["chat"])
        self.assertIn("viduq3-pro", grouped["video"])

    def test_catalog_leaves_non_ominilink_hosts_unchanged(self):
        grouped = {"image": [], "chat": ["upstream-chat"], "video": []}
        merged, ids = main.merge_ominilink_model_catalog(
            "https://api.example.test/v1", grouped, ["upstream-chat"]
        )
        self.assertEqual(merged, grouped)
        self.assertEqual(ids, ["upstream-chat"])

    async def test_fetch_models_404_uses_unverified_catalog(self):
        client = FakeClient([
            FakeResponse(404, {"error": "not found"}, '{"error":"not found"}'),
        ])
        with patch.object(main.httpx, "AsyncClient", return_value=client):
            result = await main.fetch_models_from_upstream(
                "https://api.aig-ai.com/v1", "secret", "openai"
            )
        self.assertIn("gemini-omni-flash-preview", result["video_models"])
        self.assertTrue(result["catalog_fallback"])
        self.assertFalse(result["connection_verified"])
        self.assertEqual(len(client.calls), 1)

    async def test_fetch_models_405_uses_unverified_catalog_with_total(self):
        client = FakeClient([
            FakeResponse(405, {"error": "method not allowed"}, '{"error":"method not allowed"}'),
        ])
        with patch.object(main.httpx, "AsyncClient", return_value=client):
            result = await main.fetch_models_from_upstream(
                "https://api.aig-ai.com/v1", "secret", "openai"
            )
        self.assertTrue(result["catalog_fallback"])
        self.assertFalse(result["connection_verified"])
        self.assertEqual(result["total"], 19)

    async def test_fetch_models_html_uses_unverified_catalog(self):
        client = FakeClient([FakeResponse(200, text="<html>login</html>")])
        with patch.object(main.httpx, "AsyncClient", return_value=client):
            result = await main.fetch_models_from_upstream(
                "https://api.aig-ai.com/v1", "secret", "openai"
            )
        self.assertTrue(result["catalog_fallback"])
        self.assertFalse(result["connection_verified"])
        self.assertEqual(result["total"], 19)

    async def test_fetch_models_timeout_uses_unverified_catalog(self):
        client = FakeClient([main.httpx.TimeoutException("timeout")])
        with patch.object(main.httpx, "AsyncClient", return_value=client):
            result = await main.fetch_models_from_upstream(
                "https://api.aig-ai.com/v1", "secret", "openai"
            )
        self.assertTrue(result["catalog_fallback"])
        self.assertFalse(result["connection_verified"])

    async def test_connection_404_uses_unverified_catalog(self):
        client = FakeClient([
            FakeResponse(404, {"error": "not found"}, '{"error":"not found"}'),
        ])
        payload = main.TestConnectionPayload(
            base_url="https://api.aig-ai.com/v1", api_key="secret", protocol="openai"
        )
        with patch.object(main.httpx, "AsyncClient", return_value=client):
            result = await main.test_provider_connection(payload)
        self.assertTrue(result["ok"])
        self.assertTrue(result["catalog_fallback"])
        self.assertFalse(result["connection_verified"])
        self.assertIn("未验证当前 API Key 的实际模型权限", result["message"])

    async def test_connection_405_uses_unverified_catalog(self):
        client = FakeClient([
            FakeResponse(405, {"error": "method not allowed"}, '{"error":"method not allowed"}'),
        ])
        payload = main.TestConnectionPayload(
            base_url="https://api.aig-ai.com/v1", api_key="secret", protocol="openai"
        )
        with patch.object(main.httpx, "AsyncClient", return_value=client):
            result = await main.test_provider_connection(payload)
        self.assertTrue(result["ok"])
        self.assertTrue(result["catalog_fallback"])
        self.assertFalse(result["connection_verified"])

    async def test_connection_html_uses_unverified_catalog(self):
        client = FakeClient([FakeResponse(200, text="<html>login</html>")])
        payload = main.TestConnectionPayload(
            base_url="https://api.aig-ai.com/v1", api_key="secret", protocol="openai"
        )
        with patch.object(main.httpx, "AsyncClient", return_value=client):
            result = await main.test_provider_connection(payload)
        self.assertTrue(result["ok"])
        self.assertTrue(result["catalog_fallback"])
        self.assertFalse(result["connection_verified"])

    async def test_connection_timeout_uses_unverified_catalog(self):
        client = FakeClient([main.httpx.TimeoutException("timeout")])
        payload = main.TestConnectionPayload(
            base_url="https://api.aig-ai.com/v1", api_key="secret", protocol="openai"
        )
        with patch.object(main.httpx, "AsyncClient", return_value=client):
            result = await main.test_provider_connection(payload)
        self.assertTrue(result["ok"])
        self.assertTrue(result["catalog_fallback"])
        self.assertFalse(result["connection_verified"])

    async def test_html_auth_failures_never_use_catalog_fallback(self):
        for status_code in (401, 403):
            with self.subTest(entry="connection", status_code=status_code):
                client = FakeClient([FakeResponse(status_code, text="<html>login</html>")])
                payload = main.TestConnectionPayload(
                    base_url="https://api.aig-ai.com/v1", api_key="secret", protocol="openai"
                )
                with patch.object(main.httpx, "AsyncClient", return_value=client):
                    result = await main.test_provider_connection(payload)
                self.assertFalse(result["ok"])
                self.assertFalse(result.get("catalog_fallback", False))
                self.assertFalse(result.get("connection_verified", False))
                self.assertEqual(result["status"], status_code)

            with self.subTest(entry="fetch", status_code=status_code):
                client = FakeClient([FakeResponse(status_code, text="<html>login</html>")])
                with patch.object(main.httpx, "AsyncClient", return_value=client):
                    with self.assertRaises(main.HTTPException) as raised:
                        await main.fetch_models_from_upstream(
                            "https://api.aig-ai.com/v1", "secret", "openai"
                        )
                self.assertEqual(raised.exception.status_code, status_code)

    async def test_successful_model_lists_merge_catalog_in_both_entries(self):
        model_list = {"data": [{"id": "upstream-chat"}]}
        fetch_client = FakeClient([FakeResponse(200, model_list, '{"data":[{"id":"upstream-chat"}]}')])
        with patch.object(main.httpx, "AsyncClient", return_value=fetch_client):
            fetched = await main.fetch_models_from_upstream(
                "https://api.aig-ai.com/v1", "secret", "openai"
            )
        self.assertIn("gemini-omni-flash-preview", fetched["chat_models"])
        self.assertIn("gemini-omni-flash-preview", fetched["video_models"])
        self.assertFalse(fetched["catalog_fallback"])
        self.assertTrue(fetched["connection_verified"])

        connection_client = FakeClient([FakeResponse(200, model_list, '{"data":[{"id":"upstream-chat"}]}')])
        payload = main.TestConnectionPayload(
            base_url="https://api.aig-ai.com/v1", api_key="secret", protocol="openai"
        )
        with patch.object(main.httpx, "AsyncClient", return_value=connection_client):
            connected = await main.test_provider_connection(payload)
        self.assertIn("gemini-omni-flash-preview", connected["chat_models"])
        self.assertIn("gemini-omni-flash-preview", connected["video_models"])
        self.assertFalse(connected["catalog_fallback"])
        self.assertTrue(connected["connection_verified"])

    async def test_unknown_and_lookalike_hosts_fail_in_both_entries(self):
        for base_url in ("https://api.example.test/v1", "https://aig-ai.com.evil.test/v1"):
            with self.subTest(entry="connection", base_url=base_url):
                client = FakeClient([
                    FakeResponse(404, {"error": "not found"}, '{"error":"not found"}'),
                    FakeResponse(404, {"error": "not found"}, '{"error":"not found"}'),
                    FakeResponse(404, {"error": "not found"}, '{"error":"not found"}'),
                ])
                payload = main.TestConnectionPayload(base_url=base_url, api_key="secret", protocol="openai")
                with patch.object(main.httpx, "AsyncClient", return_value=client):
                    result = await main.test_provider_connection(payload)
                self.assertFalse(result["ok"])
                self.assertFalse(result.get("catalog_fallback", False))

            with self.subTest(entry="fetch", base_url=base_url):
                client = FakeClient([
                    FakeResponse(404, {"error": "not found"}, '{"error":"not found"}'),
                    FakeResponse(404, {"error": "not found"}, '{"error":"not found"}'),
                    FakeResponse(404, {"error": "not found"}, '{"error":"not found"}'),
                ])
                with patch.object(main.httpx, "AsyncClient", return_value=client):
                    with self.assertRaises(main.HTTPException) as raised:
                        await main.fetch_models_from_upstream(base_url, "secret", "openai")
                self.assertEqual(raised.exception.status_code, 404)

    async def test_fetch_models_401_never_uses_catalog_fallback(self):
        client = FakeClient([
            FakeResponse(401, {"error": "unauthorized"}, '{"error":"unauthorized"}'),
        ])
        with patch.object(main.httpx, "AsyncClient", return_value=client):
            with self.assertRaises(main.HTTPException) as raised:
                await main.fetch_models_from_upstream(
                    "https://api.aig-ai.com/v1", "secret", "openai"
                )
        self.assertEqual(raised.exception.status_code, 401)

    async def test_legacy_ark_protocol_uses_openai_models_route_without_ark_probe(self):
        client = FakeClient([
            FakeResponse(200, {"data": [{"id": "upstream-chat"}]}, '{"data":[{"id":"upstream-chat"}]}'),
        ])

        with patch.object(main.httpx, "AsyncClient", return_value=client):
            result = await main.fetch_models_from_upstream(
                "https://api.aig-ai.com/v1", "redacted-key", "volcengine"
            )

        self.assertTrue(result["connection_verified"])
        self.assertEqual([(method, url) for method, url, _ in client.calls], [
            ("GET", "https://api.aig-ai.com/v1/models"),
        ])

    async def test_rate_limit_is_not_reclassified_as_successful_ark_detection(self):
        client = FakeClient([
            FakeResponse(429, {"error": "rate limited"}, '{"error":"rate limited"}'),
        ])

        with patch.object(main.httpx, "AsyncClient", return_value=client):
            with self.assertRaises(main.HTTPException) as raised:
                await main.fetch_models_from_upstream(
                    "https://api.aig-ai.com/v1", "redacted-key", "volcengine"
                )

        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(len(client.calls), 1)
