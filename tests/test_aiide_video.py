import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

import aiide_video as aiide
import main


PROVIDER = {
    "id": "aiide-test", "name": "Aiide", "protocol": "openai",
    "base_url": "https://www.aiide.com/v1",
    "video_base_url": "https://www.aiide.com/v1/video/generations",
}
MODEL = "doubao-seedance-2-0-260128"
SUBMIT = "https://www.aiide.com/v1/video/generations"
TEST_KEY = "test-only-not-a-real-key"
VIDEO = "https://media.example.com/result.mp4"


def payload(**kwargs):
    return main.CanvasVideoRequest(**{
        "provider_id": "aiide-test", "model": MODEL, "prompt": "测试视频", **kwargs,
    })


def success():
    return {"code": 0, "data": {"task_id": "task-123", "status": "SUCCESS", "data": {
        "id": "native-456", "status": "succeeded", "content": {"video_url": VIDEO},
    }}}


class AiideContractTests(unittest.TestCase):
    def test_exact_endpoint_and_base_url_forms(self):
        for value in (SUBMIT, SUBMIT + "/", "https://www.aiide.com/v1", "https://www.aiide.com"):
            with self.subTest(value=value):
                self.assertEqual(aiide.endpoints({**PROVIDER, "video_base_url": value}, MODEL), (SUBMIT, SUBMIT))
        self.assertEqual(aiide.endpoints({**PROVIDER, "video_base_url": ""}, MODEL)[0], SUBMIT)

    def test_endpoint_does_not_guess_or_redirect_to_unrelated_hosts(self):
        for value in ("https://www.aiide.com/v1/videos/generations", "https://www.aiide.com/en/v2/videos/generations",
                      "https://www.aiide.com.evil.test/v1", "https://evil.test/v1", "http://www.aiide.com/v1",
                      "https://key@www.aiide.com/v1", "https://www.aiide.com/v1?key=secret", "https://www.aiide.com:bad/v1"):
            with self.subTest(value=value), self.assertRaises(HTTPException):
                aiide.endpoints({**PROVIDER, "video_base_url": value}, MODEL)

    def test_host_matching_is_exact_and_other_providers_unaffected(self):
        self.assertTrue(aiide.is_provider(PROVIDER))
        for host in ("openrouter.ai", "api.ominilink.ai", "www.aiide.com.evil.test", "vod.tencentcloudapi.com"):
            self.assertFalse(aiide.is_provider({"base_url": f"https://{host}/v1"}))

    def test_seedance_preserves_all_multimodal_references_in_metadata(self):
        request = payload(images=[{"url": "https://media.example.com/1.png"}, {"url": "https://media.example.com/2.png"}],
                          videos=["https://media.example.com/ref.mp4"], audios=["https://media.example.com/ref.mp3"],
                          multimodal=True, duration=12, aspect_ratio="9:16", resolution="1080p", generate_audio=True,
                          watermark=True, seed=0, camerafixed=True, return_last_frame=True)
        body = aiide.request_payload(request, lambda ref: ref["url"])
        self.assertEqual(set(body), {"model", "prompt", "metadata"})
        metadata = body["metadata"]
        self.assertEqual([r["role"] for r in metadata["content"]],
                         ["reference_image", "reference_image", "reference_video", "reference_audio"])
        self.assertEqual(metadata["content"][2]["video_url"]["url"], request.videos[0])
        self.assertEqual(metadata["seed"], 0)
        self.assertEqual(metadata["ratio"], "9:16")
        self.assertEqual(metadata["duration"], 12)
        self.assertEqual(metadata["resolution"], "1080p")
        self.assertTrue(metadata["generate_audio"])

    def test_single_image_and_explicit_first_last_frame_roles(self):
        body = aiide.request_payload(payload(images=[{"url": "asset://mat_test"}]), None)
        self.assertEqual(body["metadata"]["content"][0]["role"], "first_frame")
        body = aiide.request_payload(payload(images=[{"url": "mat_first", "role": "first_frame"},
                                                    {"url": "mat_last", "role": "last_frame"}]), None)
        self.assertEqual([ref["role"] for ref in body["metadata"]["content"]], ["first_frame", "last_frame"])
        self.assertEqual(body["metadata"]["content"][0]["image_url"]["url"], "asset://mat_first")

    def test_no_silent_truncation_of_prompt_or_references(self):
        request = payload(prompt="字" * 8000, images=[{"url": f"asset://mat_{i}"} for i in range(8)])
        body = aiide.request_payload(request, None)
        self.assertEqual(body["prompt"], request.prompt)
        self.assertEqual(len(body["metadata"]["content"]), 8)

    def test_local_images_require_public_links_not_base64(self):
        resolver = lambda ref: "https://media.example.com/image.png"
        body = aiide.request_payload(payload(images=[{"url": "/assets/input/image.png"}]), resolver)
        self.assertEqual(body["metadata"]["content"][0]["image_url"]["url"], "https://media.example.com/image.png")
        with self.assertRaises(HTTPException) as caught:
            aiide.request_payload(payload(images=[{"url": "/assets/input/image.png"}]), lambda ref: "data:image/png;base64,aW1hZ2U=")
        self.assertIn("Base64", caught.exception.detail)
        for value in ("/assets/input/ref.mp4", "/output/ref.mp4", "http://127.0.0.1/ref.mp4", "http://192.168.2.2/ref.mp4"):
            with self.subTest(value=value), self.assertRaises(HTTPException):
                aiide.request_payload(payload(videos=[value]), None)

    def test_inline_media_never_sent_as_asset_urls(self):
        for kind, field in (("image", "images"), ("video", "videos"), ("audio", "audios")):
            value = f"data:{kind}/test;base64,YQ=="
            refs = [{"url": value}] if kind == "image" else [value]
            with self.subTest(kind=kind), self.assertRaises(HTTPException):
                aiide.request_payload(payload(**{field: refs}), None)

    def test_serialized_upstream_error_is_readable(self):
        response = httpx.Response(400, json={"error": {"message": json.dumps({
            "code": "asset_ref_invalid", "message": "素材 URL 必须是公网可访问地址", "data": None,
        })}})
        with self.assertRaises(HTTPException) as caught:
            aiide.response_json(response)
        self.assertIn("素材 URL 必须是公网可访问地址", caught.exception.detail)
        self.assertNotIn('"code"', caught.exception.detail)

    def test_incompatible_roles_and_undocumented_controls_fail_before_submission(self):
        for fields in ({"images": [{"url": "asset://mat_last", "role": "last_frame"}]},
                       {"images": [{"url": "asset://mat_first", "role": "first_frame"}], "videos": ["asset://mat_video"]},
                       {"enhance_prompt": True}, {"enable_upsample": True}):
            with self.subTest(fields=fields), self.assertRaises(HTTPException):
                aiide.request_payload(payload(**fields), None)

    def test_veo_and_sora_use_documented_formats_not_seedance_endpoint(self):
        provider = {**PROVIDER, "video_base_url": ""}
        self.assertEqual(aiide.endpoints(provider, "gemini-veo-3.1-generate-preview-8s")[0], "https://www.aiide.com/v1/videos")
        body = aiide.request_payload(payload(model="gemini-veo-3.1-generate-preview-8s", duration=8, aspect_ratio="9:16",
                                            images=[{"url": "https://media.example.com/image.png"}]), None)
        self.assertEqual(body["size"], "720x1280")
        self.assertEqual(len(body["images"]), 1)
        body = aiide.request_payload(payload(model="sora-v3-fast", duration=8, videos=["https://media.example.com/ref.mp4"]), None)
        self.assertEqual(body["seconds"], "8")
        self.assertEqual(body["reference_videos"], ["https://media.example.com/ref.mp4"])

    def test_nested_output_is_found_without_mistaking_input_for_output(self):
        self.assertEqual(aiide.task_result(success()), ("task-123", [VIDEO]))
        self.assertEqual(aiide.task_result({"task_id": "pending", "status": "queued", "input": {"video_url": VIDEO}}), ("pending", []))

    def test_nested_failure_overrides_outer_success_and_result_url(self):
        data = success()
        data["data"]["data"].update(status="failed", error={"message": "sensitive content"})
        with self.assertRaises(HTTPException) as caught:
            aiide.task_result(data)
        self.assertIn("sensitive content", caught.exception.detail)

    def test_failure_and_empty_completed_result_are_not_infinite_pending(self):
        for data in ({"code": 400, "message": "bad request"}, {"task_id": "empty", "status": "SUCCESS"}):
            with self.subTest(data=data), self.assertRaises(HTTPException):
                aiide.task_result(data)

    def test_non_json_and_redirects_are_actionable_and_hide_secrets(self):
        for status in (301, 302, 307, 308):
            response = httpx.Response(status, text="<html>secret</html>", headers={"Location": "https://evil.test/?token=secret"})
            with self.subTest(status=status), self.assertRaises(HTTPException) as caught:
                aiide.response_json(response)
            self.assertIn(str(status), caught.exception.detail)
            self.assertNotIn("secret", caught.exception.detail)
        with self.assertRaises(HTTPException) as caught:
            aiide.response_json(httpx.Response(401, json={"error": {"message": "bad key " + TEST_KEY}}), (TEST_KEY,))
        self.assertNotIn(TEST_KEY, caught.exception.detail)
        with self.assertRaises(HTTPException):
            aiide.response_json(httpx.Response(200, text="<html>not api</html>"))

    def test_task_id_is_escaped(self):
        self.assertEqual(aiide.task_url(SUBMIT, "a/b?token=x"), SUBMIT + "/a%2Fb%3Ftoken%3Dx")


class AiideGenerationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.calls = []
        self.key_patch = patch.object(main, "provider_env_key_value", return_value=TEST_KEY)
        self.key_patch.start()
        self.addCleanup(self.key_patch.stop)
        self.download = AsyncMock(return_value="/assets/output/aiide_video.mp4")
        self.download_patch = patch.object(main, "save_ominilink_video_to_output", self.download)
        self.download_patch.start()
        self.addCleanup(self.download_patch.stop)

    async def run_responses(self, responses, request=None):
        responses = iter(responses)

        def handle(req):
            self.calls.append(req)
            response = next(responses)
            if isinstance(response, Exception):
                raise response
            return response

        # Even a caller-owned client with follow_redirects=True must not follow POST/GET redirects.
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle), follow_redirects=True) as client:
            with patch.object(main.asyncio, "sleep", new=AsyncMock()):
                return await main.generate_aiide_video(request or payload(), PROVIDER, client)

    async def test_submit_poll_download_entire_contract(self):
        result = await self.run_responses([
            httpx.Response(200, json={"task_id": "task-123", "status": "queued"}),
            httpx.Response(200, json={"data": {"task_id": "task-123", "status": "IN_PROGRESS"}}),
            httpx.Response(200, json=success()),
        ], payload(images=[{"url": "asset://mat_image"}], multimodal=True))
        self.assertEqual([(r.method, str(r.url)) for r in self.calls],
                         [("POST", SUBMIT), ("GET", SUBMIT + "/task-123"), ("GET", SUBMIT + "/task-123")])
        self.assertEqual(json.loads(self.calls[0].content)["metadata"]["content"][0]["role"], "reference_image")
        self.assertEqual(self.calls[0].headers["Authorization"], "Bearer " + TEST_KEY)
        self.download.assert_awaited_once_with(VIDEO, prefix="aiide_")
        self.assertEqual(result["videos"], ["/assets/output/aiide_video.mp4"])
        self.assertEqual(result["task_id"], "task-123")
        self.assertNotIn(TEST_KEY, json.dumps(result))

    async def test_redirect_never_follows_or_submits_fallback_request(self):
        for status in (301, 302, 307, 308):
            self.calls.clear()
            with self.subTest(status=status), self.assertRaises(HTTPException):
                await self.run_responses([httpx.Response(status, headers={"Location": "https://evil.test/video"})])
            self.assertEqual(len(self.calls), 1)
        self.download.assert_not_awaited()

    async def test_query_redirect_stops_and_preserves_original_task_id(self):
        with self.assertRaises(HTTPException) as caught:
            await self.run_responses([httpx.Response(200, json={"task_id": "task-123"}),
                                      httpx.Response(301, headers={"Location": "/en/v1/video/generations/task-123"})])
        self.assertEqual(caught.exception.upstream_task_id, "task-123")
        self.assertEqual([r.method for r in self.calls], ["POST", "GET"])

    async def test_query_transient_failure_retries_get_only(self):
        result = await self.run_responses([httpx.Response(200, json={"task_id": "task-123"}),
                                           httpx.Response(503), httpx.Response(429),
                                           httpx.ReadTimeout("temporary"), httpx.Response(200, json=success())])
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual([r.method for r in self.calls], ["POST", "GET", "GET", "GET", "GET"])

    async def test_submit_timeout_is_not_retried(self):
        with self.assertRaises(HTTPException) as caught:
            await self.run_responses([httpx.ReadTimeout("connection interrupted " + TEST_KEY)])
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn(TEST_KEY, caught.exception.detail)
        self.assertIn("重复扣费", caught.exception.detail)

    async def test_timeout_preserves_task_id(self):
        with patch.object(main, "VIDEO_POLL_TIMEOUT", 0), self.assertRaises(HTTPException) as caught:
            await self.run_responses([httpx.Response(200, json={"task_id": "task-123"})])
        self.assertEqual(caught.exception.status_code, 504)
        self.assertEqual(caught.exception.upstream_task_id, "task-123")
        self.assertEqual(len(self.calls), 1)

    async def test_nested_failure_does_not_download(self):
        with self.assertRaises(HTTPException) as caught:
            await self.run_responses([httpx.Response(200, json={"task_id": "task-123"}),
                                      httpx.Response(200, json={"data": {"status": "FAILURE", "fail_reason": "moderation failed"}})])
        self.assertEqual(caught.exception.upstream_task_id, "task-123")
        self.assertIn("moderation failed", caught.exception.detail)
        self.download.assert_not_awaited()

    async def test_result_save_failure_preserves_task_without_returning_remote_as_success(self):
        self.download.side_effect = HTTPException(502, "OminiLink 下载失败")
        with self.assertRaises(HTTPException) as caught:
            await self.run_responses([httpx.Response(200, json=success())])
        self.assertIn("视频已生成", caught.exception.detail)
        self.assertEqual(caught.exception.upstream_task_id, "task-123")

    async def test_canvas_dispatches_aiide_before_generic_video_handler(self):
        with patch.object(main, "get_api_provider", return_value=PROVIDER), \
                patch.object(main, "generate_aiide_video", new=AsyncMock(return_value={"videos": ["local.mp4"]})) as generate:
            request = payload()
            self.assertEqual(await main.canvas_video(request), {"videos": ["local.mp4"]})
            generate.assert_awaited_once_with(request, PROVIDER)

    async def test_unprepared_local_reference_does_not_upload_or_submit(self):
        with patch.object(main, "local_media_path_for_cloud_upload", return_value="local.png"), \
                patch.object(main, "local_asset_public_url", return_value=""), \
                patch.object(main, "upload_local_video_to_cloud", new=AsyncMock()) as upload, \
                self.assertRaises(HTTPException):
            await self.run_responses([], payload(images=[{"url": "/assets/input/image.png"}]))
        upload.assert_not_awaited()
        self.assertEqual(self.calls, [])

    async def test_own_public_storage_can_be_used_without_third_party_upload(self):
        with patch.object(main, "local_media_path_for_cloud_upload", return_value="local.png"), \
                patch.object(main, "local_asset_public_url", return_value="https://company.example.com/image.png"), \
                patch.object(main, "upload_local_video_to_cloud", new=AsyncMock()) as upload:
            await self.run_responses([httpx.Response(200, json=success())], payload(images=[{"url": "/assets/input/image.png"}]))
        upload.assert_not_awaited()
        reference = json.loads(self.calls[0].content)["metadata"]["content"][0]
        self.assertEqual(reference["image_url"]["url"], "https://company.example.com/image.png")


