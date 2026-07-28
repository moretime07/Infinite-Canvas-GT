import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main


class _UploadResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise json.JSONDecodeError("Expecting value", self.text, 0)
        return self._payload


class _UploadClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.posts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, **kwargs):
        self.posts.append({"url": url, **kwargs})
        return self.responses.pop(0)


class RunningHubUploadRetryTests(unittest.IsolatedAsyncioTestCase):
    def patches(self, client):
        return (
            patch.object(main, "runninghub_provider", return_value={"base_url": "https://example.invalid"}),
            patch.object(main, "runninghub_api_key", return_value="test-key"),
            patch.object(main, "runninghub_app_headers", return_value={}),
            patch.object(main, "runninghub_local_asset_path", return_value=__file__),
            patch.object(main, "content_type_for_path", return_value="text/plain"),
            patch.object(main.httpx, "AsyncClient", return_value=client),
            patch.object(main.asyncio, "sleep", new=AsyncMock()),
        )

    async def test_empty_gateway_response_is_retried_once(self):
        client = _UploadClient([
            _UploadResponse(502, text=""),
            _UploadResponse(200, {
                "code": 0,
                "data": {"fileName": "api/uploaded.png", "fileType": "input"},
            }),
        ])
        payload = main.RunningHubUploadAssetRequest(url="/assets/output/example.png", useWallet=True)

        with self.patches(client)[0], self.patches(client)[1], self.patches(client)[2], self.patches(client)[3], self.patches(client)[4], self.patches(client)[5], self.patches(client)[6]:
            result = await main.runninghub_upload_asset(payload)

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["fileName"], "api/uploaded.png")
        self.assertEqual(len(client.posts), 2)

    async def test_non_json_failure_reports_http_status_and_body_preview(self):
        client = _UploadClient([
            _UploadResponse(400, text="<html>Bad gateway request</html>"),
        ])
        payload = main.RunningHubUploadAssetRequest(url="/assets/output/example.png")

        with self.patches(client)[0], self.patches(client)[1], self.patches(client)[2], self.patches(client)[3], self.patches(client)[4], self.patches(client)[5], self.patches(client)[6]:
            with self.assertRaises(HTTPException) as raised:
                await main.runninghub_upload_asset(payload)

        detail = str(raised.exception.detail)
        self.assertIn("HTTP 400", detail)
        self.assertIn("Bad gateway request", detail)
        self.assertNotIn("Expecting value", detail)


if __name__ == "__main__":
    unittest.main()
