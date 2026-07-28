"""Contract tests for all-person, gap-safe DWPose rendering."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from motion_extractor import media, models
from motion_extractor.pose import PersonPose, PoseFrame, PoseProcessor, render_pose_frame, smooth_pose_sequence


class _Input:
    def __init__(self, name: str) -> None:
        self.name = name
        self.shape = [1, 3, 640, 640]


class FakeDetectorSession:
    def __init__(self, boxes: list[np.ndarray]) -> None:
        self.boxes = boxes
        self.calls: list[tuple[object, object]] = []

    def get_inputs(self):
        return [_Input("images")]

    def run(self, outputs, inputs):
        self.calls.append((outputs, inputs))
        return [self.boxes.pop(0)]


class FakePoseSession:
    def __init__(self, poses: list[np.ndarray]) -> None:
        self.poses = poses
        self.calls: list[tuple[object, object]] = []

    def get_inputs(self):
        return [_Input("input")]

    def run(self, outputs, inputs):
        self.calls.append((outputs, inputs))
        return [self.poses.pop(0)]


class MotionPoseTests(unittest.TestCase):
    def _store(self, directory: Path, frames: np.ndarray) -> media.SharedFrameStore:
        raw_path = directory / "source.rgb"
        mapped = np.memmap(raw_path, mode="w+", dtype=np.uint8, shape=frames.shape)
        mapped[:] = frames
        mapped.flush()
        return media.SharedFrameStore(
            media.VideoMetadata(
                width=frames.shape[2], height=frames.shape[1], fps_num=12, fps_den=1,
                frame_count=frames.shape[0], duration_seconds=frames.shape[0] / 12,
                rotation=0, has_audio=False,
            ), raw_path, mapped,
        )

    def _processor(
        self, directory: Path, detector: FakeDetectorSession, pose: FakePoseSession, captured: list[np.ndarray], **kwargs: object
    ) -> PoseProcessor:
        def encode(frames, _metadata, destination, _source, preserve_audio):
            self.assertFalse(preserve_audio)
            captured.extend(np.asarray(frame).copy() for frame in frames)
            destination.write_bytes(b"fake mp4")
            return media.EncodeResult(destination=destination, audio_transcoded=False)

        return PoseProcessor(
            cache_root=directory / "cache", work_dir=directory / "work", detector_session=detector,
            pose_session=pose, encoder=encode, **kwargs,
        )

    def test_uses_cuda_for_both_verified_onnx_sessions_then_explicit_cpu_fallback(self) -> None:
        """CPU is permitted only after constructing a CUDA-backed ONNX session fails."""
        attempts: list[tuple[str, tuple[str, ...]]] = []

        def session(path: Path, providers: list[str]):
            attempts.append((path.name, tuple(providers)))
            if providers == ["CUDAExecutionProvider"]:
                raise RuntimeError("CUDA provider unavailable")
            return FakeDetectorSession([np.empty((0, 5), dtype=np.float32)])

        processor = PoseProcessor(cache_root=Path("cache"), session_factory=session)
        assets = {"yolox_l.onnx": Path("yolox_l.onnx"), "dw-ll_ucoco_384.onnx": Path("dw-ll_ucoco_384.onnx")}
        with self.assertWarnsRegex(RuntimeWarning, "CPU"):
            detector, pose = processor._load_sessions(assets)

        self.assertIsInstance(detector, FakeDetectorSession)
        self.assertIsInstance(pose, FakeDetectorSession)
        self.assertEqual(
            attempts,
            [
                ("yolox_l.onnx", ("CUDAExecutionProvider",)),
                ("yolox_l.onnx", ("CPUExecutionProvider",)),
                ("dw-ll_ucoco_384.onnx", ("CUDAExecutionProvider",)),
                ("dw-ll_ucoco_384.onnx", ("CPUExecutionProvider",)),
            ],
        )

    def test_retains_every_person_and_renders_body_feet_hands_and_face_on_black(self) -> None:
        """Selecting a best box or drawing source pixels violates the pose-reference contract."""
        frame = np.zeros((1, 32, 32, 3), dtype=np.uint8)
        boxes = np.array([[1, 1, 15, 30, 0.9], [17, 1, 31, 30, 0.8]], dtype=np.float32)
        first = np.zeros((133, 3), dtype=np.float32)
        second = np.zeros((133, 3), dtype=np.float32)
        for pose, offset in ((first, 2), (second, 18)):
            pose[[0, 5, 6, 11, 12, 17, 20, 23, 91, 112]] = [offset, 8, 1.0]
            pose[5] = [offset, 12, 1.0]
            pose[11] = [offset + 3, 18, 1.0]
            pose[17] = [offset + 4, 25, 1.0]
            pose[20] = [offset + 5, 25, 1.0]
            pose[23] = [offset, 6, 1.0]
            pose[91] = [offset + 2, 14, 1.0]
            pose[112] = [offset + 3, 14, 1.0]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            rendered: list[np.ndarray] = []
            store = self._store(directory, frame)
            try:
                result = self._processor(
                    directory, FakeDetectorSession([boxes]), FakePoseSession([first, second]), rendered
                ).run(store, directory / "pose.mp4", lambda _value: None, lambda: False)
            finally:
                store.close()
        self.assertEqual(result.state, "completed")
        self.assertEqual(len(rendered), 1)
        image = rendered[0]
        self.assertTrue(np.array_equal(image[..., 0], image[..., 1]))
        self.assertTrue(np.array_equal(image[..., 1], image[..., 2]))
        self.assertEqual(set(np.unique(image)).difference({0, 224, 255}), set())
        self.assertTrue(image[:, :16].any())
        self.assertTrue(image[:, 16:].any())

    def test_smoothing_uses_adjacent_present_keypoints_without_filling_gaps(self) -> None:
        """Filling absent points makes missed detections look like tracked people."""
        def person(x: float, confidence: float = 1.0) -> PersonPose:
            return PersonPose((0, 0, 10, 10), np.array([[x, 5, confidence]], dtype=np.float32), track_id=4)

        smoothed = smooth_pose_sequence(
            [PoseFrame((person(0),)), PoseFrame((person(9),)), PoseFrame((person(12),))], 0.5
        )
        self.assertGreater(float(smoothed[1].people[0].keypoints[0, 0]), 0.0)
        self.assertLess(float(smoothed[1].people[0].keypoints[0, 0]), 9.0)

        missing = PersonPose((0, 0, 10, 10), np.array([[0, 0, 0]], dtype=np.float32), track_id=4)
        gap = smooth_pose_sequence([PoseFrame((person(0),)), PoseFrame((missing,)), PoseFrame((person(12),))], 0.5)
        self.assertEqual(float(gap[1].people[0].keypoints[0, 2]), 0.0)
        self.assertEqual(float(gap[1].people[0].keypoints[0, 0]), 0.0)

    def test_public_smoothing_associates_only_adjacent_frames_and_ends_tracks_at_gaps(self) -> None:
        """Association inside smoothing must never reconnect a person after an empty frame."""
        def person(x: float) -> PersonPose:
            return PersonPose((x, 0, x + 10, 10), np.array([[x + 2, 5, 1]], dtype=np.float32))

        smoothed = smooth_pose_sequence([PoseFrame((person(0),)), PoseFrame(()), PoseFrame((person(40),))], 0.5)

        self.assertIsNotNone(smoothed[0].people[0].track_id)
        self.assertIsNotNone(smoothed[2].people[0].track_id)
        self.assertNotEqual(smoothed[0].people[0].track_id, smoothed[2].people[0].track_id)

    def test_missed_frames_stay_black_and_no_people_completes_with_warning(self) -> None:
        """A missed frame must never duplicate a preceding pose or fail the entire clip."""
        frames = np.zeros((2, 16, 16, 3), dtype=np.uint8)
        boxes = [np.array([[1, 1, 12, 14, 0.9]], dtype=np.float32), np.empty((0, 5), dtype=np.float32)]
        pose = np.zeros((133, 3), dtype=np.float32)
        pose[[0, 5]] = [[4, 4, 1], [5, 10, 1]]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            rendered: list[np.ndarray] = []
            store = self._store(directory, frames)
            try:
                result = self._processor(directory, FakeDetectorSession(boxes), FakePoseSession([pose]), rendered).run(
                    store, directory / "pose.mp4", lambda _value: None, lambda: False
                )
            finally:
                store.close()
        self.assertEqual(result.state, "completed")
        self.assertTrue(rendered[0].any())
        self.assertFalse(rendered[1].any())

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            rendered = []
            store = self._store(directory, frames[:1])
            try:
                result = self._processor(
                    directory, FakeDetectorSession([np.empty((0, 5), dtype=np.float32)]), FakePoseSession([]), rendered
                ).run(store, directory / "empty.mp4", lambda _value: None, lambda: False)
            finally:
                store.close()
        self.assertEqual(result.state, "completed")
        self.assertIsNotNone(result.warning)
        self.assertFalse(rendered[0].any())

    def test_cancellation_progress_and_temporary_cleanup_match_depth(self) -> None:
        """Cancellation must avoid encoding and never leave task-temporary pose files behind."""
        frames = np.zeros((2, 16, 16, 3), dtype=np.uint8)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            rendered: list[np.ndarray] = []
            store = self._store(directory, frames)
            checks = 0

            def cancelled() -> bool:
                nonlocal checks
                checks += 1
                return checks >= 3

            try:
                result = self._processor(
                    directory, FakeDetectorSession([np.empty((0, 5), dtype=np.float32)]), FakePoseSession([]), rendered
                ).run(store, directory / "cancelled.mp4", lambda _value: None, cancelled)
            finally:
                store.close()
        self.assertEqual(result.state, "cancelled")
        self.assertFalse(rendered)
        self.assertFalse(list((directory / "work").glob("motion-pose-*")))

        # Progress uses the same monotonic, 0-to-1 contract as depth.
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            values: list[float] = []
            rendered = []
            store = self._store(directory, frames[:1])
            try:
                result = self._processor(
                    directory, FakeDetectorSession([np.empty((0, 5), dtype=np.float32)]), FakePoseSession([]), rendered
                ).run(store, directory / "progress.mp4", values.append, lambda: False)
            finally:
                store.close()
        self.assertEqual(result.state, "completed")
        self.assertEqual(values[0], 0.0)
        self.assertEqual(values[-1], 1.0)
        self.assertEqual(values, sorted(values))

    def test_pose_asset_helper_requests_only_dwpose_source_and_onnx_files(self) -> None:
        """Preparing pose must never request VDA sources or checkpoints."""
        with mock.patch.object(models, "ensure_motion_assets", return_value={}) as prepare:
            models.ensure_pose_assets(Path("cache"), lambda _message, _value: None, lambda: False)
        self.assertEqual(
            prepare.call_args.kwargs,
            {
                "source_names": (models.DWPOSE_SOURCE.name,),
                "artifact_names": ("yolox_l.onnx", "dw-ll_ucoco_384.onnx"),
            },
        )


if __name__ == "__main__":
    unittest.main()
