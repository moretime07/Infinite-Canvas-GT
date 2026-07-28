import asyncio
import base64
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

import main


class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = {}

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class RecordingClient:
    def __init__(self, post_responses, get_responses=()):
        self.post_responses = iter(post_responses)
        self.get_responses = iter(get_responses)
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return next(self.post_responses)

    async def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        response = next(self.get_responses)
        if isinstance(response, Exception):
            raise response
        return response


class ContextRecordingClient(RecordingClient):
    def __init__(self, post_responses, get_responses=()):
        super().__init__(post_responses, get_responses)
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, *_):
        self.exited = True
        return False


class HungRecordingClient(RecordingClient):
    def __init__(self, post_responses, get_responses=()):
        super().__init__(post_responses, get_responses)
        self.cancelled = False

    async def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class ExceptionRecordingClient(RecordingClient):
    def __init__(self, exc):
        super().__init__([])
        self.exc = exc

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        raise self.exc


class DownloadResponse:
    def __init__(self, status_code=200, content=b"video", headers=None, chunks=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {"Content-Type": "video/mp4"}
        self.chunks = list(chunks) if chunks is not None else [content]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def aiter_bytes(self):
        for chunk in self.chunks:
            yield chunk

    def raise_for_status(self):
        if self.status_code >= 400:
            raise main.httpx.HTTPStatusError(
                "download failed",
                request=main.httpx.Request("GET", "https://public.example.test/video.mp4"),
                response=main.httpx.Response(self.status_code),
            )


class DownloadClient:
    def __init__(self, responses=(), exc=None):
        self.responses = iter(responses)
        self.exc = exc
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        if self.exc:
            raise self.exc
        return next(self.responses)

    def stream(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.exc:
            raise self.exc
        return next(self.responses)


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

    async def test_multiple_images_are_forwarded_without_a_local_count_limit(self):
        references = [
            main.AIReference(url=f"data:image/png;base64,{base64.b64encode(str(index).encode()).decode()}")
            for index in range(12)
        ]
        payload = main.CanvasVideoRequest(
            prompt="keep every reference", model="gemini-omni-flash-preview",
            images=references,
        )

        body = await main.build_ominilink_omni_request(payload)

        self.assertEqual(
            body["generation_config"]["video_config"]["task"],
            "reference_to_video",
        )
        image_items = [
            item for item in body["input"][0]["content"]
            if item.get("type") == "image"
        ]
        self.assertEqual(len(image_items), 12)
        self.assertEqual(
            [item["data"] for item in image_items],
            [base64.b64encode(str(index).encode()).decode() for index in range(12)],
        )

    async def test_long_prompt_is_forwarded_without_a_local_character_limit(self):
        prompt = "长" * 5001
        payload = main.CanvasVideoRequest(
            prompt=prompt, model="gemini-omni-flash-preview",
        )

        body = await main.build_ominilink_omni_request(payload)

        self.assertEqual(body["input"][0]["content"][0], {
            "type": "text",
            "text": prompt,
        })

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

    async def test_image_to_video_rejects_invalid_image_mime_syntax(self):
        for reference_url in (
            "data:image/;base64,YQ==",
            "data:image/pn g;base64,YQ==",
        ):
            with self.subTest(reference_url=reference_url):
                payload = main.CanvasVideoRequest(
                    prompt="move", model="gemini-omni-flash-preview",
                    images=[main.AIReference(url=reference_url)],
                )

                with self.assertRaises(main.HTTPException) as raised:
                    await main.build_ominilink_omni_request(payload)

                self.assertEqual(raised.exception.status_code, 400)
                self.assertIn("MIME", raised.exception.detail)

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

    async def test_image_and_video_accept_safe_public_https_uri(self):
        resolver = lambda _host: ["93.184.216.34"]
        image = await main.build_ominilink_omni_request(
            main.CanvasVideoRequest(
                prompt="move", model="gemini-omni-flash-preview",
                images=[main.AIReference(url="https://cdn.example.test/reference.png")],
            ),
            resolver=resolver,
        )
        video = await main.build_ominilink_omni_request(
            main.CanvasVideoRequest(
                prompt="edit", model="gemini-omni-flash-preview",
                videos=["https://cdn.example.test/reference.mp4"],
            ),
            resolver=resolver,
        )

        self.assertEqual(image["input"][0]["content"][1], {
            "type": "image", "uri": "https://cdn.example.test/reference.png",
        })
        self.assertEqual(video["input"][0]["content"][1], {
            "type": "video", "uri": "https://cdn.example.test/reference.mp4",
        })

    async def test_oversized_local_media_is_rejected_without_reading_bytes(self):
        payload = main.CanvasVideoRequest(
            prompt="edit", model="gemini-omni-flash-preview",
            videos=["/assets/input/large.mp4"],
        )
        with patch.object(main, "local_media_path_for_cloud_upload", return_value="C:/media/large.mp4"), \
                patch.object(main.os.path, "isfile", return_value=True), \
                patch.object(main.os.path, "getsize", return_value=4 * 1024 * 1024 + 1), \
                patch("builtins.open", side_effect=AssertionError("oversized media must not be read")):
            with self.assertRaises(main.HTTPException) as raised:
                await main.build_ominilink_omni_request(payload)

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("HTTPS", raised.exception.detail)

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

    async def test_video_edit_rejects_invalid_video_mime_syntax(self):
        for reference_url in (
            "data:video/;base64,YQ==",
            "data:video/mp 4;base64,YQ==",
        ):
            with self.subTest(reference_url=reference_url):
                payload = main.CanvasVideoRequest(
                    prompt="edit", model="gemini-omni-flash-preview",
                    videos=[reference_url],
                )

                with self.assertRaises(main.HTTPException) as raised:
                    await main.build_ominilink_omni_request(payload)

                self.assertEqual(raised.exception.status_code, 400)
                self.assertIn("MIME", raised.exception.detail)

    async def test_media_mime_tokens_allow_plus_period_and_hyphen(self):
        image_body = await main.build_ominilink_omni_request(main.CanvasVideoRequest(
            prompt="move", model="gemini-omni-flash-preview",
            images=[main.AIReference(url="data:image/vnd.example+json.test-base64;base64,YQ==")],
        ))
        video_body = await main.build_ominilink_omni_request(main.CanvasVideoRequest(
            prompt="edit", model="gemini-omni-flash-preview",
            videos=["data:video/vnd.example+json.test-base64;base64,YQ=="],
        ))

        self.assertEqual(image_body["input"][0]["content"][1]["mime_type"], "image/vnd.example+json.test-base64")
        self.assertEqual(video_body["input"][0]["content"][1]["mime_type"], "video/vnd.example+json.test-base64")

    async def test_video_edit_rejects_http_and_private_https_references_locally(self):
        payload = main.CanvasVideoRequest(
            prompt="edit", model="gemini-omni-flash-preview",
            videos=["http://cdn.example.test/clip.mp4"],
        )

        with self.assertRaises(main.HTTPException) as raised:
            await main.build_ominilink_omni_request(payload)

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("HTTP", raised.exception.detail)

        with self.assertRaises(main.HTTPException) as raised:
            await main.build_ominilink_omni_request(
                main.CanvasVideoRequest(
                    prompt="edit", model="gemini-omni-flash-preview",
                    videos=["https://user:pass@127.0.0.1/clip.mp4"],
                ),
                resolver=lambda _host: ["127.0.0.1"],
            )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("HTTPS", raised.exception.detail)

    def test_public_https_uri_rejects_non_unicast_and_mapped_special_addresses(self):
        unsafe_literals = (
            "https://0.0.0.0/media.mp4",
            "https://10.0.0.1/media.mp4",
            "https://127.0.0.1/media.mp4",
            "https://169.254.1.1/media.mp4",
            "https://224.0.0.1/media.mp4",
            "https://240.0.0.1/media.mp4",
            "https://[::]/media.mp4",
            "https://[::1]/media.mp4",
            "https://[fc00::1]/media.mp4",
            "https://[fe80::1]/media.mp4",
            "https://[ff02::1]/media.mp4",
            "https://[64:ff9b:1::1]/media.mp4",
            "https://[::ffff:127.0.0.1]/media.mp4",
            "https://[::ffff:10.0.0.1]/media.mp4",
        )
        for value in unsafe_literals:
            with self.subTest(value=value), self.assertRaises(ValueError):
                main.ominilink_public_https_uri(value)

        for resolved in ("224.0.0.1", "ff02::1", "64:ff9b:1::1", "::ffff:127.0.0.1"):
            with self.subTest(resolved=resolved), self.assertRaises(ValueError):
                main.ominilink_public_https_uri(
                    "https://cdn.example.test/media.mp4",
                    resolver=lambda _host, address=resolved: [address],
                )

        self.assertEqual(
            main.ominilink_public_https_uri("https://[2001:4860:4860::8888]/media.mp4"),
            "https://[2001:4860:4860::8888]/media.mp4",
        )

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


class OminiLinkVideoAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.provider = {
            "id": "orange",
            "name": "OminiLink",
            "base_url": "https://api.aig-ai.com/v1",
            "video_base_url": "https://vg-api.aig-ai.com/v1",
        }
        self.env_patch = patch.dict(
            main.os.environ,
            {"API_PROVIDER_ORANGE_KEY": "test-key"},
        )
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()

    def test_extracts_only_nested_video_uri(self):
        raw = {
            "output_url": "https://cdn.example.test/unrelated.mp4",
            "steps": [{"content": [
                {"type": "text", "uri": "https://cdn.example.test/text.mp4", "text": "done"},
                {"type": "video", "uri": "file:///tmp/not-remote.mp4"},
                {"type": "video", "uri": "https://cdn.example.test/result.mp4"},
            ]}],
        }

        self.assertEqual(
            main.ominilink_video_output_urls(raw),
            ["https://cdn.example.test/result.mp4"],
        )

    async def test_result_downloader_saves_public_https_video_as_local_asset(self):
        target = os.path.join(tempfile.gettempdir(), "ominilink-result.mp4")
        client = DownloadClient([DownloadResponse(content=b"durable-video")])
        replace_calls = []
        real_replace = os.replace

        def record_atomic_replace(source, destination):
            self.assertFalse(os.path.exists(destination))
            replace_calls.append((source, destination))
            real_replace(source, destination)

        if os.path.exists(target):
            os.unlink(target)
        with patch.object(main, "output_path_for", return_value=target), \
                patch.object(main, "output_url_for", return_value="/assets/output/ominilink-result.mp4"), \
                patch.object(main.os, "replace", side_effect=record_atomic_replace):
            result = await main.save_ominilink_video_to_output(
                "https://cdn.example.test/result.mp4?token=secret",
                client=client,
                resolver=lambda _host: ["93.184.216.34"],
            )
        try:
            self.assertEqual(result, "/assets/output/ominilink-result.mp4")
            with open(target, "rb") as handle:
                self.assertEqual(handle.read(), b"durable-video")
            self.assertEqual(client.calls[0][0], "GET")
            self.assertEqual(client.calls[0][1], "https://93.184.216.34/result.mp4?token=secret")
            self.assertEqual(client.calls[0][2]["headers"]["Host"], "cdn.example.test")
            self.assertEqual(client.calls[0][2]["extensions"]["sni_hostname"], "cdn.example.test")
            self.assertFalse(client.calls[0][2].get("follow_redirects", True))
            self.assertEqual(len(replace_calls), 1)
            self.assertTrue(replace_calls[0][0].startswith(f"{target}.part-"))
            self.assertEqual(replace_calls[0][1], target)
            self.assertFalse(any(name.startswith("ominilink-result.mp4.part-") for name in os.listdir(tempfile.gettempdir())))
        finally:
            if os.path.exists(target):
                os.unlink(target)

    async def test_result_downloader_pins_and_revalidates_every_redirect_hop(self):
        target = os.path.join(tempfile.gettempdir(), "ominilink-redirect-result.mp4")
        client = DownloadClient([
            DownloadResponse(302, headers={"Location": "https://media.example.test:8443/final.mp4?key=opaque"}),
            DownloadResponse(content=b"redirected-video"),
        ])
        resolved_hosts = []

        def resolver(host):
            resolved_hosts.append(host)
            return {
                "cdn.example.test": ["93.184.216.34"],
                "media.example.test": ["1.1.1.1"],
            }[host]

        with patch.object(main, "output_path_for", return_value=target), \
                patch.object(main, "output_url_for", return_value="/assets/output/ominilink-redirect-result.mp4"):
            result = await main.save_ominilink_video_to_output(
                "https://cdn.example.test/start.mp4",
                client=client,
                resolver=resolver,
            )
        try:
            self.assertEqual(result, "/assets/output/ominilink-redirect-result.mp4")
            self.assertEqual(resolved_hosts, ["cdn.example.test", "media.example.test"])
            self.assertEqual(
                [(method, url) for method, url, _kwargs in client.calls],
                [
                    ("GET", "https://93.184.216.34/start.mp4"),
                    ("GET", "https://1.1.1.1:8443/final.mp4?key=opaque"),
                ],
            )
            self.assertEqual(client.calls[0][2]["headers"]["Host"], "cdn.example.test")
            self.assertEqual(client.calls[0][2]["extensions"]["sni_hostname"], "cdn.example.test")
            self.assertEqual(client.calls[1][2]["headers"]["Host"], "media.example.test:8443")
            self.assertEqual(client.calls[1][2]["extensions"]["sni_hostname"], "media.example.test")
        finally:
            if os.path.exists(target):
                os.unlink(target)

    async def test_result_downloader_bounds_stream_and_removes_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, "bounded.mp4")
            client = DownloadClient([
                DownloadResponse(content=b"", chunks=[b"1234", b"56"]),
            ])
            with patch.object(main, "OMINILINK_RESULT_MAX_BYTES", 5), \
                    patch.object(main, "output_path_for", return_value=target):
                with self.assertRaises(main.HTTPException) as raised:
                    await main.save_ominilink_video_to_output(
                        "https://cdn.example.test/bounded.mp4",
                        client=client,
                        resolver=lambda _host: ["93.184.216.34"],
                    )

            self.assertEqual(raised.exception.status_code, 502)
            self.assertFalse(os.path.exists(target))
            self.assertEqual(os.listdir(directory), [])

    async def test_result_downloader_rejects_private_initial_and_redirect_hosts(self):
        with self.assertRaises(main.HTTPException) as raised:
            await main.save_ominilink_video_to_output(
                "https://private.example.test/result.mp4",
                client=DownloadClient(),
                resolver=lambda _host: ["127.0.0.1"],
            )
        self.assertEqual(raised.exception.status_code, 502)

        client = DownloadClient([DownloadResponse(302, headers={"Location": "https://127.0.0.1/private.mp4"})])
        with self.assertRaises(main.HTTPException) as raised:
            await main.save_ominilink_video_to_output(
                "https://cdn.example.test/result.mp4",
                client=client,
                resolver=lambda _host: ["93.184.216.34"],
            )
        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(len(client.calls), 1)

    async def test_result_download_failure_never_leaks_signed_url_or_returns_it(self):
        signed = "https://cdn.example.test/result.mp4?token=private-signed-token"
        client = DownloadClient(exc=main.httpx.ConnectError(
            f"connect failed: {signed}",
            request=main.httpx.Request("GET", signed),
        ))
        with self.assertRaises(main.HTTPException) as raised:
            await main.save_ominilink_video_to_output(
                signed,
                client=client,
                resolver=lambda _host: ["93.184.216.34"],
            )
        self.assertEqual(raised.exception.status_code, 502)
        self.assertNotIn("private-signed-token", raised.exception.detail)
        self.assertNotIn("cdn.example.test", raised.exception.detail)

        completed = FakeResponse(200, {
            "id": "job-download-fallback", "status": "completed",
            "steps": [{"content": [{"type": "video", "uri": signed}]}],
        })
        with patch.object(main, "save_ominilink_video_to_output", new=AsyncMock(return_value=signed)):
            with self.assertRaises(main.HTTPException) as raised:
                await main.generate_ominilink_video(
                    main.CanvasVideoRequest(prompt="go", model="gemini-omni-flash-preview"),
                    self.provider,
                    client=RecordingClient([completed]),
                )
        self.assertEqual(raised.exception.status_code, 502)
        self.assertNotIn("private-signed-token", raised.exception.detail)

    def test_video_size_maps_supported_ratios_and_falls_back(self):
        expected = {
            "16:9": "1280x720",
            "9:16": "720x1280",
            "1:1": "720x720",
            "4:3": "960x720",
            "3:4": "720x960",
            "21:9": "1680x720",
            "9:21": "720x1680",
            "unknown": "1280x720",
        }

        self.assertEqual(
            {
                ratio: main.ominilink_video_size(ratio, "1080p")
                for ratio in expected
            },
            expected,
        )

    def test_video_headers_always_use_provider_bearer_key(self):
        provider = {
            **self.provider,
            "protocol": "gemini",
            "model_protocols": {"gemini-omni-flash-preview": "gemini"},
        }

        self.assertEqual(main.ominilink_video_headers(provider), {
            "Accept": "application/json",
            "Authorization": "Bearer test-key",
            "Content-Type": "application/json",
        })

    async def test_nested_completed_uses_opaque_id_and_saves_unique_uris_in_order(self):
        completed = FakeResponse(
            200,
            {
                "data": {
                    "id": "550e8400-e29b-41d4-a716-446655440000",
                    "status": " completed ",
                    "steps": [{"content": [
                        {
                            "type": "video",
                            "uri": "https://cdn.example.test/first.mp4",
                        },
                        {
                            "type": "video",
                            "uri": "https://cdn.example.test/first.mp4",
                        },
                        {
                            "type": "video",
                            "uri": "https://cdn.example.test/second.mp4",
                        },
                    ]}],
                },
            },
            "",
        )
        client = RecordingClient([completed])
        save = AsyncMock(side_effect=[
            "/assets/output/first.mp4",
            "/assets/output/second.mp4",
        ])

        with patch.object(main, "save_ominilink_video_to_output", new=save):
            result = await main.generate_ominilink_video(
                main.CanvasVideoRequest(
                    prompt="go",
                    model="gemini-omni-flash-preview",
                    duration=6,
                ),
                self.provider,
                client=client,
            )

        self.assertEqual(save.await_args_list, [
            call("https://cdn.example.test/first.mp4"),
            call("https://cdn.example.test/second.mp4"),
        ])
        self.assertEqual(result["videos"], [
            "/assets/output/first.mp4",
            "/assets/output/second.mp4",
        ])
        self.assertEqual(
            result["task_id"],
            "550e8400-e29b-41d4-a716-446655440000",
        )

    async def test_nested_queued_submission_and_nested_completed_poll(self):
        submitted = FakeResponse(
            200,
            {"data": {"task_id": "opaque-7f0a", "status": " queued "}},
            "",
        )
        completed = FakeResponse(
            200,
            {
                "data": {
                    "task_id": "opaque-7f0a",
                    "status": " completed ",
                    "steps": [{"content": [{
                        "type": "video",
                        "uri": "https://cdn.example.test/nested.mp4",
                    }]}],
                },
            },
            "",
        )
        client = RecordingClient([submitted], [completed])
        save = AsyncMock(return_value="/assets/output/nested.mp4")

        with patch.object(main.asyncio, "sleep", new=AsyncMock()), \
                patch.object(main, "save_ominilink_video_to_output", new=save):
            result = await main.generate_ominilink_video(
                main.CanvasVideoRequest(
                    prompt="go",
                    model="gemini-omni-flash-preview",
                    duration=6,
                ),
                self.provider,
                client=client,
            )

        self.assertEqual(client.calls[1][0:2], (
            "GET",
            "https://vg-api.aig-ai.com/v1/query/gemini-omni-flash-preview/opaque-7f0a",
        ))
        save.assert_awaited_once_with("https://cdn.example.test/nested.mp4")
        self.assertEqual(result["task_id"], "opaque-7f0a")
        self.assertEqual(result["videos"], ["/assets/output/nested.mp4"])

    async def test_omni_submit_and_get_poll(self):
        submitted = FakeResponse(
            200, {"id": "job-1", "status": "queued"}, '{"id":"job-1"}',
        )
        completed = FakeResponse(
            200,
            {
                "id": "job-1",
                "status": "completed",
                "steps": [{"content": [{
                    "type": "video",
                    "uri": "https://cdn.example.test/v.mp4",
                }]}],
            },
            '{"status":"completed"}',
        )
        client = RecordingClient([submitted], [completed])
        save = AsyncMock(return_value="/assets/output/v.mp4")

        with patch.object(main.asyncio, "sleep", new=AsyncMock()), \
                patch.object(main, "save_ominilink_video_to_output", new=save):
            result = await main.generate_ominilink_video(
                main.CanvasVideoRequest(
                    prompt="go",
                    provider_id="orange",
                    model="gemini-omni-flash-preview",
                    duration=6,
                ),
                self.provider,
                client=client,
            )

        self.assertEqual(client.calls[0], (
            "POST",
            "https://vg-api.aig-ai.com/v1/gemini-omni-flash-preview",
            {
                "headers": {
                    "Accept": "application/json",
                    "Authorization": "Bearer test-key",
                    "Content-Type": "application/json",
                },
                "json": {
                    "model": "gemini-omni-flash-preview",
                    "background": True,
                    "input": [{
                        "type": "user_input",
                        "content": [{"type": "text", "text": "go"}],
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
                },
            },
        ))
        self.assertEqual(client.calls[1], (
            "GET",
            "https://vg-api.aig-ai.com/v1/query/gemini-omni-flash-preview/job-1",
            {
                "headers": {
                    "Accept": "application/json",
                    "Authorization": "Bearer test-key",
                    "Content-Type": "application/json",
                },
            },
        ))
        save.assert_awaited_once_with("https://cdn.example.test/v.mp4")
        self.assertEqual(result, {
            "videos": ["/assets/output/v.mp4"],
            "task_id": "job-1",
            "model": "gemini-omni-flash-preview",
            "provider_id": "orange",
            "status": "COMPLETED",
        })

    async def test_gemini_model_override_still_uses_bearer_for_submit_and_poll(self):
        rejected = FakeResponse(200, {"id": "job-auth", "status": "failed"}, "")
        completed = FakeResponse(200, {
            "id": "job-auth",
            "status": "completed",
            "steps": [{"content": [{
                "type": "video", "uri": "https://cdn.example.test/auth.mp4",
            }]}],
        }, "")
        provider = {
            **self.provider,
            "protocol": "gemini",
            "model_protocols": {"gemini-omni-flash-preview": "gemini"},
        }
        submit_client = RecordingClient([rejected])
        poll_client = RecordingClient([], [completed])

        with self.assertRaises(main.HTTPException):
            await main.generate_ominilink_video(
                main.CanvasVideoRequest(prompt="go", model="gemini-omni-flash-preview"),
                provider,
                client=submit_client,
            )
        with patch.object(main.asyncio, "sleep", new=AsyncMock()):
            await main.wait_for_ominilink_video_task(
                poll_client, provider, "gemini-omni-flash-preview", "job-auth",
            )

        for _, _, kwargs in [*submit_client.calls, *poll_client.calls]:
            self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-key")
            self.assertNotIn("x-goog-api-key", kwargs["headers"])

    async def test_poll_retries_transient_timeout_then_completes(self):
        submitted = FakeResponse(200, {"id": "job-retry", "status": "queued"}, "")
        completed = FakeResponse(200, {
            "id": "job-retry",
            "status": "completed",
            "steps": [{"content": [{
                "type": "video", "uri": "https://cdn.example.test/retry.mp4",
            }]}],
        }, "")
        client = RecordingClient([], [main.httpx.TimeoutException("transient"), completed])

        with patch.object(main.asyncio, "sleep", new=AsyncMock()):
            result = await main.wait_for_ominilink_video_task(
                client, self.provider, "gemini-omni-flash-preview", "job-retry",
            )

        self.assertEqual(main._ominilink_task_status(result), "COMPLETED")
        self.assertEqual([method for method, _, _ in client.calls], ["GET", "GET"])

    async def test_repeated_poll_timeouts_end_at_the_existing_deadline(self):
        submitted = FakeResponse(200, {"id": "job-timeouts", "status": "queued"}, "")
        client = RecordingClient(
            [submitted],
            [main.httpx.TimeoutException("one"), main.httpx.ReadTimeout("two")],
        )
        clock = [0.0]

        async def advance_sleep(delay):
            clock[0] += delay

        with patch.object(main, "VIDEO_POLL_TIMEOUT", 10.0), \
                patch.object(main, "time", SimpleNamespace(monotonic=lambda: clock[0])), \
                patch.object(main.asyncio, "sleep", new=advance_sleep):
            with self.assertRaises(main.HTTPException) as raised:
                await main.generate_ominilink_video(
                    main.CanvasVideoRequest(prompt="go", model="gemini-omni-flash-preview"),
                    self.provider,
                    client=client,
                )

        self.assertEqual(raised.exception.status_code, 504)
        self.assertEqual([method for method, _, _ in client.calls], ["POST", "GET", "GET"])

    async def test_poll_rate_limit_stops_without_retrying(self):
        submitted = FakeResponse(200, {"id": "job-rate-limit", "status": "queued"}, "")
        rate_limited = FakeResponse(429, text="private token=do-not-echo")
        client = RecordingClient([submitted], [rate_limited])

        with patch.object(main.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaises(main.HTTPException) as raised:
                await main.generate_ominilink_video(
                    main.CanvasVideoRequest(prompt="go", model="gemini-omni-flash-preview"),
                    self.provider,
                    client=client,
                )

        self.assertEqual(raised.exception.status_code, 429)
        self.assertNotIn("private token", raised.exception.detail)
        self.assertEqual([method for method, _, _ in client.calls], ["POST", "GET"])

    def test_non_json_http_errors_map_without_echoing_response_body(self):
        for status_code in (401, 403, 404, 405, 429):
            with self.subTest(status_code=status_code):
                response = FakeResponse(
                    status_code,
                    text="private prompt and https://signed.example.test/?token=secret",
                )
                with self.assertRaises(main.HTTPException) as raised:
                    main._ominilink_response_json(response, "job-safe")

                self.assertEqual(raised.exception.status_code, status_code)
                self.assertIn(f"HTTP {status_code}", raised.exception.detail)
                self.assertNotIn("private prompt", raised.exception.detail)
                self.assertNotIn("signed.example.test", raised.exception.detail)

    def test_malformed_success_response_remains_bad_gateway(self):
        with self.assertRaises(main.HTTPException) as raised:
            main._ominilink_response_json(FakeResponse(200, text="not-json"), "job-safe")

        self.assertEqual(raised.exception.status_code, 502)

    async def test_immediate_completed_response_skips_poll(self):
        completed = FakeResponse(
            200,
            {
                "id": "job-now",
                "status": "completed",
                "steps": [{"content": [{
                    "type": "video",
                    "uri": "https://cdn.example.test/now.mp4",
                }]}],
            },
            '{"id":"job-now","status":"completed"}',
        )
        client = RecordingClient([completed])
        save = AsyncMock(return_value="/assets/output/now.mp4")

        with patch.object(main, "save_ominilink_video_to_output", new=save):
            result = await main.generate_ominilink_video(
                main.CanvasVideoRequest(
                    prompt="go",
                    model="gemini-omni-flash-preview",
                    duration=6,
                ),
                self.provider,
                client=client,
            )

        self.assertEqual(
            [(method, url) for method, url, _ in client.calls],
            [("POST", "https://vg-api.aig-ai.com/v1/gemini-omni-flash-preview")],
        )
        save.assert_awaited_once_with("https://cdn.example.test/now.mp4")
        self.assertEqual(result["videos"], ["/assets/output/now.mp4"])
        self.assertEqual(result["task_id"], "job-now")

    async def test_success_response_serializes_only_local_media_and_safe_metadata(self):
        signed_uri = (
            "https://signed-upstream.example.test/private/result.mp4"
            "?token=private-success-token&signature=opaque"
        )
        completed = FakeResponse(
            200,
            {
                "id": "job-redacted-success",
                "status": "completed",
                "steps": [{"content": [{
                    "type": "video",
                    "uri": signed_uri,
                }]}],
                "prompt": "private prompt must not leak",
                "internal": {"trace": "private-success-trace"},
            },
            '{"id":"job-redacted-success","status":"completed"}',
        )
        client = RecordingClient([completed])
        save = AsyncMock(return_value="/assets/output/safe-result.mp4")

        with patch.object(main, "save_ominilink_video_to_output", new=save):
            result = await main.generate_ominilink_video(
                main.CanvasVideoRequest(
                    prompt="go",
                    model="gemini-omni-flash-preview",
                    duration=6,
                ),
                self.provider,
                client=client,
            )

        self.assertEqual(result, {
            "videos": ["/assets/output/safe-result.mp4"],
            "task_id": "job-redacted-success",
            "model": "gemini-omni-flash-preview",
            "provider_id": "orange",
            "status": "COMPLETED",
        })
        serialized = json.dumps(result, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("raw", result)
        self.assertNotIn("signed-upstream.example.test", serialized)
        self.assertNotIn("private-success-token", serialized)
        self.assertNotIn("signature=opaque", serialized)
        self.assertNotIn(signed_uri, serialized)
        self.assertNotIn("private prompt must not leak", serialized)
        self.assertNotIn("private-success-trace", serialized)

    async def test_completed_without_video_uri_is_terminal_error(self):
        completed = FakeResponse(
            200,
            {
                "id": "job-empty",
                "status": "completed",
                "steps": [],
                "prompt": "private prompt must not leak",
                "signed_url": "https://signed.example.test/private?token=secret",
                "internal": {"trace": "private-trace"},
            },
            '{"id":"job-empty","status":"completed"}',
        )
        client = RecordingClient([completed])

        with self.assertRaises(main.HTTPException) as raised:
            await main.generate_ominilink_video(
                main.CanvasVideoRequest(
                    prompt="go",
                    model="gemini-omni-flash-preview",
                    duration=6,
                ),
                self.provider,
                client=client,
            )

        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(
            raised.exception.detail,
            "OminiLink 视频任务已完成但没有返回视频 URI"
            "（任务 ID：job-empty，状态：COMPLETED）",
        )
        self.assertNotIn("private prompt", raised.exception.detail)
        self.assertNotIn("signed.example.test", raised.exception.detail)
        self.assertNotIn("private-trace", raised.exception.detail)
        self.assertEqual(len(client.calls), 1)

    async def test_non_omni_model_uses_basic_body_and_post_query(self):
        submitted = FakeResponse(
            200, {"id": "job-2", "status": "queued"}, '{"id":"job-2"}',
        )
        completed = FakeResponse(
            200,
            {
                "id": "job-2",
                "status": "completed",
                "output_url": "https://cdn.example.test/basic.mp4",
            },
            '{"status":"completed"}',
        )
        client = RecordingClient([submitted, completed])
        save = AsyncMock(return_value="/assets/output/basic.mp4")

        with patch.object(main.asyncio, "sleep", new=AsyncMock()), \
                patch.object(main, "save_ominilink_video_to_output", new=save):
            result = await main.generate_ominilink_video(
                main.CanvasVideoRequest(
                    prompt="go", model="seedance-2.0", duration=6,
                ),
                self.provider,
                client=client,
            )

        self.assertEqual(client.calls[0], (
            "POST",
            "https://vg-api.aig-ai.com/v1/seedance-2.0",
            {
                "headers": {
                    "Accept": "application/json",
                    "Authorization": "Bearer test-key",
                    "Content-Type": "application/json",
                },
                "json": {
                    "prompt": "go",
                    "size": "1280x720",
                    "seconds": "6",
                },
            },
        ))
        self.assertEqual(client.calls[1], (
            "POST",
            "https://vg-api.aig-ai.com/v1/query/seedance-2.0/job-2",
            {
                "headers": {
                    "Accept": "application/json",
                    "Authorization": "Bearer test-key",
                    "Content-Type": "application/json",
                },
                "json": {},
            },
        ))
        save.assert_awaited_once_with("https://cdn.example.test/basic.mp4")
        self.assertEqual(result["videos"], ["/assets/output/basic.mp4"])
        self.assertEqual(result["task_id"], "job-2")

    async def test_model_and_nested_task_id_are_url_encoded(self):
        submitted = FakeResponse(
            200,
            {"data": {"id": "job/one ?", "status": "queued"}},
            "",
        )
        completed = FakeResponse(
            200,
            {
                "data": {
                    "id": "job/one ?",
                    "status": "completed",
                    "output_url": "https://cdn.example.test/encoded.mp4",
                },
            },
            "",
        )
        client = RecordingClient([submitted, completed])
        save = AsyncMock(return_value="/assets/output/encoded.mp4")

        with patch.object(main.asyncio, "sleep", new=AsyncMock()), \
                patch.object(main, "save_ominilink_video_to_output", new=save):
            result = await main.generate_ominilink_video(
                main.CanvasVideoRequest(
                    prompt="go", model="seedance/model 2", duration=6,
                ),
                self.provider,
                client=client,
            )

        self.assertEqual(
            [(method, url) for method, url, _ in client.calls],
            [
                (
                    "POST",
                    "https://vg-api.aig-ai.com/v1/seedance%2Fmodel%202",
                ),
                (
                    "POST",
                    "https://vg-api.aig-ai.com/v1/query/seedance%2Fmodel%202/job%2Fone%20%3F",
                ),
            ],
        )
        self.assertEqual(result["task_id"], "job/one ?")

    async def test_non_omni_models_reject_all_reference_media_before_submit(self):
        payloads = [
            main.CanvasVideoRequest(
                prompt="go",
                model="seedance-2.0",
                images=[main.AIReference(url="data:image/png;base64,YQ==")],
            ),
            main.CanvasVideoRequest(
                prompt="go",
                model="seedance-2.0",
                videos=["data:video/mp4;base64,YQ=="],
            ),
            main.CanvasVideoRequest(
                prompt="go",
                model="seedance-2.0",
                audios=["data:audio/mpeg;base64,YQ=="],
            ),
        ]

        for payload in payloads:
            with self.subTest(payload=payload):
                client = RecordingClient([])
                with self.assertRaises(main.HTTPException) as raised:
                    await main.generate_ominilink_video(
                        payload, self.provider, client=client,
                    )
                self.assertEqual(raised.exception.status_code, 400)
                self.assertIn("参考素材", raised.exception.detail)
                self.assertEqual(client.calls, [])

    async def test_arbitrary_failure_reason_is_not_echoed(self):
        submitted = FakeResponse(
            200, {"id": "job-failed", "status": "queued"}, "",
        )
        failed = FakeResponse(
            200,
            {
                "id": "job-failed",
                "status": "failed",
                "message": "renderer exploded",
            },
            "",
        )
        client = RecordingClient([submitted], [failed])

        with patch.object(main.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaises(main.HTTPException) as raised:
                await main.generate_ominilink_video(
                    main.CanvasVideoRequest(
                        prompt="go",
                        model="gemini-omni-flash-preview",
                        duration=6,
                    ),
                    self.provider,
                    client=client,
                )

        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(
            raised.exception.detail,
            "OminiLink 视频任务失败"
            "（任务 ID：job-failed，状态：FAILED）",
        )
        self.assertNotIn("renderer exploded", raised.exception.detail)

    async def test_failure_without_reason_exposes_only_safe_task_context(self):
        failed = FakeResponse(
            200,
            {
                "data": {
                    "id": "job-private",
                    "status": " failed ",
                    "prompt": "private prompt must not leak",
                    "signed_url": "https://signed.example.test/private?token=secret",
                    "internal_fields": {"trace": "private-trace"},
                },
            },
            "",
        )
        client = RecordingClient([failed])

        with self.assertRaises(main.HTTPException) as raised:
            await main.generate_ominilink_video(
                main.CanvasVideoRequest(
                    prompt="go",
                    model="gemini-omni-flash-preview",
                    duration=6,
                ),
                self.provider,
                client=client,
            )

        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(
            raised.exception.detail,
            "OminiLink 视频任务失败"
            "（任务 ID：job-private，状态：FAILED）",
        )
        self.assertNotIn("private prompt", raised.exception.detail)
        self.assertNotIn("signed.example.test", raised.exception.detail)
        self.assertNotIn("private-trace", raised.exception.detail)

    async def test_sensitive_explicit_failure_reason_is_redacted(self):
        failed = FakeResponse(
            200,
            {
                "data": {
                    "id": "job-redacted",
                    "status": "failed",
                    "message": (
                        "prompt=private prompt; "
                        "signed_url=https://signed.example.test/private?token=secret"
                    ),
                },
            },
            "",
        )
        client = RecordingClient([failed])

        with self.assertRaises(main.HTTPException) as raised:
            await main.generate_ominilink_video(
                main.CanvasVideoRequest(
                    prompt="go",
                    model="gemini-omni-flash-preview",
                    duration=6,
                ),
                self.provider,
                client=client,
            )

        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(
            raised.exception.detail,
            "OminiLink 视频任务失败"
            "（任务 ID：job-redacted，状态：FAILED）",
        )
        self.assertNotIn("private prompt", raised.exception.detail)
        self.assertNotIn("signed.example.test", raised.exception.detail)
        self.assertNotIn("token", raised.exception.detail)

    async def test_adversarial_failure_reasons_are_never_echoed(self):
        reasons = [
            "internal_fields=trace-private",
            "promptText=private-prompt",
            "authorizationToken=Bearer-private-token",
            "api key=sk-private",
            "signedUrl=https://signed.example.test/private",
            "sig=bare-signature-value",
            "query=authorizationToken%3DBearer-private-token",
            "SAFETY internal_fields=trace-private",
        ]
        expected = (
            "OminiLink 视频任务失败"
            "（任务 ID：job-adversarial，状态：FAILED）"
        )

        for reason in reasons:
            with self.subTest(reason=reason):
                failed = FakeResponse(
                    200,
                    {
                        "data": {
                            "id": "job-adversarial",
                            "status": "failed",
                            "message": reason,
                        },
                    },
                    "",
                )
                client = RecordingClient([failed])

                with self.assertRaises(main.HTTPException) as raised:
                    await main.generate_ominilink_video(
                        main.CanvasVideoRequest(
                            prompt="go",
                            model="gemini-omni-flash-preview",
                            duration=6,
                        ),
                        self.provider,
                        client=client,
                    )

                self.assertEqual(raised.exception.status_code, 502)
                self.assertEqual(raised.exception.detail, expected)
                self.assertNotIn(reason, raised.exception.detail)

    async def test_network_error_does_not_echo_signed_url(self):
        exc = main.httpx.ConnectError(
            "connect failed: https://signed.example.test/private?token=secret",
            request=main.httpx.Request(
                "POST",
                "https://vg-api.aig-ai.com/v1/gemini-omni-flash-preview",
            ),
        )
        client = ExceptionRecordingClient(exc)

        with self.assertRaises(main.HTTPException) as raised:
            await main.generate_ominilink_video(
                main.CanvasVideoRequest(
                    prompt="go",
                    model="gemini-omni-flash-preview",
                    duration=6,
                ),
                self.provider,
                client=client,
            )

        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(
            raised.exception.detail,
            "请求 OminiLink 视频接口失败，请稍后重试。",
        )
        self.assertNotIn("signed.example.test", raised.exception.detail)
        self.assertNotIn("token", raised.exception.detail)

    async def test_safety_failure_uses_humanized_message(self):
        submitted = FakeResponse(
            200, {"id": "job-safe", "status": "queued"}, "",
        )
        failed = FakeResponse(
            200,
            {"id": "job-safe", "status": "failed", "message": "SAFETY"},
            "",
        )
        client = RecordingClient([submitted], [failed])

        with patch.object(main.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaises(main.HTTPException) as raised:
                await main.generate_ominilink_video(
                    main.CanvasVideoRequest(
                        prompt="go",
                        model="gemini-omni-flash-preview",
                        duration=6,
                    ),
                    self.provider,
                    client=client,
                )

        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(
            raised.exception.detail,
            "视频生成被上游内容安全策略拦截（错误码：SAFETY）。\n\n"
            "这是 veo 的内容审核规则，提示词或参考图触发了安全过滤。\n"
            "请调整提示词/参考图后重试，避免涉及真人、暴力、敏感或受限内容。",
        )

    async def test_poll_timeout_raises_504(self):
        submitted = FakeResponse(
            200, {"id": "job-slow", "status": "queued"}, "",
        )
        client = RecordingClient([submitted])
        clock = [0.0]

        async def advance_sleep(delay):
            clock[0] += delay

        fake_time = SimpleNamespace(monotonic=lambda: clock[0])

        with patch.object(main, "VIDEO_POLL_TIMEOUT", 1.0), \
                patch.object(main, "time", fake_time), \
                patch.object(main.asyncio, "sleep", new=advance_sleep):
            with self.assertRaises(main.HTTPException) as raised:
                await main.generate_ominilink_video(
                    main.CanvasVideoRequest(
                        prompt="go",
                        model="gemini-omni-flash-preview",
                        duration=6,
                    ),
                    self.provider,
                    client=client,
                )

        self.assertEqual(raised.exception.status_code, 504)
        self.assertEqual(
            raised.exception.detail,
            "OminiLink 视频生成任务超时"
            "（任务 ID：job-slow，状态：UNKNOWN）",
        )
        self.assertEqual(
            [(method, url) for method, url, _ in client.calls],
            [("POST", "https://vg-api.aig-ai.com/v1/gemini-omni-flash-preview")],
        )

    async def test_timeout_after_queued_poll_does_not_echo_payload(self):
        submitted = FakeResponse(
            200, {"id": "job-sensitive", "status": "queued"}, "",
        )
        queued = FakeResponse(
            200,
            {
                "data": {
                    "id": "job-sensitive",
                    "status": " queued ",
                    "prompt": "private prompt must not leak",
                    "signed_url": "https://signed.example.test/private?token=secret",
                    "internal": {"trace": "private-trace"},
                },
            },
            "",
        )
        client = RecordingClient([submitted], [queued])
        clock = [0.0]

        async def advance_sleep(delay):
            clock[0] += delay

        fake_time = SimpleNamespace(monotonic=lambda: clock[0])

        with patch.object(main, "VIDEO_POLL_TIMEOUT", 3.0), \
                patch.object(main, "time", fake_time), \
                patch.object(main.asyncio, "sleep", new=advance_sleep):
            with self.assertRaises(main.HTTPException) as raised:
                await main.generate_ominilink_video(
                    main.CanvasVideoRequest(
                        prompt="go",
                        model="gemini-omni-flash-preview",
                        duration=6,
                    ),
                    self.provider,
                    client=client,
                )

        self.assertEqual(raised.exception.status_code, 504)
        self.assertEqual(
            raised.exception.detail,
            "OminiLink 视频生成任务超时"
            "（任务 ID：job-sensitive，状态：QUEUED）",
        )
        self.assertNotIn("private prompt", raised.exception.detail)
        self.assertNotIn("signed.example.test", raised.exception.detail)
        self.assertNotIn("private-trace", raised.exception.detail)
        self.assertEqual(len(client.calls), 2)

    async def test_hung_poll_request_is_bounded_by_total_deadline(self):
        submitted = FakeResponse(
            200, {"id": "job-hung", "status": "queued"}, "",
        )
        client = HungRecordingClient([submitted])

        async def invoke():
            return await main.generate_ominilink_video(
                main.CanvasVideoRequest(
                    prompt="go",
                    model="gemini-omni-flash-preview",
                    duration=6,
                ),
                self.provider,
                client=client,
            )

        with patch.object(main, "VIDEO_POLL_TIMEOUT", 0.02), \
                patch.object(main.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaises(main.HTTPException) as raised:
                await asyncio.wait_for(invoke(), timeout=0.5)

        self.assertEqual(raised.exception.status_code, 504)
        self.assertEqual(
            raised.exception.detail,
            "OminiLink 视频生成任务超时"
            "（任务 ID：job-hung，状态：UNKNOWN）",
        )
        self.assertEqual(client.calls[1][0], "GET")
        self.assertTrue(client.cancelled)

    async def test_polling_does_not_require_python_311_asyncio_timeout(self):
        submitted = FakeResponse(
            200, {"id": "job-py310", "status": "queued"}, "",
        )
        completed = FakeResponse(
            200,
            {
                "data": {
                    "id": "job-py310",
                    "status": "completed",
                    "steps": [{"content": [{
                        "type": "video",
                        "uri": "https://cdn.example.test/py310.mp4",
                    }]}],
                },
            },
            "",
        )
        client = RecordingClient([submitted], [completed])
        save = AsyncMock(return_value="/assets/output/py310.mp4")

        with patch.object(main.asyncio, "timeout", new=None, create=True), \
                patch.object(main.asyncio, "sleep", new=AsyncMock()), \
                patch.object(main, "save_ominilink_video_to_output", new=save):
            result = await asyncio.wait_for(
                main.generate_ominilink_video(
                    main.CanvasVideoRequest(
                        prompt="go",
                        model="gemini-omni-flash-preview",
                        duration=6,
                    ),
                    self.provider,
                    client=client,
                ),
                timeout=0.5,
            )

        self.assertEqual(result["videos"], ["/assets/output/py310.mp4"])
        self.assertEqual(result["task_id"], "job-py310")

    async def test_missing_task_id_error_does_not_echo_payload(self):
        submitted = FakeResponse(
            200,
            {
                "status": " queued ",
                "prompt": "private prompt must not leak",
                "signed_url": "https://signed.example.test/private?token=secret",
                "internal": {"trace": "private-trace"},
            },
            "",
        )
        client = RecordingClient([submitted])

        with self.assertRaises(main.HTTPException) as raised:
            await main.generate_ominilink_video(
                main.CanvasVideoRequest(
                    prompt="go",
                    model="gemini-omni-flash-preview",
                    duration=6,
                ),
                self.provider,
                client=client,
            )

        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(
            raised.exception.detail,
            "OminiLink 视频接口未返回任务 ID 或视频 URI"
            "（状态：QUEUED）",
        )
        self.assertNotIn("private prompt", raised.exception.detail)
        self.assertNotIn("signed.example.test", raised.exception.detail)
        self.assertNotIn("private-trace", raised.exception.detail)

    async def test_list_json_error_does_not_echo_payload(self):
        submitted = FakeResponse(
            200,
            [
                "private prompt must not leak",
                "https://signed.example.test/private?token=secret",
            ],
            "",
        )
        client = RecordingClient([submitted])

        with self.assertRaises(main.HTTPException) as raised:
            await main.generate_ominilink_video(
                main.CanvasVideoRequest(
                    prompt="go",
                    model="gemini-omni-flash-preview",
                    duration=6,
                ),
                self.provider,
                client=client,
            )

        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(
            raised.exception.detail,
            "OminiLink 视频接口返回了无效 JSON 结构",
        )
        self.assertNotIn("private prompt", raised.exception.detail)
        self.assertNotIn("signed.example.test", raised.exception.detail)

    async def test_html_submit_response_reports_non_json(self):
        html = FakeResponse(
            200,
            None,
            "<!doctype html><html><head><title>Portal</title></head></html>",
        )
        client = RecordingClient([html])

        with self.assertRaises(main.HTTPException) as raised:
            await main.generate_ominilink_video(
                main.CanvasVideoRequest(
                    prompt="go",
                    model="gemini-omni-flash-preview",
                    duration=6,
                ),
                self.provider,
                client=client,
            )

        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(
            raised.exception.detail,
            "OminiLink 视频接口返回网页而不是 JSON（HTTP 200）",
        )
        self.assertNotIn("Portal", raised.exception.detail)
        self.assertEqual(len(client.calls), 1)

    async def test_owned_client_is_closed_after_request(self):
        completed = FakeResponse(
            200,
            {
                "id": "job-owned",
                "status": "completed",
                "steps": [{"content": [{
                    "type": "video",
                    "uri": "https://cdn.example.test/owned.mp4",
                }]}],
            },
            "",
        )
        client = ContextRecordingClient([completed])
        save = AsyncMock(return_value="/assets/output/owned.mp4")

        with patch.object(main, "VIDEO_POLL_TIMEOUT", 123.0), \
                patch.object(main.httpx, "AsyncClient", return_value=client) as factory, \
                patch.object(main, "save_ominilink_video_to_output", new=save):
            result = await main.generate_ominilink_video(
                main.CanvasVideoRequest(
                    prompt="go",
                    model="gemini-omni-flash-preview",
                    duration=6,
                ),
                self.provider,
            )

        factory.assert_called_once_with(timeout=123.0)
        self.assertTrue(client.entered)
        self.assertTrue(client.exited)
        self.assertEqual(result["videos"], ["/assets/output/owned.mp4"])

    async def test_canvas_video_routes_ominilink_before_generic_transport(self):
        payload = main.CanvasVideoRequest(
            prompt="go",
            provider_id="orange",
            model="gemini-omni-flash-preview",
            duration=6,
        )
        generated = AsyncMock(return_value={
            "videos": ["/assets/output/routed.mp4"],
            "task_id": "job-route",
            "raw": {"id": "job-route", "status": "completed"},
        })

        with patch.object(main, "get_api_provider", return_value=self.provider), \
                patch.object(main, "generate_ominilink_video", new=generated):
            result = await main.canvas_video(payload)

        generated.assert_awaited_once_with(payload, self.provider)
        self.assertEqual(result["videos"], ["/assets/output/routed.mp4"])

    async def test_canvas_video_does_not_route_ominilink_lookalike_host(self):
        payload = main.CanvasVideoRequest(
            prompt="go",
            provider_id="lookalike",
            model="gemini-omni-flash-preview",
            duration=6,
        )
        provider = {
            "id": "lookalike",
            "name": "Lookalike",
            "base_url": "https://api.aig-ai.com.evil.test/v1",
            "video_base_url": "https://vg-api.aig-ai.com.evil.test/v1",
        }
        generated = AsyncMock()

        with patch.object(main, "get_api_provider", return_value=provider), \
                patch.object(main, "video_api_root", return_value=""), \
                patch.object(main, "generate_ominilink_video", new=generated):
            with self.assertRaises(main.HTTPException) as raised:
                await main.canvas_video(payload)

        self.assertEqual(raised.exception.status_code, 400)
        generated.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
