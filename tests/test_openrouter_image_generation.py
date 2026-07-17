import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main


class FakeImageResponse:
    status_code = 200
    text = ""

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "created": 1784281499,
            "data": [{"b64_json": "generated-image"}],
            "usage": {
                "prompt_tokens": 37,
                "completion_tokens": 1120,
                "total_tokens": 1157,
            },
        }


class RecordingAsyncClient:
    def __init__(self):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return FakeImageResponse()


class OpenRouterImageGenerationTests(unittest.IsolatedAsyncioTestCase):
    provider = {
        "id": "custom-api",
        "name": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "protocol": "openai",
        "image_request_mode": "openai",
    }

    async def generate(self, references):
        client = RecordingAsyncClient()
        with patch.object(main, "get_api_provider", return_value=self.provider), patch.object(
            main.httpx, "AsyncClient", return_value=client
        ), patch.object(main, "api_headers", return_value={"Authorization": "Bearer test"}):
            result = await main.generate_ai_image(
                "keep image one structure and use image two style",
                "1280x720",
                "high",
                "google/gemini-3-pro-image",
                references,
                "custom-api",
            )
        return client, result

    async def test_reference_images_use_openrouter_images_endpoint_and_input_references(self):
        references = [
            {"url": "data:image/png;base64,AAA", "name": "structure.png"},
            {"url": "data:image/png;base64,BBB", "name": "style.png"},
        ]

        client, (image, raw) = await self.generate(references)

        self.assertEqual([call["url"] for call in client.calls], ["https://openrouter.ai/api/v1/images"])
        body = client.calls[0]["json"]
        self.assertEqual(body["model"], "google/gemini-3-pro-image")
        self.assertEqual(body["prompt"], "keep image one structure and use image two style")
        self.assertEqual(body["size"], "1280x720")
        self.assertEqual(body["quality"], "high")
        self.assertEqual(body["n"], 1)
        self.assertEqual(body["input_references"], [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,BBB"}},
        ])
        self.assertNotIn("image", body)
        self.assertEqual(image["type"], "b64")
        self.assertEqual(image["value"], "generated-image")
        self.assertEqual(raw["data"][0]["b64_json"], "generated-image")

    async def test_text_to_image_omits_empty_input_references(self):
        client, _ = await self.generate([])

        self.assertEqual([call["url"] for call in client.calls], ["https://openrouter.ai/api/v1/images"])
        self.assertNotIn("input_references", client.calls[0]["json"])


if __name__ == "__main__":
    unittest.main()
