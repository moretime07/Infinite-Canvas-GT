import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main


class _RunningHubResponse:
    status_code = 200

    @staticmethod
    def json():
        return {"code": 0, "data": {"taskId": "task-prompt-limit"}}


class _RunningHubClient:
    def __init__(self):
        self.body = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, _url, **kwargs):
        self.body = kwargs["json"]
        return _RunningHubResponse()


class RunningHubPromptLimitTests(unittest.IsolatedAsyncioTestCase):
    async def assert_prompt_is_limited(self, webapp_id):
        client = _RunningHubClient()
        payload = main.RunningHubSubmitRequest(
            webappId=webapp_id,
            nodeInfoList=[
                {"nodeId": "5", "fieldName": "prompt", "fieldValue": "角" * 2895},
            ],
        )

        with (
            patch.object(main, "runninghub_provider", return_value={"base_url": "https://example.invalid"}),
            patch.object(main, "runninghub_api_key", return_value="test-key"),
            patch.object(main, "runninghub_app_headers", return_value={}),
            patch.object(main.httpx, "AsyncClient", return_value=client),
        ):
            result = await main.runninghub_submit(payload)

        self.assertTrue(result["success"])
        submitted_prompt = client.body["nodeInfoList"][0]["fieldValue"]
        self.assertEqual(len(submitted_prompt), 2048)
        self.assertEqual(submitted_prompt, "角" * 2048)

    async def test_original_omni_video_prompt_is_limited_before_upstream_submission(self):
        await self.assert_prompt_is_limited("2058790334674587649")

    async def test_budget_omni_video_prompt_is_limited_before_upstream_submission(self):
        await self.assert_prompt_is_limited("2059985306476179457")

    def test_runninghub_failure_prefers_validation_traceback_over_generic_message(self):
        raw = {
            "code": 805,
            "data": {
                "failedReason": {
                    "exception_message": "Custom validation failed for node",
                    "traceback": '["prompt length must be <= 2048"]',
                },
            },
        }

        self.assertEqual(main.runninghub_fail_reason(raw), "prompt length must be <= 2048")

    def test_runninghub_failure_prefers_specific_timeout_over_python_traceback(self):
        raw = {
            "code": 805,
            "data": {
                "failedReason": {
                    "node_name": "RH_GeminiOmniFlashImageToVideo",
                    "exception_type": "RuntimeError",
                    "exception_message": (
                        "[RH_OpenAPI_GeminiOmniFlashImageToVideo] Task execution failed: "
                        "Task failed: Video generation timed out, please retry "
                        "[errorCode: 1006, taskId: 2080132814095134721]"
                    ),
                    "traceback": (
                        '["  File \\"/workspace/ComfyUI/execution.py\\", line 1888, in execute\\n",'
                        '"  File \\"/workspace/ComfyUI/custom_nodes/node_factory.py\\", line 1562, in execute\\n"]'
                    ),
                },
            },
        }

        detail = main.runninghub_fail_reason(raw)

        self.assertIn("视频生成超时", detail)
        self.assertIn("1006", detail)
        self.assertIn("2080132814095134721", detail)
        self.assertNotIn("/workspace/ComfyUI", detail)


if __name__ == "__main__":
    unittest.main()
