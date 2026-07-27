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


class RouteClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.responses[("GET", url)]

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.responses[("POST", url)]


class OminiLinkProviderTests(unittest.TestCase):
    def test_legacy_transport_helpers_defer_to_ominilink_authority(self):
        runninghub = {
            "id": "runninghub",
            "protocol": "runninghub",
            "base_url": "https://api.runninghub.cn",
            "video_base_url": "https://vg-api.ominilink.ai/v1",
        }
        volcengine = {
            "id": "volcengine",
            "protocol": "volcengine",
            "base_url": "https://ark.cn-beijing.volces.com",
            "video_base_url": "https://vg-api.aig-ai.com/v1",
        }

        self.assertFalse(main.is_runninghub_provider(runninghub))
        self.assertFalse(main.is_volcengine_provider(volcengine))

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


class OminiLinkProviderEndpointTests(unittest.IsolatedAsyncioTestCase):
    def test_default_merge_keeps_ominilink_protocol_authoritative(self):
        providers = [
            main.normalize_provider({
                "id": "runninghub",
                "name": "Legacy RunningHub",
                "base_url": "https://api.runninghub.cn",
                "video_base_url": "https://vg-api.ominilink.ai/v1",
                "protocol": "runninghub",
            }),
            main.normalize_provider({
                "id": "volcengine",
                "name": "Legacy Volcengine",
                "base_url": "https://ark.cn-beijing.volces.com",
                "video_base_url": "https://vg-api.aig-ai.com/v1",
                "protocol": "volcengine",
            }),
        ]

        with patch.object(main, "load_static_runninghub_provider", return_value=None):
            merged = main.merge_default_api_providers(providers)

        protocols = {item["id"]: item["protocol"] for item in merged}
        self.assertEqual(protocols["runninghub"], "openai")
        self.assertEqual(protocols["volcengine"], "openai")

    async def test_save_endpoint_persists_and_returns_openai_for_exact_hosts(self):
        cases = (
            ("volcengine", "https://api.aig-ai.com/v1", ""),
            ("volcengine", "https://vg-api.aig-ai.com/v1", ""),
            ("volcengine", "https://example.test/v1", "https://vg-api.aig-ai.com/v1"),
            ("runninghub", "https://api.ominilink.ai/v1", ""),
            ("runninghub", "https://example.test/v1", "https://vg-api.ominilink.ai/v1"),
        )
        for provider_id, base_url, video_base_url in cases:
            with self.subTest(provider_id=provider_id, base_url=base_url, video_base_url=video_base_url):
                payload = [main.ApiProviderPayload(
                    id=provider_id,
                    name=provider_id,
                    base_url=base_url,
                    video_base_url=video_base_url,
                    protocol=provider_id,
                    chat_models=["chat-model"],
                )]
                saved = []
                with patch.object(main, "load_api_providers", return_value=[]), patch.object(
                    main, "load_static_runninghub_provider", return_value=None
                ), patch.object(main, "load_runninghub_workflow_store", return_value={}), patch.object(
                    main, "provider_env_key_value", return_value=""
                ), patch.object(main, "runninghub_wallet_key_value", return_value=""), patch.object(
                    main, "volcengine_access_key_value", return_value=""
                ), patch.object(main, "volcengine_secret_key_value", return_value=""), patch.object(
                    main, "save_api_providers", side_effect=lambda items: saved.extend(dict(item) for item in items)
                ), patch.object(main, "update_env_values"), patch.object(main, "reload_env_globals"):
                    result = await main.save_providers(payload)

                self.assertEqual(saved[0]["protocol"], "openai")
                self.assertEqual(result["providers"][0]["protocol"], "openai")

    async def test_get_fetch_models_legacy_ids_use_ominilink_url_and_standard_key(self):
        cases = (
            {
                "id": "runninghub",
                "name": "Legacy RunningHub",
                "base_url": "https://api.runninghub.cn",
                "video_base_url": "https://vg-api.ominilink.ai/v1",
                "protocol": "runninghub",
                "image_request_mode": "openai",
                "expected_url": "https://api.ominilink.ai/v1",
            },
            {
                "id": "volcengine",
                "name": "Legacy Volcengine",
                "base_url": "https://ark.cn-beijing.volces.com/api/v3",
                "video_base_url": "https://vg-api.aig-ai.com/v1",
                "protocol": "volcengine",
                "image_request_mode": "openai",
                "expected_url": "https://api.aig-ai.com/v1",
            },
        )
        for provider in cases:
            with self.subTest(provider_id=provider["id"]):
                captured = []

                async def fake_fetch(base_url, api_key, protocol, image_request_mode):
                    captured.append((base_url, api_key, protocol, image_request_mode))
                    return {"source": "model-fetch-sentinel"}

                def fake_getenv(name, default=""):
                    if name == main.runninghub_wallet_key_env():
                        return "wallet-key-sentinel"
                    if name == main.provider_key_env(provider["id"]):
                        return "standard-key-sentinel"
                    return default

                with patch.object(main, "get_api_provider_exact", return_value=provider), patch.object(
                    main, "provider_env_key_value", return_value="standard-key-sentinel"
                ), patch.object(main.os, "getenv", side_effect=fake_getenv), patch.object(
                    main, "fetch_models_from_upstream", side_effect=fake_fetch
                ):
                    result = await main.fetch_upstream_models(provider["id"])

                self.assertEqual(result, {"source": "model-fetch-sentinel"})
                self.assertEqual(
                    captured,
                    [
                        (
                            provider["expected_url"],
                            "standard-key-sentinel",
                            "openai",
                            "openai",
                        )
                    ],
                )

    async def test_get_fetch_models_rejects_wallet_only_ominilink_runninghub(self):
        provider = {
            "id": "runninghub",
            "name": "Legacy RunningHub",
            "base_url": "https://api.runninghub.cn",
            "video_base_url": "https://vg-api.ominilink.ai/v1",
            "protocol": "runninghub",
            "image_request_mode": "openai",
        }
        captured = []

        async def fake_fetch(*args):
            captured.append(args)
            return {"source": "unexpected"}

        def fake_getenv(name, default=""):
            if name == main.runninghub_wallet_key_env():
                return "wallet-only-sentinel"
            return default

        with patch.object(main, "get_api_provider_exact", return_value=provider), patch.object(
            main, "provider_env_key_value", return_value=""
        ), patch.object(main.os, "getenv", side_effect=fake_getenv), patch.object(
            main, "fetch_models_from_upstream", side_effect=fake_fetch
        ):
            with self.assertRaises(main.HTTPException) as raised:
                await main.fetch_upstream_models("runninghub")

        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(captured, [])

    async def test_get_fetch_models_true_runninghub_keeps_wallet_transport(self):
        provider = {
            "id": "runninghub",
            "name": "RunningHub",
            "base_url": "https://api.runninghub.cn",
            "video_base_url": "",
            "protocol": "runninghub",
            "image_request_mode": "openai",
        }
        captured = []

        async def fake_fetch(base_url, api_key, protocol, image_request_mode):
            captured.append((base_url, api_key, protocol, image_request_mode))
            return {"source": "runninghub-sentinel"}

        def fake_getenv(name, default=""):
            if name == main.runninghub_wallet_key_env():
                return "wallet-key-sentinel"
            if name == main.provider_key_env("runninghub"):
                return "standard-key-sentinel"
            return default

        with patch.object(main, "get_api_provider_exact", return_value=provider), patch.object(
            main, "provider_env_key_value", return_value="standard-key-sentinel"
        ), patch.object(main.os, "getenv", side_effect=fake_getenv), patch.object(
            main, "fetch_models_from_upstream", side_effect=fake_fetch
        ):
            result = await main.fetch_upstream_models("runninghub")

        self.assertEqual(result, {"source": "runninghub-sentinel"})
        self.assertEqual(
            captured,
            [
                (
                    "https://api.runninghub.cn",
                    "wallet-key-sentinel",
                    "runninghub",
                    "openai",
                )
            ],
        )


class OminiLinkDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    def test_probe_payload_retains_video_base_url(self):
        payload = main.TestConnectionPayload(
            base_url="",
            video_base_url="https://vg-api.aig-ai.com/v1",
            provider_id="runninghub",
            protocol="runninghub",
        )

        self.assertEqual(getattr(payload, "video_base_url", None), "https://vg-api.aig-ai.com/v1")

    def test_route_probe_rate_limit_is_not_success(self):
        self.assertFalse(main.route_probe_succeeded(429))

    async def test_probe_async_exact_host_uses_only_openai_compatible_routes(self):
        base_url = "https://api.aig-ai.com/v1"
        client = RouteClient({
            ("GET", f"{base_url}/models"): FakeResponse(
                404, {"error": "not found"}, '{"error":"not found"}'
            ),
            ("POST", f"{base_url}/chat/completions"): FakeResponse(
                400, {"error": {"message": "messages required"}},
                '{"error":{"message":"messages required"}}',
            ),
            ("GET", f"{base_url}/tasks/healthcheck_probe_do_not_submit"): FakeResponse(
                404, {"error": "not found"}, '{"error":"not found"}'
            ),
        })
        payload = main.TestConnectionPayload(
            base_url=base_url,
            api_key="redacted-key",
            provider_id="volcengine",
            protocol="volcengine",
        )

        with patch.object(main.httpx, "AsyncClient", return_value=client):
            result = await main.probe_async_endpoint(payload)

        self.assertTrue(result["ok"])
        self.assertEqual(result["protocol"], "openai")
        self.assertEqual(
            [(method, url) for method, url, _ in client.calls],
            [
                ("GET", f"{base_url}/models"),
                ("POST", f"{base_url}/chat/completions"),
            ],
        )

    async def test_probe_async_exact_host_returns_redacted_terminal_rate_limit(self):
        base_url = "https://api.ominilink.ai/v1"
        sensitive = "rate limited: token=do-not-echo"
        client = RouteClient({
            ("GET", f"{base_url}/models"): FakeResponse(
                429, {"error": sensitive}, sensitive
            ),
            ("GET", f"{base_url}/tasks/healthcheck_probe_do_not_submit"): FakeResponse(
                404, {"error": "not found"}, '{"error":"not found"}'
            ),
            ("POST", f"{base_url}/chat/completions"): FakeResponse(
                429, {"error": sensitive}, sensitive
            ),
        })
        payload = main.TestConnectionPayload(
            base_url=base_url,
            api_key="redacted-key",
            provider_id="runninghub",
            protocol="runninghub",
        )

        with patch.object(main.httpx, "AsyncClient", return_value=client):
            result = await main.probe_async_endpoint(payload)

        self.assertFalse(result["ok"])
        self.assertEqual(result["protocol"], "openai")
        self.assertEqual(result["status_code"], 429)
        self.assertNotIn("do-not-echo", str(result))
        self.assertEqual(
            [(method, url) for method, url, _ in client.calls],
            [("GET", f"{base_url}/models")],
        )

    async def test_probe_async_legacy_ids_use_only_ominilink_routes(self):
        cases = (
            (
                "runninghub",
                "https://api.aig-ai.com/v1",
                "",
                "https://api.aig-ai.com/v1",
            ),
            (
                "runninghub",
                "",
                "https://vg-api.ominilink.ai/v1",
                "https://api.ominilink.ai/v1",
            ),
            (
                "volcengine",
                "",
                "https://vg-api.aig-ai.com/v1",
                "https://api.aig-ai.com/v1",
            ),
        )
        for provider_id, base_url, video_base_url, probe_base in cases:
            with self.subTest(provider_id=provider_id, base_url=base_url, video_base_url=video_base_url):
                client = RouteClient({
                    ("GET", f"{probe_base}/models"): FakeResponse(
                        404, {"error": "not found"}, '{"error":"not found"}'
                    ),
                    ("POST", f"{probe_base}/chat/completions"): FakeResponse(
                        400,
                        {"error": {"message": "messages required"}},
                        '{"error":{"message":"messages required"}}',
                    ),
                })
                payload = main.TestConnectionPayload(
                    base_url=base_url,
                    video_base_url=video_base_url,
                    api_key="redacted-key",
                    provider_id=provider_id,
                    protocol=provider_id,
                )

                with patch.object(main.httpx, "AsyncClient", return_value=client):
                    try:
                        result = await main.probe_async_endpoint(payload)
                    except main.HTTPException as exc:
                        self.fail(f"video URL authority was not accepted: {exc.detail}")

                self.assertTrue(result["ok"])
                self.assertEqual(result["protocol"], "openai")
                self.assertEqual(
                    [(method, url) for method, url, _ in client.calls],
                    [
                        ("GET", f"{probe_base}/models"),
                        ("POST", f"{probe_base}/chat/completions"),
                    ],
                )

    async def test_fetch_models_payload_uses_video_base_url_chat_host(self):
        probe_base = "https://api.aig-ai.com/v1"
        client = RouteClient({
            ("GET", f"{probe_base}/models"): FakeResponse(
                200,
                {"data": [{"id": "upstream-chat"}]},
                '{"data":[{"id":"upstream-chat"}]}',
            ),
        })
        payload = main.TestConnectionPayload(
            base_url="",
            video_base_url="https://vg-api.aig-ai.com/v1",
            api_key="redacted-key",
            provider_id="runninghub",
            protocol="runninghub",
        )

        with patch.object(main.httpx, "AsyncClient", return_value=client):
            try:
                result = await main.fetch_upstream_models_from_payload(payload)
            except main.HTTPException as exc:
                self.fail(f"video URL authority was not accepted: {exc.detail}")

        self.assertTrue(result.get("connection_verified", False))
        self.assertEqual(
            [(method, url) for method, url, _ in client.calls],
            [("GET", f"{probe_base}/models")],
        )

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


class OminiLinkVideoRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_canvas_video_legacy_ids_route_to_ominilink(self):
        cases = (
            {
                "id": "runninghub",
                "name": "Legacy RunningHub",
                "protocol": "runninghub",
                "base_url": "https://api.aig-ai.com/v1",
                "video_base_url": "https://vg-api.aig-ai.com/v1",
            },
            {
                "id": "runninghub",
                "name": "Legacy RunningHub Video Only",
                "protocol": "runninghub",
                "base_url": "https://api.runninghub.cn",
                "video_base_url": "https://vg-api.ominilink.ai/v1",
            },
            {
                "id": "volcengine",
                "name": "Legacy Volcengine Video Only",
                "protocol": "volcengine",
                "base_url": "https://ark.cn-beijing.volces.com",
                "video_base_url": "https://vg-api.aig-ai.com/v1",
            },
        )
        for provider in cases:
            with self.subTest(provider_id=provider["id"], video_base_url=provider["video_base_url"]):
                payload = main.CanvasVideoRequest(
                    prompt="route me",
                    provider_id=provider["id"],
                    model="gemini-omni-flash-preview",
                )
                ominilink = AsyncMock(return_value={"transport": "ominilink"})
                runninghub = AsyncMock(return_value={"transport": "runninghub"})

                with patch.object(main, "get_api_provider", return_value=provider), patch.object(
                    main, "generate_ominilink_video", new=ominilink
                ), patch.object(main, "generate_runninghub_video", new=runninghub):
                    result = await main.canvas_video(payload)

                self.assertEqual(result, {"transport": "ominilink"})
