import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import main


class GenerationTimingTests(unittest.IsolatedAsyncioTestCase):
    async def run_timed(self, generate):
        task_id = "stage-timing-fixture"
        clock = [10.0]
        fake_time = SimpleNamespace(monotonic=lambda: clock[0], time=lambda: 1000 + clock[0])
        main.CANVAS_TASKS[task_id] = {"status": "queued"}

        async def execute(_):
            return await generate(clock)

        try:
            with patch.object(main, "time", fake_time), patch.object(main, "canvas_video", side_effect=execute):
                await main.run_canvas_video_task(task_id, main.CanvasVideoRequest(prompt="fixture"))
            return main.CANVAS_TASKS[task_id].copy()
        finally:
            main.CANVAS_TASKS.pop(task_id, None)
            self.assertIsNone(main.VIDEO_TASK_PROGRESS.get())

    async def test_exclusive_stages_sum_to_total_without_parallel_double_counting(self):
        async def generate(clock):
            clock[0] += 2
            main.mark_video_timing_stage("submit")
            clock[0] += 1
            main.mark_video_timing_stage("generate")
            clock[0] += 6
            main.report_video_download(100)
            clock[0] += 1
            main.report_video_download(200)  # Another concurrent transfer must not restart the phase.
            clock[0] += 2
            return {"videos": ["/output/fixture.mp4"]}

        task = await self.run_timed(generate)
        self.assertEqual(task["timings"]["stages_ms"], {"prepare": 2000, "submit": 1000, "generate": 6000, "download": 3000})
        self.assertEqual(task["timings"]["total_ms"], 12000)
        self.assertEqual(task["timings"]["upstream_ms"], 9000)
        self.assertEqual(task["downloaded_bytes"], 300)

    async def test_failure_keeps_only_reached_phases(self):
        async def generate(clock):
            clock[0] += 2
            main.mark_video_timing_stage("submit")
            clock[0] += 1
            main.mark_video_timing_stage("generate")
            clock[0] += 6
            raise main.HTTPException(502, "fixture generation failed")

        task = await self.run_timed(generate)
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["timings"]["stages_ms"], {"prepare": 2000, "submit": 1000, "generate": 6000})
        self.assertEqual(task["timings"]["total_ms"], 9000)
        self.assertNotIn("download", task["timings"]["stages_ms"])

    async def test_synchronous_endpoint_does_not_claim_separate_generation_time(self):
        async def generate(clock):
            clock[0] += 1
            main.mark_video_timing_stage("submit")
            clock[0] += 20
            main.report_video_download()
            clock[0] += 3
            return {"videos": ["/output/fixture.mp4"]}

        task = await self.run_timed(generate)
        self.assertEqual(task["timings"]["stages_ms"], {"prepare": 1000, "submit": 20000, "download": 3000})
        self.assertEqual(sum(task["timings"]["stages_ms"].values()), task["timings"]["total_ms"])

    async def test_runninghub_query_reports_only_its_result_transfer_time(self):
        clock = [0.0]
        client = AsyncMock()
        client.post.return_value = httpx.Response(200, json={"code": 0, "data": []})
        client.__aenter__.return_value = client

        async def save(_, remote):
            clock[0] += 1.25
            return "/output/" + remote

        with patch.object(main, "runninghub_provider", return_value={}), patch.object(
            main, "runninghub_api_key", return_value="fixture-key"
        ), patch.object(main, "runninghub_endpoint_url", return_value="https://example.test/query"), patch.object(
            main, "runninghub_app_headers", return_value={}
        ), patch.object(main.httpx, "AsyncClient", return_value=client), patch.object(
            main, "runninghub_extract_outputs", return_value=["a.mp4", "b.mp4"]
        ), patch.object(main, "runninghub_store_remote_output", side_effect=save), patch.object(
            main, "time", SimpleNamespace(monotonic=lambda: clock[0])
        ):
            response = await main.runninghub_query("fixture")
        self.assertEqual(response["data"]["timings"], {"download_ms": 2500})
        self.assertEqual(response["data"]["status"], "SUCCESS")


if __name__ == "__main__":
    unittest.main()
