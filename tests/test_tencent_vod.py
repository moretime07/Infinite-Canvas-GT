import asyncio
import base64
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

import main
import tencent_vod as vod


CONFIG = {"secret_id": "test-id", "secret_key": "test-secret"}


class TencentVodTests(unittest.TestCase):
    def test_provider_id_does_not_force_tokenhub(self):
        provider = main.normalize_provider({"id": "custom-api-3", "base_url": vod.DEFAULT_BASE, "protocol": "wand"})
        self.assertEqual(provider["protocol"], "tencent-vod")
        self.assertEqual(provider["base_url"], vod.DEFAULT_BASE)
        self.assertFalse(main.is_wand_provider(provider))
        self.assertEqual(main.protocol_from_payload(main.TestConnectionPayload(provider_id="custom-api-3", base_url=vod.DEFAULT_BASE)), "tencent-vod")
        unrelated = main.normalize_provider({"id": "custom-api-3", "base_url": "https://example.com", "protocol": "openai"})
        self.assertEqual(unrelated["protocol"], "openai")

    def test_tokenhub_still_uses_bearer_adapter(self):
        provider = main.normalize_provider({"id": "tokenhub", "base_url": main.WAND_INTL_BASE, "protocol": "wand"})
        self.assertTrue(main.is_wand_provider(provider))
        self.assertFalse(vod.is_provider(provider))

    def test_vod_does_not_allow_openai_model_override(self):
        provider = {"id": "test-vod", "protocol": "tencent-vod", "model_protocols": {vod.MODEL_ID: "openai"}}
        self.assertEqual(main.effective_protocol(provider, vod.MODEL_ID), "tencent-vod")

    def test_vod_image_controls_advertise_supported_reference_count(self):
        fields = main.build_image_param_fields("api", {"protocol": "tencent-vod"}, vod.MODEL_ID)
        self.assertEqual(next(field for field in fields if field["key"] == "reference_images")["max"], 20)
        self.assertNotIn("quality", [field["key"] for field in fields])

    def test_credentials_are_not_persisted_in_provider_json_or_public_response(self):
        provider = main.normalize_provider({"id": "test-vod", "protocol": "tencent-vod", "base_url": vod.DEFAULT_BASE,
                                            "tencent_secret_id": "private-id", "tencent_secret_key": "private-secret",
                                            "tencent_token": "private-token", "tencent_sub_app_id": 123})
        with patch.object(main, "tencent_vod_config", return_value={**CONFIG, "token": "private-token"}):
            public = main.public_provider(provider)
        self.assertTrue(public["has_key"])
        self.assertEqual(public["tencent_sub_app_id"], 123)
        for key in ("tencent_secret_id", "tencent_secret_key", "tencent_token"):
            self.assertNotIn(key, provider)
            self.assertNotIn(key, public)

    def test_signed_payload_uses_tc3_not_bearer(self):
        url, headers, body = vod.signed_request("DescribeSubAppIds", {"Limit": 200}, **CONFIG, timestamp=1551113065)
        self.assertEqual(url, vod.DEFAULT_BASE)
        self.assertEqual(json.loads(body), {"Limit": 200})
        self.assertEqual(headers["X-TC-Version"], "2018-07-17")
        self.assertEqual(headers["X-TC-Action"], "DescribeSubAppIds")
        self.assertTrue(headers["Authorization"].startswith("TC3-HMAC-SHA256 Credential=test-id/2019-02-25/vod/tc3_request"))
        self.assertNotIn("test-secret", headers["Authorization"])

    def test_signing_rejects_non_tencent_host(self):
        with self.assertRaises(HTTPException):
            vod.signed_request("DescribeSubAppIds", {}, **CONFIG, base_url="https://example.com")

    def test_twenty_references_use_base64_without_data_prefix(self):
        ref = {"url": "data:image/png;base64," + base64.b64encode(b"image").decode()}
        body = vod.image_request("edit", "2048x1152", vod.MODEL_ID, [ref] * 20, 123, lambda ref: ref["url"])
        self.assertEqual(len(body["FileInfos"]), 20)
        self.assertEqual(body["FileInfos"][0], {"Type": "Base64", "Base64": "aW1hZ2U="})
        self.assertEqual(body["ModelName"], "wand-vega-image")
        self.assertEqual(body["ModelVersion"], "1.0-lite")
        self.assertEqual(body["OutputConfig"], {"StorageMode": "Temporary"})
        self.assertEqual(json.loads(json.loads(body["ExtInfo"])["AdditionalParameters"]), {"size": "2048x1152"})

    def test_excess_references_are_not_silently_discarded(self):
        with self.assertRaises(HTTPException) as error:
            vod.image_request("edit", "2K", vod.MODEL_ID, [{"url": "https://example.com/a.png"}] * 21, 123, lambda ref: ref["url"])
        self.assertEqual(error.exception.status_code, 400)

    def test_resolution_mode_is_exclusive_with_explicit_size(self):
        body = vod.image_request("test", "1K", vod.MODEL_ID, [], 123, lambda ref: ref["url"])
        self.assertEqual(body["OutputConfig"], {"StorageMode": "Temporary", "Resolution": "1K", "AspectRatio": "1:1"})
        self.assertNotIn("ExtInfo", body)

    def test_local_reference_is_resolved(self):
        body = vod.image_request("test", "1K", vod.MODEL_ID, [{"url": "/assets/input/test.png"}], 123,
                                 lambda ref: "data:image/png;base64,aW1hZ2U=")
        self.assertEqual(body["FileInfos"], [{"Type": "Base64", "Base64": "aW1hZ2U="}])

    def test_application_id_is_required_for_generation(self):
        with self.assertRaises(HTTPException):
            vod.image_request("test", "1K", vod.MODEL_ID, [], 0, lambda ref: ref["url"])

    def test_discovery_selects_only_app_without_submitting_generation(self):
        responses = [{"SubAppIdInfoSet": [{"SubAppId": 123, "Status": "On", "SubAppIdName": "Test"}], "TotalCount": 1}, {"QuotaSet": []}]
        calls = []
        async def remote(client, action, payload, config):
            calls.append((action, payload))
            return responses.pop(0)
        with patch.object(vod, "call", remote):
            result = asyncio.run(vod.discover(CONFIG))
        self.assertTrue(result["connection_verified"])
        self.assertFalse(result["generation_verified"])
        self.assertEqual(result["model_source"], "vod_documentation")
        self.assertEqual(result["tencent_sub_app_id"], 123)
        self.assertEqual(calls[1], ("DescribeAigcQuotas", {"SubAppId": 123, "QuotaType": "Image", "Limit": 100}))
        self.assertEqual(len(calls), 2)

    def test_multiple_apps_require_selection(self):
        with patch.object(vod, "list_apps", AsyncMock(return_value=[{"id": 1, "name": "A"}, {"id": 2, "name": "B"}])):
            result = asyncio.run(vod.discover(CONFIG))
        self.assertFalse(result["ok"])
        self.assertTrue(result["needs_sub_app"])
        self.assertEqual(result["all"], [])

    def test_missing_local_reference_is_actionable(self):
        def missing(ref):
            raise FileNotFoundError()
        with self.assertRaises(HTTPException) as error:
            vod.image_request("test", "1K", vod.MODEL_ID, [{"url": "/assets/input/missing.png"}], 123, missing)
        self.assertEqual(error.exception.status_code, 400)

    def test_task_polls_same_application_and_returns_all_images(self):
        actions = []
        def handler(request):
            action = request.headers["x-tc-action"]
            payload = json.loads(request.content)
            actions.append(action)
            self.assertEqual(payload["SubAppId"], 123)
            if action == "CreateAigcImageTask":
                return httpx.Response(200, json={"Response": {"TaskId": "task-1"}})
            self.assertEqual(payload["TaskId"], "task-1")
            if len(actions) == 2:
                return httpx.Response(200, json={"Response": {"Status": "PROCESSING"}})
            return httpx.Response(200, json={"Response": {"Status": "FINISH", "AigcImageTask": {
                "ErrCodeExt": "", "Output": {"FileInfos": [{"FileUrl": "https://example.com/1.png"}, {"FileUrl": "https://example.com/2.png"}]}}}})
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await vod.generate(CONFIG, {"SubAppId": 123}, client=client, interval=0)
        image, result = asyncio.run(run())
        self.assertEqual(actions, ["CreateAigcImageTask", "DescribeTaskDetail", "DescribeTaskDetail"])
        self.assertEqual(image, {"type": "url", "value": "https://example.com/1.png"})
        self.assertEqual(len(result["data"]), 2)

    def test_finished_task_with_error_is_not_success(self):
        def handler(request):
            result = {"TaskId": "task-2"} if request.headers["x-tc-action"] == "CreateAigcImageTask" else {
                "Status": "FINISH", "AigcImageTask": {"ErrCodeExt": "ContentReviewFailed", "Message": "review rejected"}}
            return httpx.Response(200, json={"Response": result})
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await vod.generate(CONFIG, {"SubAppId": 123}, client=client, interval=0)
        with self.assertRaises(HTTPException) as error:
            asyncio.run(run())
        self.assertIn("ContentReviewFailed", error.exception.detail)

    def test_auth_error_is_not_connection_success(self):
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={
                "Response": {"Error": {"Code": "AuthFailure.SignatureFailure", "Message": "test-secret"}, "RequestId": "r1"}}))) as client:
                return await vod.call(client, "DescribeSubAppIds", {}, CONFIG)
        with self.assertRaises(HTTPException) as error:
            asyncio.run(run())
        self.assertEqual(error.exception.status_code, 403)
        self.assertNotIn("test-secret", error.exception.detail)

    def test_online_and_canvas_dispatch_to_vod(self):
        provider = {"id": "test-vod", "protocol": "tencent-vod", "base_url": vod.DEFAULT_BASE, "tencent_sub_app_id": 123}
        output = ({"type": "url", "value": "https://example.com/result.png"}, {"task_id": "task-1"})
        with patch.object(main, "get_api_provider", return_value=provider), \
                patch.object(main, "tencent_vod_config", return_value=CONFIG), \
                patch.object(vod, "generate", AsyncMock(return_value=output)):
            result = asyncio.run(main.generate_ai_image("test", "1K", "auto", vod.MODEL_ID, [], "test-vod"))
        self.assertEqual(result, output)

    def test_all_settings_endpoints_use_vod_discovery(self):
        payload = main.TestConnectionPayload(provider_id="custom-api-3", base_url=vod.DEFAULT_BASE, protocol="tencent-vod")
        expected = {"ok": True, "image_models": [vod.MODEL_ID]}
        with patch.object(main, "tencent_vod_models", AsyncMock(return_value=expected)):
            for route in (main.test_provider_connection, main.probe_async_endpoint, main.fetch_upstream_models_from_payload):
                self.assertEqual(asyncio.run(route(payload)), expected)


if __name__ == "__main__":
    unittest.main()
