import asyncio
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import main


class VideoDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_runninghub_videos_use_accelerated_downloader_not_buffered_get(self):
        client = AsyncMock()
        with patch.object(main, "save_remote_video_to_output", new=AsyncMock(return_value="/assets/output/rh.mp4")) as save:
            result = await main.runninghub_store_remote_output(client, "https://example.test/result.mp4?token=opaque")
        self.assertEqual(result, "/assets/output/rh.mp4")
        save.assert_awaited_once()
        client.get.assert_not_awaited()

    async def test_parallel_strict_download_revalidates_range_redirects(self):
        calls = []

        def handler(request):
            calls.append(request)
            if request.headers.get("range"):
                return httpx.Response(302, headers={"Location": "https://127.0.0.1/private.mp4"})
            return httpx.Response(200, headers={
                "Content-Type": "video/mp4", "Content-Length": "16384", "Accept-Ranges": "bytes", "ETag": '"same"',
            }, content=b"x" * 16384)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with tempfile.TemporaryDirectory() as folder, patch.object(
                main, "output_path_for", side_effect=lambda filename, category: str(Path(folder) / filename)
            ), patch.object(main.video_download, "PARALLEL_THRESHOLD", 1024):
                with self.assertRaises(main.HTTPException):
                    await main.save_ominilink_video_to_output(
                        "https://cdn.example.test/result.mp4", client=client, resolver=lambda host: ["93.184.216.34"],
                    )
                self.assertEqual(list(Path(folder).iterdir()), [])
        self.assertTrue(any(r.headers.get("range") for r in calls))
        self.assertTrue(all(r.url.host == "93.184.216.34" for r in calls))
        self.assertTrue(all("authorization" not in r.headers for r in calls))

    async def test_download_progress_reports_size_and_speed_without_signed_urls(self):
        task_id = "delivery-progress-test"
        main.CANVAS_TASKS[task_id] = {"status": "running"}
        progress = {"task_id": task_id, "started": main.time.monotonic()}
        token = main.VIDEO_TASK_PROGRESS.set(progress)
        try:
            report = main.video_transfer_progress()
            report(2048, 8192, "parallel")
            task = main.CANVAS_TASKS[task_id]
            self.assertEqual(task["downloaded_bytes"], 2048)
            self.assertEqual(task["download_total_bytes"], 8192)
            self.assertEqual(task["download_mode"], "parallel")
            self.assertGreater(task["download_speed_bps"], 0)
            report(0, 8192, "single")  # Restart after server rejects ranges.
            self.assertEqual(progress["downloaded_bytes"], 0)
        finally:
            main.VIDEO_TASK_PROGRESS.reset(token)
            main.CANVAS_TASKS.pop(task_id, None)

    async def test_downloads_are_bounded_deduplicated_and_keep_order(self):
        active = peak = 0
        calls = []

        async def save(url):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            calls.append(url)
            await asyncio.sleep(0.005)
            active -= 1
            return f"/output/{url}"

        urls = [str(i) for i in range(8)] + ["0"]
        result = await main.save_video_outputs(urls, saver=save)
        self.assertEqual(result, [f"/output/{url}" for url in urls])
        self.assertEqual(peak, 3)
        self.assertEqual(len(calls), 8)

    async def test_failed_download_waits_for_other_workers(self):
        finished = []

        async def save(url):
            if url == "fail":
                raise main.HTTPException(502, "download failed")
            await asyncio.sleep(0.01)
            finished.append(url)
            return url

        with self.assertRaises(main.HTTPException):
            await main.save_video_outputs(["fail", "one", "two"], saver=save)
        self.assertEqual(finished, ["one", "two"])

    async def test_tasks_report_separate_download_timing_and_isolate_progress(self):
        ids = ["delivery-test-a", "delivery-test-b"]
        for task_id in ids:
            main.CANVAS_TASKS[task_id] = {"status": "queued"}

        async def generate(payload):
            task_id = payload.prompt
            self.assertEqual(main.CANVAS_TASKS[task_id]["phase"], "upstream")
            await asyncio.sleep(0.01)
            main.report_video_download(123 if task_id == ids[0] else 456)
            self.assertEqual(main.CANVAS_TASKS[task_id]["phase"], "downloading")
            await asyncio.sleep(0.01)
            return {"videos": ["/assets/output/test.mp4"]}

        try:
            with patch.object(main, "canvas_video", side_effect=generate):
                await asyncio.gather(*(main.run_canvas_video_task(task_id, main.CanvasVideoRequest(prompt=task_id)) for task_id in ids))
            for task_id, count in zip(ids, [123, 456]):
                task = main.CANVAS_TASKS[task_id]
                self.assertEqual(task["status"], "succeeded")
                self.assertEqual(task["phase"], "succeeded")
                self.assertEqual(task["downloaded_bytes"], count)
                timing = task["timings"]
                self.assertGreater(timing["upstream_ms"], 0)
                self.assertGreater(timing["download_ms"], 0)
                self.assertAlmostEqual(timing["total_ms"], timing["upstream_ms"] + timing["download_ms"], delta=1)
            self.assertIsNone(main.VIDEO_TASK_PROGRESS.get())
        finally:
            for task_id in ids:
                main.CANVAS_TASKS.pop(task_id, None)

    async def test_poll_remembers_working_route(self):
        seen = []
        urls = ["https://example.test/wrong", "https://example.test/correct"]

        async def get(url, **kwargs):
            seen.append(url)
            if url == urls[0]:
                return httpx.Response(404, request=httpx.Request("GET", url))
            return httpx.Response(200, request=httpx.Request("GET", url), json={
                "status": "completed" if len(seen) == 3 else "running",
            })

        client = AsyncMock()
        client.get.side_effect = get
        with patch.object(main, "video_task_url_candidates", return_value=urls), patch.object(
            main, "api_headers", return_value={}
        ), patch.object(main.asyncio, "sleep", new=AsyncMock()):
            result = await main.wait_for_video_task(client, {"id": "test", "base_url": "https://example.test/v1"}, "task")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(seen, [urls[0], urls[1], urls[1]])

    async def test_cancelled_stream_removes_partial_video(self):
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"x" * (1024 * 1024)
                raise asyncio.CancelledError()

        transport = httpx.MockTransport(lambda request: httpx.Response(200, headers={"Content-Type": "video/mp4"}, stream=Stream()))
        original_client = httpx.AsyncClient
        with tempfile.TemporaryDirectory() as folder, patch.object(
            main, "output_path_for", side_effect=lambda filename, category: str(Path(folder) / filename)
        ), patch.object(
            main, "video_download_client_options", return_value={"trust_env": False}
        ), patch.object(main.httpx, "AsyncClient", side_effect=lambda **kwargs: original_client(transport=transport, **kwargs)):
            with self.assertRaises(asyncio.CancelledError):
                await main.save_remote_video_to_output("https://example.test/video.mp4")
            self.assertEqual(list(Path(folder).iterdir()), [])


