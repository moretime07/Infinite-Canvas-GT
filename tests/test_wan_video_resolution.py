import copy
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx

import main


class WanVideoResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def submit(self, **overrides):
        provider = {
            "id": "wan-resolution-test", "name": "Wan test",
            "base_url": "https://wan.example.test/v1", "protocol": "openai",
        }
        values = {
            "provider_id": provider["id"], "model": "wan3.0-video",
            "prompt": "A moving landscape", "resolution": "720p",
            "duration": 5, "aspect_ratio": "16:9", "enable_upsample": False,
            "images": [{"url": "data:image/png;base64,TEST"}],
        }
        values.update(overrides)
        self.requests = []

        def respond(request):
            self.requests.append(json.loads(request.content))
            return httpx.Response(200, json={
                "data": [{"url": "https://cdn.example.test/result.mp4"}],
            })

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        save = AsyncMock(return_value="/assets/output/test.mp4")
        with patch.object(main, "get_api_provider", return_value=provider), \
                patch.dict(os.environ, {main.provider_key_env(provider["id"]): "test-only"}), \
                patch.object(main.httpx, "AsyncClient", return_value=client), \
                patch.object(main, "reference_to_data_url", return_value="data:image/png;base64,TEST"), \
                patch.object(main, "save_remote_video_to_output", save):
            result = await main.canvas_video(main.CanvasVideoRequest(**values))
        self.assertEqual(result["videos"], ["/assets/output/test.mp4"])
        self.assertEqual(len(self.requests), 1)
        return self.requests[0]

    async def test_selected_resolution_reaches_legacy_and_current_wan_adapters(self):
        for model in ("wan3.0-video", "wan3.0-video-prime"):
            for resolution in ("480p", "720p", "1080p"):
                with self.subTest(model=model, resolution=resolution):
                    body = await self.submit(model=model, resolution=resolution)
                    expected = resolution.upper()
                    self.assertEqual(body["resolution"], expected)
                    self.assertEqual(body["size"], expected)
                    self.assertEqual(body["metadata"]["parameters"]["resolution"], expected)
                    self.assertEqual(body["metadata"]["parameters"]["ratio"], "16:9")
                    # The legacy New API adapter ignores top-level resolution,
                    # reads size, then applies metadata.parameters overrides.
                    native_resolution = body.get("size") or "1080P"
                    native_resolution = body.get("metadata", {}).get("parameters", {}).get(
                        "resolution", native_resolution
                    )
                    self.assertEqual(native_resolution, expected)
                    self.assertNotIn("enable_upsample", body)
                    self.assertEqual(body["duration"], 5)
                    self.assertEqual(body["aspect_ratio"], "16:9")
                    self.assertEqual(body["images"], ["data:image/png;base64,TEST"])

    async def test_explicit_resolution_overrides_stale_size(self):
        body = await self.submit(resolution=" 720P ", size="1920x1080")
        self.assertEqual(body["size"], "720P")
        self.assertEqual(body["metadata"]["parameters"]["resolution"], "720P")

    async def test_auto_does_not_silently_pick_a_resolution(self):
        body = await self.submit(resolution="")
        self.assertNotIn("size", body)
        self.assertNotIn("resolution", body)
        self.assertEqual(body["metadata"]["parameters"], {"ratio": "16:9"})

    async def test_size_only_request_keeps_existing_behavior(self):
        body = await self.submit(resolution="", size="1280*720")
        self.assertEqual(body["size"], "1280*720")
        self.assertEqual(body["metadata"]["parameters"], {"ratio": "16:9"})

    async def test_selected_aspect_and_resolution_are_sent_to_native_wan_parameters(self):
        for ratio in ("9:16", "16:9", "1:1", "4:3", "3:4", "21:9", "9:21"):
            with self.subTest(ratio=ratio):
                body = await self.submit(aspect_ratio=ratio, resolution="720p")
                self.assertEqual(body["aspect_ratio"], ratio)
                self.assertEqual(body["ratio"], ratio)
                self.assertEqual(body["metadata"]["parameters"], {
                    "ratio": ratio, "resolution": "720P",
                })

    async def test_auto_resolution_still_transmits_selected_portrait_ratio(self):
        body = await self.submit(aspect_ratio="9:16", resolution="")
        self.assertEqual(body["metadata"]["parameters"], {"ratio": "9:16"})
        self.assertNotIn("resolution", body)

    async def test_only_explicit_auto_ratio_uses_adaptive(self):
        for ratio in ("adaptive", "keep_ratio"):
            with self.subTest(ratio=ratio):
                body = await self.submit(aspect_ratio=ratio)
                self.assertEqual(body["metadata"]["parameters"]["ratio"], "adaptive")
                self.assertEqual(body["ratio"], "adaptive")

    async def test_invalid_ratio_is_not_replaced_by_an_upstream_default(self):
        with self.assertRaises(main.HTTPException) as raised:
            await self.submit(aspect_ratio="0:16")
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(self.requests, [])

    def test_native_stale_size_and_adaptive_ratio_cannot_override_the_ui(self):
        body = {
            "model": "wan3.0-video", "aspect_ratio": "9:16", "ratio": "16:9",
            "resolution": "720p", "size": "1920*1080",
            "metadata": {"parameters": {"size": "1920*1080", "resolution": "1080P", "ratio": "adaptive", "seed": 7}},
        }
        main.apply_wan_video_resolution(body)
        self.assertEqual(body["metadata"]["parameters"], {"resolution": "720P", "ratio": "9:16", "seed": 7})
        self.assertEqual(body["size"], "720P")
        self.assertEqual(body["ratio"], "9:16")

    async def test_other_models_do_not_receive_wan_parameters(self):
        body = await self.submit(model="veo3-fast", size="1280x720")
        self.assertEqual(body["resolution"], "720p")
        self.assertEqual(body["size"], "1280x720")
        self.assertNotIn("metadata", body)

    async def test_unsupported_resolution_is_rejected_before_upstream_submission(self):
        with self.assertRaises(main.HTTPException) as raised:
            await self.submit(resolution="4k")
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("720p", str(raised.exception.detail))
        self.assertEqual(self.requests, [])

    def test_preserves_unrelated_metadata_without_mutating_shared_values(self):
        metadata = {"input": {"extra": "keep"}, "parameters": {"seed": 42, "resolution": "1080P"}}
        original = copy.deepcopy(metadata)
        body = {"model": "wan3.0-video", "resolution": "720p", "metadata": metadata}
        main.apply_wan_video_resolution(body)
        self.assertEqual(body["metadata"]["parameters"], {"seed": 42, "resolution": "720P"})
        self.assertEqual(body["metadata"]["input"], {"extra": "keep"})
        self.assertEqual(metadata, original)


if __name__ == "__main__":
    unittest.main()
