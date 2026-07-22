import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main


class OpenRouterVideoReferenceTests(unittest.TestCase):
    def build(self, **overrides):
        builder = getattr(main, "build_openrouter_video_request", None)
        self.assertIsNotNone(builder, "build_openrouter_video_request is missing")
        values = {
            "prompt": "Follow the reference motion",
            "provider_id": "custom-api",
            "model": "bytedance/seedance-2.0",
            "duration": 7,
            "aspect_ratio": "9:16",
            "resolution": "720p",
        }
        values.update(overrides)
        return builder(main.CanvasVideoRequest(**values))

    def test_multimodal_references_include_image_video_and_audio(self):
        body, counts = self.build(
            images=[main.AIReference(url="data:image/png;base64,AAA")],
            videos=["data:video/mp4;base64,BBB"],
            audios=["https://cdn.example.com/music.mp3"],
            multimodal=True,
        )

        self.assertEqual(body["input_references"], [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
            {"type": "video_url", "video_url": {"url": "data:video/mp4;base64,BBB"}},
            {"type": "audio_url", "audio_url": {"url": "https://cdn.example.com/music.mp3"}},
        ])
        self.assertEqual(counts, {"image": 1, "video": 1, "audio": 1})
        self.assertNotIn("frame_images", body)

    def test_public_video_url_passes_through_unchanged(self):
        body, counts = self.build(videos=["https://cdn.example.com/reference.mp4"])

        self.assertEqual(body["input_references"], [
            {"type": "video_url", "video_url": {"url": "https://cdn.example.com/reference.mp4"}},
        ])
        self.assertEqual(counts, {"image": 0, "video": 1, "audio": 0})

    def test_unroled_image_with_video_uses_reference_mode(self):
        body, _ = self.build(
            images=[main.AIReference(url="data:image/png;base64,AAA")],
            videos=["data:video/mp4;base64,BBB"],
            multimodal=False,
        )

        self.assertNotIn("frame_images", body)
        self.assertEqual([item["type"] for item in body["input_references"]], ["image_url", "video_url"])

    def test_image_only_keeps_first_frame_compatibility(self):
        body, counts = self.build(
            images=[main.AIReference(url="data:image/png;base64,AAA")],
            multimodal=False,
        )

        self.assertEqual(body["frame_images"], [{
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,AAA"},
            "frame_type": "first_frame",
        }])
        self.assertNotIn("input_references", body)
        self.assertEqual(counts, {"image": 1, "video": 0, "audio": 0})

    def test_local_video_becomes_mime_correct_data_url(self):
        handle = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        try:
            handle.write(b"test-video-bytes")
            handle.close()
            with patch.object(main, "output_file_from_url", return_value=handle.name):
                body, _ = self.build(videos=["/assets/input/reference.mp4"])

            url = body["input_references"][0]["video_url"]["url"]
            self.assertTrue(url.startswith("data:video/mp4;base64,"))
        finally:
            handle.close()
            if os.path.exists(handle.name):
                os.unlink(handle.name)

    def test_explicit_frame_with_video_is_rejected_before_submission(self):
        with self.assertRaises(HTTPException) as ctx:
            self.build(
                images=[main.AIReference(url="data:image/png;base64,AAA", role="first_frame")],
                videos=["data:video/mp4;base64,BBB"],
            )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("首尾帧", str(ctx.exception.detail))
        self.assertIn("视频", str(ctx.exception.detail))

    def test_missing_local_video_is_not_silently_ignored(self):
        with patch.object(main, "output_file_from_url", return_value=None):
            with self.assertRaises(HTTPException) as ctx:
                self.build(videos=["/assets/input/missing.mp4"])

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("不存在", str(ctx.exception.detail))


if __name__ == "__main__":
    unittest.main()
