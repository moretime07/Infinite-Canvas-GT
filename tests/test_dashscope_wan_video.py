import contextlib
import io
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx

import main


BASE_URL = "https://ws-test.cn-hongkong.maas.aliyuncs.com/compatible-mode/v1"
SUBMIT_URL = "https://ws-test.cn-hongkong.maas.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis"
TASK_URL = "https://ws-test.cn-hongkong.maas.aliyuncs.com/api/v1/tasks/task%2Fone"
API_KEY = "sk-private-wan3-test"
RESULT_URL = "https://dashscope-result.example.test/private/video.mp4?signature=private"


class DashScopeWan3Tests(unittest.IsolatedAsyncioTestCase):
    def provider(self, base_url=BASE_URL):
        return {
            "id": "custom-api-7",
            "name": "阿里云 MaaS",
            "base_url": base_url,
            "protocol": "openai",
        }

    def payload(self, **overrides):
        values = {
            "provider_id": "custom-api-7",
            "model": "wan3.0-video",
            "prompt": "A precise moving landscape",
            "duration": 15,
            "aspect_ratio": "16:9",
            "resolution": "720p",
            "watermark": False,
            "images": [{"url": "/assets/input/first.png", "role": "first_frame"}],
        }
        values.update(overrides)
        return main.CanvasVideoRequest(**values)

    async def run_canvas(self, handler, payload=None, provider=None):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        save = AsyncMock(return_value=["/assets/output/wan3.mp4"])
        with patch.object(main, "get_api_provider", return_value=provider or self.provider()), \
                patch.object(main, "provider_env_key_value", return_value=API_KEY), \
                patch.object(main.httpx, "AsyncClient", return_value=client), \
                patch.object(main, "reference_to_data_url", return_value="data:image/png;base64,FIRST"), \
                patch.object(main, "save_video_outputs", save), \
                patch.object(main.asyncio, "sleep", new=AsyncMock()):
            result = await main.canvas_video(payload or self.payload())
        return result, save

    async def test_submit_and_poll_use_dashscope_native_routes_and_payload(self):
        seen = []

        def handler(request):
            seen.append(request)
            if request.method == "POST":
                self.assertEqual(str(request.url), SUBMIT_URL)
                self.assertEqual(request.headers["Authorization"], f"Bearer {API_KEY}")
                self.assertEqual(request.headers["X-DashScope-Async"], "enable")
                self.assertEqual(json.loads(request.content), {
                    "model": "wan3.0-video",
                    "input": {
                        "prompt": "A precise moving landscape",
                        "media": [{"type": "first_frame", "url": "data:image/png;base64,FIRST"}],
                    },
                    "parameters": {
                        "duration": 15, "ratio": "16:9", "watermark": False,
                        "prompt_extend": False, "audio": False, "resolution": "720P",
                    },
                })
                return httpx.Response(200, json={"output": {"task_id": "task/one", "task_status": "PENDING"}})
            self.assertEqual(str(request.url), TASK_URL)
            self.assertEqual(request.headers["Authorization"], f"Bearer {API_KEY}")
            self.assertNotIn("X-DashScope-Async", request.headers)
            return httpx.Response(200, json={"output": {
                "task_id": "task/one", "task_status": "SUCCEEDED", "video_url": RESULT_URL,
            }})

        result, save = await self.run_canvas(handler)
        self.assertEqual(result, {
            "videos": ["/assets/output/wan3.mp4"], "task_id": "task/one", "model": "wan3.0-video",
            "provider_id": "custom-api-7", "status": "succeeded",
        })
        self.assertEqual([request.method for request in seen], ["POST", "GET"])
        save.assert_awaited_once_with([RESULT_URL])

    async def test_pending_task_then_succeeded(self):
        count = 0

        def handler(request):
            nonlocal count
            if request.method == "POST":
                return httpx.Response(200, json={"output": {"task_id": "task/one", "task_status": "PENDING"}})
            count += 1
            status = "RUNNING" if count == 1 else "SUCCEEDED"
            body = {"output": {"task_id": "task/one", "task_status": status}}
            if status == "SUCCEEDED":
                body["output"]["video_url"] = RESULT_URL
            return httpx.Response(200, json=body)

        result, _save = await self.run_canvas(handler)
        self.assertEqual(result["task_id"], "task/one")
        self.assertEqual(count, 2)

    async def test_failed_and_unknown_task_are_terminal_and_preserve_id(self):
        for status in ("FAILED", "CANCELED", "UNKNOWN"):
            with self.subTest(status=status):
                def handler(request, status=status):
                    if request.method == "POST":
                        return httpx.Response(200, json={"output": {"task_id": "task/one", "task_status": "PENDING"}})
                    return httpx.Response(200, json={"output": {
                        "task_id": "task/one", "task_status": status,
                        "code": "InvalidParameter", "message": f"terminal {status}",
                    }})

                with self.assertRaises(main.HTTPException) as raised:
                    await self.run_canvas(handler)
                self.assertEqual(raised.exception.status_code, 502)
                self.assertEqual(getattr(raised.exception, "upstream_task_id", ""), "task/one")
                self.assertIn(status, raised.exception.detail)

    async def test_content_inspection_failure_is_explained_without_retrying(self):
        seen = []

        def handler(request):
            seen.append(request.method)
            if request.method == "POST":
                return httpx.Response(200, json={"output": {"task_id": "task/one", "task_status": "PENDING"}})
            return httpx.Response(200, json={"output": {
                "task_id": "task/one", "task_status": "FAILED",
                "code": "DataInspectionFailed",
                "message": "Green net check rejected image (output)",
            }})

        with self.assertRaises(main.HTTPException) as raised:
            await self.run_canvas(handler)
        detail = raised.exception.detail
        self.assertEqual(seen, ["POST", "GET"])
        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(getattr(raised.exception, "upstream_task_id", ""), "task/one")
        self.assertIn("DataInspectionFailed", detail)
        self.assertIn("内容安全审核未通过", detail)
        self.assertIn("生成结果被拦截", detail)
        self.assertIn("与 API Key、网络和计费无关", detail)

    def test_content_inspection_input_failure_has_distinct_advice(self):
        detail = main.dashscope_wan3_error_detail("阿里云 Wan 3.0 任务状态为 FAILED", {"output": {
            "code": "DataInspectionFailed",
            "message": "Green net check rejected image (input)",
        }})
        self.assertIn("输入素材被拦截", detail)
        self.assertNotIn("生成结果被拦截", detail)

    async def test_succeeded_without_video_url_is_an_error(self):
        def handler(request):
            if request.method == "POST":
                return httpx.Response(200, json={"output": {"task_id": "task/one", "task_status": "PENDING"}})
            return httpx.Response(200, json={"output": {"task_id": "task/one", "task_status": "SUCCEEDED"}})

        with self.assertRaises(main.HTTPException) as raised:
            await self.run_canvas(handler)
        self.assertEqual(raised.exception.status_code, 502)
        self.assertIn("video_url", raised.exception.detail)
        self.assertEqual(getattr(raised.exception, "upstream_task_id", ""), "task/one")

    async def test_parameter_validation_happens_before_post(self):
        invalid_cases = [
            {"size": "1920x1080"},
            {"enable_upsample": True},
            {"camerafixed": True},
            {"return_last_frame": True},
            {"trusted_asset": True},
            {"videos": ["https://example.test/a.mp4"]},
            {"audios": ["https://example.test/a.mp3"]},
            {"duration": 31},
            {"duration": 1},
            {"resolution": "4k"},
            {"aspect_ratio": "0:16"},
            {"images": [{"url": "/a.png", "role": "unknown"}]},
            {"images": [
                {"url": "/a.png", "role": "first_frame"},
                {"url": "/b.png", "role": "reference_image"},
            ]},
            {"images": [
                {"url": "/a.png", "role": "first_frame"},
                {"url": "/b.png", "role": "first_frame"},
            ]},
            {"images": [{"url": f"/{index}.png", "role": "reference_image"} for index in range(11)]},
            {"seed": -2},
            {"seed": 2147483648},
        ]
        constructor = AsyncMock()
        for overrides in invalid_cases:
            with self.subTest(overrides=overrides), patch.object(main, "get_api_provider", return_value=self.provider()), \
                    patch.object(main, "provider_env_key_value", return_value=API_KEY), \
                    patch.object(main.httpx, "AsyncClient", constructor), \
                    patch.object(main, "reference_to_data_url", return_value="data:image/png;base64,TEST"):
                with self.assertRaises(main.HTTPException) as raised:
                    await main.canvas_video(self.payload(**overrides))
                self.assertEqual(raised.exception.status_code, 400)
        constructor.assert_not_called()

    async def test_multiple_reference_images_and_seed_are_supported(self):
        seen = {}

        def handler(request):
            if request.method == "POST":
                seen["body"] = json.loads(request.content)
                return httpx.Response(200, json={"output": {"task_id": "task/one", "task_status": "PENDING"}})
            return httpx.Response(200, json={"output": {
                "task_id": "task/one", "task_status": "SUCCEEDED", "video_url": RESULT_URL,
            }})

        images = [{"url": f"/assets/input/{index}.png", "role": "reference_image"} for index in range(10)]
        _result, _save = await self.run_canvas(
            handler,
            payload=self.payload(duration=30, aspect_ratio="adaptive", seed=42, images=images),
        )
        self.assertEqual(seen["body"]["parameters"], {
            "duration": 30, "ratio": "adaptive", "watermark": False,
            "prompt_extend": False, "audio": False, "resolution": "720P", "seed": 42,
        })
        self.assertEqual([item["type"] for item in seen["body"]["input"]["media"]], ["reference_image"] * 10)

    async def test_prompt_extension_and_audio_map_to_official_fields(self):
        seen = {}

        def handler(request):
            if request.method == "POST":
                seen["body"] = json.loads(request.content)
                return httpx.Response(200, json={"output": {"task_id": "task/one", "task_status": "PENDING"}})
            return httpx.Response(200, json={"output": {
                "task_id": "task/one", "task_status": "SUCCEEDED", "video_url": RESULT_URL,
            }})

        await self.run_canvas(
            handler,
            payload=self.payload(enhance_prompt=True, generate_audio=True, multimodal=True),
        )
        self.assertEqual(seen["body"]["parameters"], {
            "duration": 15, "ratio": "16:9", "watermark": False,
            "prompt_extend": True, "audio": True, "resolution": "720P",
        })

    async def test_non_maas_wan_keeps_legacy_new_api_route(self):
        seen = []

        def handler(request):
            seen.append(str(request.url))
            return httpx.Response(200, json={"data": [{"url": RESULT_URL}]})

        provider = self.provider("https://wan.example.test/v1")
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch.object(main, "get_api_provider", return_value=provider), \
                patch.dict(os.environ, {main.provider_key_env(provider["id"]): API_KEY}), \
                patch.object(main.httpx, "AsyncClient", return_value=client), \
                patch.object(main, "reference_to_data_url", return_value="data:image/png;base64,FIRST"), \
                patch.object(main, "save_video_outputs", new=AsyncMock(return_value=["/assets/output/legacy.mp4"])):
            result = await main.canvas_video(self.payload())
        self.assertEqual(result["videos"], ["/assets/output/legacy.mp4"])
        self.assertEqual(len(seen), 1)
        self.assertIn("wan.example.test", seen[0])
        self.assertNotIn("maas.aliyuncs.com", seen[0])

    async def test_submit_transport_failure_never_retries_post(self):
        calls = []

        def handler(request):
            calls.append(request.method)
            raise httpx.ConnectError(f"private {API_KEY} {RESULT_URL}", request=request)

        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(main.HTTPException) as raised:
                await self.run_canvas(handler)
        self.assertEqual(calls, ["POST"])
        self.assertEqual(raised.exception.status_code, 502)
        self.assertIn("避免重复提交", raised.exception.detail)
        self.assertNotIn(API_KEY, raised.exception.detail)
        self.assertNotIn(RESULT_URL, raised.exception.detail)

    async def test_poll_transport_and_5xx_are_retried_without_posting_again(self):
        calls = []

        def handler(request):
            calls.append(request.method)
            if request.method == "POST":
                return httpx.Response(200, json={"output": {"task_id": "task/one", "task_status": "PENDING"}})
            get_count = calls.count("GET")
            if get_count == 1:
                raise httpx.ConnectError("temporary", request=request)
            if get_count == 2:
                return httpx.Response(503, json={"message": "temporary"})
            return httpx.Response(200, json={"output": {
                "task_id": "task/one", "task_status": "SUCCEEDED", "video_url": RESULT_URL,
            }})

        with contextlib.redirect_stdout(io.StringIO()):
            result, save = await self.run_canvas(handler)
        self.assertEqual(calls, ["POST", "GET", "GET", "GET"])
        self.assertEqual(result["task_id"], "task/one")
        save.assert_awaited_once_with([RESULT_URL])

    async def test_errors_and_success_do_not_log_credentials_or_signed_url(self):
        output = io.StringIO()

        def handler(request):
            if request.method == "POST":
                return httpx.Response(401, json={
                    "code": "InvalidApiKey",
                    "message": f"Bearer {API_KEY} rejected; see {RESULT_URL}",
                })
            return httpx.Response(500)

        with contextlib.redirect_stdout(output):
            with self.assertRaises(main.HTTPException) as raised:
                await self.run_canvas(handler)
        combined = output.getvalue() + str(raised.exception.detail)
        self.assertNotIn(API_KEY, combined)
        self.assertNotIn(RESULT_URL, combined)
        self.assertIn("已隐藏", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