class ExportFastPathTests(unittest.TestCase):
    def test_container_check_does_not_trust_extension(self):
        with tempfile.TemporaryDirectory() as folder:
            bad = Path(folder) / "error.mp4"
            bad.write_text("<html>bad response</html>", encoding="utf-8")
            self.assertFalse(main.canvas_video_matches_container(str(bad), "mp4"))

    def test_same_format_is_byte_identical_but_other_format_is_converted(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.mp4"
            copied = Path(folder) / "copied.mp4"
            converted = Path(folder) / "converted.mov"
            subprocess.run([
                shutil.which("ffmpeg"), "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=24:d=0.25",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=0.25", "-c:v", "libx264", "-c:a", "aac", str(source),
            ], check=True)
            self.assertEqual(main.export_canvas_output_video(str(source), str(copied), "mp4"), "copy")
            self.assertEqual(source.read_bytes(), copied.read_bytes())
            self.assertEqual(main.export_canvas_output_video(str(source), str(converted), "mov"), "transcode")
            result = subprocess.run([
                shutil.which("ffprobe"), "-v", "error", "-show_streams", "-of", "json", str(converted),
            ], check=True, capture_output=True, text=True)
            streams = json.loads(result.stdout)["streams"]
            video = next(s for s in streams if s["codec_type"] == "video")
            self.assertEqual((video["width"], video["height"], video["r_frame_rate"]), (320, 180, "24/1"))
            self.assertTrue(any(s["codec_type"] == "audio" for s in streams))


if __name__ == "__main__":
    unittest.main()
