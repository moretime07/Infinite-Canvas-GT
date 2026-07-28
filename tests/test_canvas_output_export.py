import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main


class CanvasOutputExportTests(unittest.TestCase):
    def make_request(self, image_folder, video_folder, items, template="{canvas}_{node}_{type}_{index}"):
        return main.CanvasOutputExportRequest(
            canvas_title="测试画布",
            node_id="out-demo",
            name_template=template,
            image_folder=image_folder,
            video_folder=video_folder,
            image_format="jpg",
            video_format="mp4",
            items=items,
        )

    def test_export_local_image_converts_to_jpeg_with_template(self):
        source_name = f"output-auto-export-image-{uuid.uuid4().hex}.png"
        source_path = main.output_path_for(source_name, "output")
        Image.new("RGBA", (4, 4), (255, 0, 0, 128)).save(source_path)
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                result = main.export_canvas_output_items(self.make_request(
                    temp_dir,
                    temp_dir,
                    [main.CanvasOutputExportItem(url=main.output_url_for(source_name, "output"), kind="image")],
                ))
                self.assertEqual(len(result["exported"]), 1)
                saved = result["exported"][0]
                self.assertTrue(saved["name"].endswith(".jpg"))
                self.assertIn("测试画布_out-demo_image_1", saved["name"])
                self.assertTrue(os.path.isfile(saved["path"]))
                with Image.open(saved["path"]) as image:
                    self.assertEqual(image.format, "JPEG")
        finally:
            if os.path.exists(source_path):
                os.remove(source_path)

    def test_export_uses_video_folder_and_unique_names(self):
        ffmpeg = shutil.which("ffmpeg")
        self.assertTrue(ffmpeg, "ffmpeg is required for video export tests")
        source_name = f"output-auto-export-video-{uuid.uuid4().hex}.mp4"
        source_path = main.output_path_for(source_name, "output")
        subprocess.run([
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=4x4:d=0.1",
            "-c:v",
            "libx264",
            source_path,
        ], check=True)
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                image_dir = os.path.join(temp_dir, "images")
                video_dir = os.path.join(temp_dir, "videos")
                request = self.make_request(
                    image_dir,
                    video_dir,
                    [main.CanvasOutputExportItem(url=main.output_url_for(source_name, "output"), kind="video")],
                    template="same-name",
                )
                first = main.export_canvas_output_items(request)["exported"][0]
                second = main.export_canvas_output_items(request)["exported"][0]
                self.assertEqual(os.path.dirname(first["path"]), os.path.abspath(video_dir))
                self.assertNotEqual(first["name"], second["name"])
                self.assertTrue(os.path.isfile(second["path"]))
        finally:
            if os.path.exists(source_path):
                os.remove(source_path)

    def test_openrouter_video_content_uses_configured_provider_key_only_for_matching_host(self):
        providers = [{
            "id": "custom-api",
            "name": "openrouter",
            "base_url": "https://openrouter.ai/api/v1",
            "enabled": True,
        }]
        with patch.object(main, "load_api_providers", return_value=providers), patch.object(
            main,
            "provider_env_key_value",
            return_value="test-openrouter-key",
        ):
            headers = main.remote_media_request_headers(
                "https://openrouter.ai/api/v1/videos/task-id/content?index=0"
            )
            unrelated_headers = main.remote_media_request_headers(
                "https://example.com/api/v1/videos/task-id/content?index=0"
            )
        self.assertEqual(headers.get("Authorization"), "Bearer test-openrouter-key")
        self.assertNotIn("Authorization", unrelated_headers)


class SaveRemoteVideoTests(unittest.IsolatedAsyncioTestCase):
    async def test_openrouter_video_download_uses_configured_authorization(self):
        captured = {}

        class FakeResponse:
            headers = {"Content-Type": "video/mp4"}
            content = b"\x00\x00\x00\x20ftypisomtest-video"

            def raise_for_status(self):
                return None

        class FakeClient:
            def __init__(self, *args, **kwargs):
                captured["headers"] = dict(kwargs.get("headers") or {})

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, url):
                captured["url"] = url
                return FakeResponse()

        url = "https://openrouter.ai/api/v1/videos/task-id/content?index=0"
        saved_url = ""
        with patch.object(
            main,
            "remote_media_request_headers",
            return_value={"Authorization": "Bearer test-openrouter-key"},
        ), patch.object(main.httpx, "AsyncClient", FakeClient):
            saved_url = await main.save_remote_video_to_output(url, prefix="auth_test_")

        try:
            self.assertEqual(captured["url"], url)
            self.assertEqual(captured["headers"].get("Authorization"), "Bearer test-openrouter-key")
            self.assertTrue(saved_url.startswith("/assets/output/auth_test_"))
            saved_path = main.output_file_from_url(saved_url)
            self.assertTrue(saved_path and os.path.isfile(saved_path))
        finally:
            saved_path = main.output_file_from_url(saved_url)
            if saved_path and os.path.isfile(saved_path):
                os.remove(saved_path)


if __name__ == "__main__":
    unittest.main()
