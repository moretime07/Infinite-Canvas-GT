import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import main


class OminiLinkVideoRequestTests(unittest.IsolatedAsyncioTestCase):
    def test_video_api_root_prefers_the_normalized_video_base_url(self):
        provider = {
            "base_url": "https://api.aig-ai.com/v1",
            "video_base_url": "https://vg-api.aig-ai.com/v1/",
        }

        self.assertEqual(
            main.ominilink_video_api_root(provider),
            "https://vg-api.aig-ai.com/v1",
        )

    async def test_text_to_video_body(self):
        payload = main.CanvasVideoRequest(
            prompt="a flying ship", model="gemini-omni-flash-preview",
            duration=6, aspect_ratio="16:9",
        )

        body = await main.build_ominilink_omni_request(payload)

        self.assertEqual(body, {
            "model": "gemini-omni-flash-preview",
            "background": True,
            "input": [{
                "type": "user_input",
                "content": [{"type": "text", "text": "a flying ship"}],
            }],
            "generation_config": {
                "thinking_level": "low",
                "thinking_summaries": "auto",
                "video_config": {"task": "text_to_video"},
            },
            "response_format": {
                "type": "video",
                "delivery": "uri",
                "aspect_ratio": "16:9",
                "duration": "6s",
            },
        })

    async def test_image_to_video_contains_mime_and_base64(self):
        payload = main.CanvasVideoRequest(
            prompt="move", model="gemini-omni-flash-preview",
            images=[main.AIReference(url="data:image/png;base64,aGVsbG8=")],
        )

        body = await main.build_ominilink_omni_request(payload)

        self.assertEqual(body["generation_config"]["video_config"]["task"], "image_to_video")
        self.assertEqual(body["input"][0]["content"], [
            {"type": "text", "text": "move"},
            {"type": "image", "data": "aGVsbG8=", "mime_type": "image/png"},
        ])

    async def test_image_to_video_rejects_invalid_base64_locally(self):
        for reference_url in (
            "data:image/png;base64,%%%",
            "data:image/png;base64,aGVsbG8=%%%",
        ):
            with self.subTest(reference_url=reference_url):
                payload = main.CanvasVideoRequest(
                    prompt="move", model="gemini-omni-flash-preview",
                    images=[main.AIReference(url=reference_url)],
                )

                with self.assertRaises(main.HTTPException) as raised:
                    await main.build_ominilink_omni_request(payload)

                self.assertEqual(raised.exception.status_code, 400)
                self.assertIn("base64", raised.exception.detail)

    async def test_image_to_video_rejects_non_image_mimes(self):
        for reference_url in (
            "data:video/mp4;base64,dmlkZW8=",
            "data:text/plain;base64,dGV4dA==",
        ):
            with self.subTest(reference_url=reference_url):
                payload = main.CanvasVideoRequest(
                    prompt="move", model="gemini-omni-flash-preview",
                    images=[main.AIReference(url=reference_url)],
                )

                with self.assertRaises(main.HTTPException) as raised:
                    await main.build_ominilink_omni_request(payload)

                self.assertEqual(raised.exception.status_code, 400)
                self.assertIn("图片", raised.exception.detail)

    async def test_video_edit_contains_data_url_media(self):
        payload = main.CanvasVideoRequest(
            prompt="edit", model="gemini-omni-flash-preview",
            videos=["data:video/mp4;base64,dmlkZW8="],
        )

        body = await main.build_ominilink_omni_request(payload)

        self.assertEqual(body["generation_config"]["video_config"]["task"], "edit")
        self.assertEqual(body["input"][0]["content"], [
            {"type": "text", "text": "edit"},
            {"type": "video", "data": "dmlkZW8=", "mime_type": "video/mp4"},
        ])

    async def test_video_edit_rejects_non_video_mimes(self):
        for reference_url in (
            "data:image/png;base64,aGVsbG8=",
            "data:text/plain;base64,dGV4dA==",
        ):
            with self.subTest(reference_url=reference_url):
                payload = main.CanvasVideoRequest(
                    prompt="edit", model="gemini-omni-flash-preview",
                    videos=[reference_url],
                )

                with self.assertRaises(main.HTTPException) as raised:
                    await main.build_ominilink_omni_request(payload)

                self.assertEqual(raised.exception.status_code, 400)
                self.assertIn("视频", raised.exception.detail)

    async def test_video_edit_rejects_http_references_locally(self):
        payload = main.CanvasVideoRequest(
            prompt="edit", model="gemini-omni-flash-preview",
            videos=["https://cdn.example.test/clip.mp4"],
        )

        with self.assertRaises(main.HTTPException) as raised:
            await main.build_ominilink_omni_request(payload)

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("HTTP", raised.exception.detail)

    async def test_video_edit_rejects_an_unresolvable_local_reference(self):
        payload = main.CanvasVideoRequest(
            prompt="edit", model="gemini-omni-flash-preview",
            videos=["/assets/input/missing.mp4"],
        )

        with patch.object(main, "local_media_path_for_cloud_upload", return_value=""):
            with self.assertRaises(main.HTTPException) as raised:
                await main.build_ominilink_omni_request(payload)

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("本地", raised.exception.detail)

    async def test_video_edit_encodes_a_local_asset(self):
        handle = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        try:
            handle.write(b"video-bytes")
            handle.close()
            payload = main.CanvasVideoRequest(
                prompt="edit", model="gemini-omni-flash-preview",
                videos=["/assets/input/clip.mp4"],
            )

            with patch.object(main, "local_media_path_for_cloud_upload", return_value=handle.name):
                body = await main.build_ominilink_omni_request(payload)

            self.assertEqual(body["input"][0]["content"][1], {
                "type": "video",
                "data": "dmlkZW8tYnl0ZXM=",
                "mime_type": "video/mp4",
            })
        finally:
            if not handle.closed:
                handle.close()
            if os.path.exists(handle.name):
                os.unlink(handle.name)

    async def test_video_edit_rejects_known_reference_over_three_seconds(self):
        payload = main.CanvasVideoRequest(
            prompt="edit", model="gemini-omni-flash-preview",
            videos=["/assets/input/long.mp4"],
        )

        with patch.object(main, "probe_local_video_duration_seconds", return_value=3.1):
            with self.assertRaises(main.HTTPException) as raised:
                await main.build_ominilink_omni_request(payload)

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("3 秒", raised.exception.detail)

    async def test_unsupported_input_combinations_fail_locally(self):
        cases = [
            (
                main.CanvasVideoRequest(
                    prompt="x", model="gemini-omni-flash-preview",
                    images=[main.AIReference(url="data:image/png;base64,YQ==")],
                    videos=["data:video/mp4;base64,YQ=="],
                ),
                "不能同时",
            ),
            (
                main.CanvasVideoRequest(
                    prompt="x", model="gemini-omni-flash-preview",
                    audios=["/assets/input/a.mp3"],
                ),
                "不支持音频",
            ),
            (
                main.CanvasVideoRequest(
                    prompt="x", model="gemini-omni-flash-preview", duration=2,
                ),
                "3 到 10",
            ),
            (
                main.CanvasVideoRequest(
                    prompt="x", model="gemini-omni-flash-preview", duration=11,
                ),
                "3 到 10",
            ),
            (
                main.CanvasVideoRequest(
                    prompt="x", model="gemini-omni-flash-preview",
                    images=[
                        main.AIReference(url="data:image/png;base64,YQ=="),
                        main.AIReference(url="data:image/png;base64,Yg=="),
                    ],
                ),
                "只支持一张参考图",
            ),
            (
                main.CanvasVideoRequest(
                    prompt="x", model="gemini-omni-flash-preview",
                    videos=[
                        "data:video/mp4;base64,YQ==",
                        "data:video/mp4;base64,Yg==",
                    ],
                ),
                "只支持一个参考视频",
            ),
        ]
        for payload, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaises(main.HTTPException) as raised:
                    await main.build_ominilink_omni_request(payload)
                self.assertEqual(raised.exception.status_code, 400)
                self.assertIn(expected, raised.exception.detail)

    def test_video_duration_probe_returns_ffprobe_duration(self):
        with patch.object(main, "output_file_from_url", return_value="C:/media/clip.mp4"), \
                patch.object(main.os.path, "isfile", return_value=True), \
                patch.object(main.shutil, "which", return_value="ffprobe"), \
                patch.object(
                    main.subprocess,
                    "run",
                    return_value=SimpleNamespace(returncode=0, stdout="2.75\n"),
                ):
            duration = main.probe_local_video_duration_seconds("/assets/input/clip.mp4")

        self.assertEqual(duration, 2.75)


if __name__ == "__main__":
    unittest.main()
