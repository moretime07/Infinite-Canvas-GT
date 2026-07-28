import asyncio
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
from motion_extractor.depth import BranchResult
from motion_extractor.errors import MotionMediaError, MotionOutOfMemory, MotionRuntimeError
from motion_extractor.media import VideoMetadata
from motion_extractor.service import MotionTaskService


METADATA = VideoMetadata(
    width=2,
    height=2,
    fps_num=24,
    fps_den=1,
    frame_count=1,
    duration_seconds=1.0 / 24.0,
    rotation=0,
    has_audio=False,
)


class FakeFrameStore:
    def __init__(self, metadata=METADATA):
        self.metadata = metadata
        self.frames = object()
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        self.closed = True


class SuccessfulDepth:
    def __init__(self, observations=None, warning=None):
        self.observations = observations
        self.warning = warning

    def run(self, frame_store, output_path, progress, cancelled, input_size=518):
        if self.observations is not None:
            self.observations.append(("depth", id(frame_store), input_size))
        if cancelled():
            return BranchResult("cancelled", None)
        progress(0.5)
        Path(output_path).write_bytes(b"depth")
        progress(1.0)
        return BranchResult("completed", Path(output_path), self.warning)


class SuccessfulPose:
    def __init__(self, observations=None, warning=None):
        self.observations = observations
        self.warning = warning

    def run(self, frame_store, output_path, progress, cancelled):
        if self.observations is not None:
            self.observations.append(("pose", id(frame_store), None))
        if cancelled():
            return BranchResult("cancelled", None)
        progress(0.5)
        Path(output_path).write_bytes(b"pose")
        progress(1.0)
        return BranchResult("completed", Path(output_path), self.warning)


class CanvasMotionTaskTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.mp4"
        self.source.write_bytes(b"local")
        self.services = []

    async def asyncTearDown(self):
        for service in reversed(self.services):
            await service.close()
        self.temporary.cleanup()

    def make_service(
        self,
        *,
        depth_factory=None,
        pose_factory=None,
        decoder=None,
        prober=None,
        audio_muxer=None,
    ):
        service = MotionTaskService(
            output_dir=self.root / "assets" / "output" / "motion",
            work_dir=self.root / "work",
            depth_factory=depth_factory or (lambda: SuccessfulDepth()),
            pose_factory=pose_factory or (lambda: SuccessfulPose()),
            decoder=decoder or (lambda _path, _work_dir: FakeFrameStore()),
            prober=prober or (lambda _path: METADATA),
            audio_muxer=audio_muxer,
        )
        self.services.append(service)
        return service

    async def wait_for_state(self, service, task_id, expected, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            record = await service.get(task_id)
            if record is not None and record["state"] in expected:
                return record
            await asyncio.sleep(0.01)
        self.fail(f"task did not reach {sorted(expected)}")

    async def post(self, payload):
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/api/canvas-motion-tasks", json=payload)

    async def test_post_rejects_missing_unsafe_and_nonlocal_urls_through_safe_resolver(self):
        service = self.make_service()
        for source_url in ("", "https://example.invalid/video.mp4", r"C:\private\video.mp4"):
            with self.subTest(source_url=source_url), \
                    patch.object(main, "MOTION_TASK_SERVICE", service), \
                    patch.object(main, "output_file_from_url", return_value=None) as resolver:
                response = await self.post({"source_url": source_url})
                self.assertEqual(response.status_code, 422)
                resolver.assert_called_once_with(source_url)

    async def test_post_rejects_when_both_processors_are_disabled(self):
        service = self.make_service()
        with patch.object(main, "MOTION_TASK_SERVICE", service):
            response = await self.post({
                "source_url": "/assets/input/source.mp4",
                "depth_enabled": False,
                "pose_enabled": False,
            })
        self.assertEqual(response.status_code, 422)

    async def test_corrupt_and_overlong_sources_fail_preflight_before_processor_work(self):
        processor_started = threading.Event()

        class ShouldNotRun(SuccessfulDepth):
            def run(self, *args, **kwargs):
                processor_started.set()
                return super().run(*args, **kwargs)

        for name in ("corrupt.mp4", "over-30-seconds.mp4"):
            with self.subTest(name=name):
                service = self.make_service(
                    depth_factory=ShouldNotRun,
                    prober=lambda _path: (_ for _ in ()).throw(MotionMediaError("private path")),
                )
                with patch.object(main, "MOTION_TASK_SERVICE", service), \
                        patch.object(main, "output_file_from_url", return_value=str(self.source)):
                    response = await self.post({"source_url": f"/assets/input/{name}"})
                self.assertEqual(response.status_code, 422)
                self.assertFalse(processor_started.is_set())
                self.assertNotIn(str(self.source), response.text)

    async def test_post_returns_202_with_the_queued_contract_immediately(self):
        started = threading.Event()

        class BlockingDepth:
            def run(self, _store, output_path, _progress, cancelled, input_size=518):
                started.set()
                while not cancelled():
                    time.sleep(0.005)
                Path(output_path).write_bytes(b"incomplete")
                return BranchResult("cancelled", None)

        service = self.make_service(depth_factory=BlockingDepth)
        with patch.object(main, "MOTION_TASK_SERVICE", service), \
                patch.object(main, "output_file_from_url", return_value=str(self.source)):
            response = await self.post({"source_url": "/assets/input/source.mp4"})
        self.assertEqual(response.status_code, 202)
        record = response.json()
        self.assertEqual(
            {
                "state": record["state"],
                "stage": record["stage"],
                "progress": record["progress"],
                "queue_position": record["queue_position"],
                "depth_state": record["depth_state"],
                "depth_url": record["depth_url"],
                "depth_error": record["depth_error"],
                "pose_state": record["pose_state"],
                "pose_url": record["pose_url"],
                "pose_error": record["pose_error"],
                "warnings": record["warnings"],
                "low_memory_retry": record["low_memory_retry"],
            },
            {
                "state": "queued",
                "stage": "queued",
                "progress": 0.0,
                "queue_position": 1,
                "depth_state": "pending",
                "depth_url": None,
                "depth_error": None,
                "pose_state": "disabled",
                "pose_url": None,
                "pose_error": None,
                "warnings": [],
                "low_memory_retry": False,
            },
        )
        await asyncio.to_thread(started.wait, 1.0)
        await service.cancel(record["task_id"])

    async def test_two_tasks_run_fifo_with_at_most_one_active_processor(self):
        starts = [threading.Event(), threading.Event()]
        releases = [threading.Event(), threading.Event()]
        order = []
        active = 0
        max_active = 0
        lock = threading.Lock()
        factory_index = 0

        class OrderedDepth:
            def __init__(self, index):
                self.index = index

            def run(self, _store, output_path, _progress, cancelled, input_size=518):
                nonlocal active, max_active
                with lock:
                    active += 1
                    max_active = max(max_active, active)
                    order.append(self.index)
                starts[self.index].set()
                try:
                    while not releases[self.index].wait(0.005):
                        if cancelled():
                            return BranchResult("cancelled", None)
                    Path(output_path).write_bytes(str(self.index).encode())
                    return BranchResult("completed", Path(output_path))
                finally:
                    with lock:
                        active -= 1

        def factory():
            nonlocal factory_index
            processor = OrderedDepth(factory_index)
            factory_index += 1
            return processor

        service = self.make_service(depth_factory=factory)
        first = await service.submit("/assets/input/first.mp4", self.source, True, False, False)
        second = await service.submit("/assets/input/second.mp4", self.source, True, False, False)
        self.assertEqual(first["state"], "queued")
        self.assertEqual(second["state"], "queued")
        self.assertTrue(await asyncio.to_thread(starts[0].wait, 1.0))
        self.assertFalse(starts[1].is_set())
        releases[0].set()
        self.assertTrue(await asyncio.to_thread(starts[1].wait, 1.0))
        releases[1].set()
        await self.wait_for_state(service, first["task_id"], {"completed"})
        await self.wait_for_state(service, second["task_id"], {"completed"})
        self.assertEqual(order, [0, 1])
        self.assertEqual(max_active, 1)

    async def test_depth_success_and_pose_failure_is_partial_with_one_shared_decode(self):
        observations = []
        decode_count = 0

        def decoder(_path, _work_dir):
            nonlocal decode_count
            decode_count += 1
            return FakeFrameStore()

        class FailingPose:
            def run(self, frame_store, _output_path, _progress, _cancelled):
                observations.append(("pose", id(frame_store), None))
                raise MotionRuntimeError(r"Traceback C:\private\clip.mp4 sk-secret")

        service = self.make_service(
            depth_factory=lambda: SuccessfulDepth(observations),
            pose_factory=FailingPose,
            decoder=decoder,
        )
        created = await service.submit("/assets/input/source.mp4", self.source, True, True, False)
        record = await self.wait_for_state(service, created["task_id"], {"partial"})
        self.assertEqual(record["depth_state"], "completed")
        self.assertTrue(record["depth_url"].startswith("/assets/output/motion/"))
        self.assertEqual(record["pose_state"], "failed")
        self.assertIsNone(record["pose_url"])
        self.assertEqual(decode_count, 1)
        self.assertEqual(observations[0][1], observations[1][1])
        self.assertNotIn("private", record["pose_error"].lower())
        self.assertNotIn("sk-", record["pose_error"].lower())

    async def test_cancel_cleans_incomplete_pose_and_preserves_published_depth(self):
        pose_started = threading.Event()

        class CancellablePose:
            def run(self, _store, output_path, _progress, cancelled):
                Path(output_path).write_bytes(b"incomplete-pose")
                pose_started.set()
                while not cancelled():
                    time.sleep(0.005)
                return BranchResult("cancelled", None)

        service = self.make_service(pose_factory=CancellablePose)
        created = await service.submit("/assets/input/source.mp4", self.source, True, True, False)
        self.assertTrue(await asyncio.to_thread(pose_started.wait, 1.0))
        record = await service.cancel(created["task_id"])
        self.assertEqual(record["state"], "cancelled")
        self.assertEqual(record["depth_state"], "completed")
        self.assertEqual(record["pose_state"], "cancelled")
        published = service.output_dir / Path(record["depth_url"]).name
        self.assertTrue(published.is_file())
        self.assertEqual(list(service.work_dir.rglob("*.mp4")), [])

    async def test_depth_oom_retries_exactly_once_at_input_size_392(self):
        input_sizes = []

        class RetryDepth:
            def run(self, _store, output_path, _progress, _cancelled, input_size=518):
                input_sizes.append(input_size)
                if len(input_sizes) == 1:
                    raise MotionOutOfMemory("first")
                Path(output_path).write_bytes(b"depth")
                return BranchResult("completed", Path(output_path))

        service = self.make_service(depth_factory=RetryDepth)
        created = await service.submit("/assets/input/source.mp4", self.source, True, False, False)
        record = await self.wait_for_state(service, created["task_id"], {"completed"})
        self.assertEqual(input_sizes, [518, 392])
        self.assertTrue(record["low_memory_retry"])

    async def test_second_depth_oom_is_a_sanitized_branch_failure(self):
        input_sizes = []

        class AlwaysOom:
            def run(self, _store, _output_path, _progress, _cancelled, input_size=518):
                input_sizes.append(input_size)
                raise MotionOutOfMemory(r"Traceback C:\private\model.pth sk-secret")

        service = self.make_service(depth_factory=AlwaysOom)
        created = await service.submit("/assets/input/source.mp4", self.source, True, False, False)
        record = await self.wait_for_state(service, created["task_id"], {"failed"})
        self.assertEqual(input_sizes, [518, 392])
        self.assertTrue(record["low_memory_retry"])
        self.assertEqual(record["depth_state"], "failed")
        serialized = json.dumps(record)
        self.assertNotIn("Traceback", serialized)
        self.assertNotIn(r"C:\\private", serialized)
        self.assertNotIn("sk-secret", serialized)

    async def test_completed_api_record_has_only_safe_randomized_asset_urls(self):
        service = self.make_service(
            pose_factory=lambda: SuccessfulPose(warning="C:/private/model.pth"),
        )
        source_url = "/assets/input/source.mp4?token=sk-secret"
        with patch.object(main, "MOTION_TASK_SERVICE", service), \
                patch.object(main, "output_file_from_url", return_value=str(self.source)):
            response = await self.post({
                "source_url": source_url,
                "depth_enabled": True,
                "pose_enabled": True,
            })
            created = response.json()
            record = await self.wait_for_state(service, created["task_id"], {"completed"})
            fetched = await main.get_canvas_motion_task(created["task_id"])
        self.assertEqual(record, fetched)
        self.assertEqual(record["source_url"], "/assets/input/source.mp4")
        self.assertRegex(record["depth_url"], r"^/assets/output/motion/[0-9a-f]{32}-depth\.mp4$")
        self.assertRegex(record["pose_url"], r"^/assets/output/motion/[0-9a-f]{32}-pose\.mp4$")
        serialized = json.dumps(record)
        self.assertNotIn(str(self.root), serialized)
        self.assertNotIn("C:/private", serialized)
        self.assertNotIn("sk-secret", serialized)

    async def test_unknown_get_and_cancel_routes_return_404(self):
        service = self.make_service()
        with patch.object(main, "MOTION_TASK_SERVICE", service):
            with self.assertRaises(main.HTTPException) as get_error:
                await main.get_canvas_motion_task("unknown")
            with self.assertRaises(main.HTTPException) as cancel_error:
                await main.cancel_canvas_motion_task("unknown")
        self.assertEqual(get_error.exception.status_code, 404)
        self.assertEqual(cancel_error.exception.status_code, 404)

    async def test_disabled_branch_is_not_counted_as_a_failure(self):
        service = self.make_service()
        created = await service.submit("/assets/input/source.mp4", self.source, True, False, False)
        record = await self.wait_for_state(service, created["task_id"], {"completed"})
        self.assertEqual(record["state"], "completed")
        self.assertEqual(record["depth_state"], "completed")
        self.assertEqual(record["pose_state"], "disabled")
        self.assertIsNone(record["pose_error"])

    async def test_preserve_audio_uses_injected_mux_before_publication(self):
        mux_calls = []
        audio_metadata = VideoMetadata(**{**METADATA.__dict__, "has_audio": True})

        def muxer(video_path, source_path, destination):
            mux_calls.append((Path(video_path), Path(source_path), Path(destination)))
            Path(destination).write_bytes(b"muxed")

        service = self.make_service(
            decoder=lambda _path, _work_dir: FakeFrameStore(audio_metadata),
            prober=lambda _path: audio_metadata,
            audio_muxer=muxer,
        )
        created = await service.submit("/assets/input/source.mp4", self.source, True, False, True)
        record = await self.wait_for_state(service, created["task_id"], {"completed"})
        self.assertEqual(len(mux_calls), 1)
        self.assertEqual(mux_calls[0][1], self.source)
        published = service.output_dir / Path(record["depth_url"]).name
        self.assertEqual(published.read_bytes(), b"muxed")


if __name__ == "__main__":
    unittest.main()
