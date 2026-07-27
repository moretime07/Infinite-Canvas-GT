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