class AiideReferencePlanTests(unittest.TestCase):
    def plan(self, request, public=""):
        with patch.object(main, "get_api_provider", return_value=PROVIDER), \
                patch.object(main, "ensure_same_origin_request") as origin, \
                patch.object(main, "local_media_path_for_cloud_upload", return_value="local.png"), \
                patch.object(main, "local_asset_public_url", return_value=public), \
                patch.object(main, "upload_local_video_to_cloud", new=AsyncMock()) as upload:
            result = main.prepare_aiide_video_references(request, None)
        origin.assert_called_once_with(None)
        upload.assert_not_awaited()
        return result

    def test_plan_preserves_every_reference_and_deduplicates_uploads(self):
        request = payload(images=[{"url": "/assets/input/first.png", "role": "reference_image"},
                                  {"url": "/assets/input/first.png", "role": "reference_image"}],
                          videos=["/assets/input/ref.mp4"], audios=["/assets/input/ref.mp3"], multimodal=True)
        result = self.plan(request)
        self.assertEqual(result["upload_required"], [{"url": "/assets/input/first.png", "kind": "image"},
                                                    {"url": "/assets/input/ref.mp4", "kind": "video"},
                                                    {"url": "/assets/input/ref.mp3", "kind": "audio"}])
        self.assertEqual(len(result["images"]), 2)
        self.assertEqual(result["images"][0]["role"], "reference_image")
        self.assertEqual(result["videos"], request.videos)
        self.assertNotIn("upload-pending.invalid", json.dumps(result))

    def test_plan_uses_configured_public_origin_and_asset_ids(self):
        result = self.plan(payload(images=[{"url": "/assets/input/one.png"}, {"url": "mat_ready"}]),
                           public="https://company.example.com/one.png")
        self.assertEqual(result["upload_required"], [])
        self.assertEqual([ref["url"] for ref in result["images"]], ["https://company.example.com/one.png", "asset://mat_ready"])

    def test_plan_rejects_invalid_config_before_asking_for_upload(self):
        with self.assertRaises(HTTPException):
            self.plan(payload(images=[{"url": "/assets/input/one.png"}], enhance_prompt=True))
        with self.assertRaises(HTTPException):
            self.plan(payload(images=[{"url": "/assets/input/one.png"}]), public="http://127.0.0.1/one.png")

    def test_readonly_plan_does_not_allow_other_providers(self):
        with patch.object(main, "get_api_provider", return_value={"id": "other", "base_url": "https://openrouter.ai/api/v1"}), \
                patch.object(main, "ensure_same_origin_request"), self.assertRaises(HTTPException):
            main.prepare_aiide_video_references(payload(), None)


if __name__ == "__main__":
    unittest.main()
