import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main


class CanvasVideoTaskTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        with main.CANVAS_TASK_LOCK:
            main.CANVAS_TASKS.clear()

    def tearDown(self):
        with main.CANVAS_TASK_LOCK:
            main.CANVAS_TASKS.clear()

    async def test_background_video_task_keeps_result_after_client_stops_polling(self):
        task_id = "canvas_video_test"
        payload = main.CanvasVideoRequest(prompt="test prompt", provider_id="openrouter")
        with main.CANVAS_TASK_LOCK:
            main.CANVAS_TASKS[task_id] = {
                "id": task_id,
                "type": "video",
                "status": "queued",
                "result": None,
                "error": "",
            }

        result = {"videos": ["/assets/output/video.mp4"], "task_id": "upstream-1"}
        with patch.object(main, "canvas_video", new=AsyncMock(return_value=result)):
            await main.run_canvas_video_task(task_id, payload)

        with main.CANVAS_TASK_LOCK:
            task = dict(main.CANVAS_TASKS[task_id])
        self.assertEqual(task["status"], "succeeded")
        self.assertEqual(task["result"], result)
        self.assertEqual(task["upstream_task_id"], "upstream-1")

    async def test_background_video_task_preserves_failure_detail(self):
        task_id = "canvas_video_failed"
        payload = main.CanvasVideoRequest(prompt="test prompt", provider_id="openrouter")
        with main.CANVAS_TASK_LOCK:
            main.CANVAS_TASKS[task_id] = {
                "id": task_id,
                "type": "video",
                "status": "queued",
                "result": None,
                "error": "",
            }

        async def fail(_payload):
            exc = main.HTTPException(status_code=502, detail="upstream failed")
            setattr(exc, "upstream_task_id", "upstream-failed")
            raise exc

        with patch.object(main, "canvas_video", side_effect=fail):
            await main.run_canvas_video_task(task_id, payload)

        with main.CANVAS_TASK_LOCK:
            task = dict(main.CANVAS_TASKS[task_id])
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["error"], "upstream failed")
        self.assertEqual(task["status_code"], 502)
        self.assertEqual(task["upstream_task_id"], "upstream-failed")


if __name__ == "__main__":
    unittest.main()
