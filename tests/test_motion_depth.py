"""Behavior coverage for clip-consistent Video Depth Anything output."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from motion_extractor import media
from motion_extractor.depth import DepthProcessor
from motion_extractor.errors import MotionMediaError, MotionOutOfMemory


class FakeDepthModel:
    """A bounded fake for the upstream ``infer_video_depth`` boundary."""

    def __init__(self, depths: list[np.ndarray], *, error: Exception | None = None) -> None:
        self.depths = depths
        self.error = error
        self.calls: list[dict[str, object]] = []

    def infer_video_depth(self, frames: np.ndarray, target_fps: float, **options: object) -> tuple[object, float]:
        self.calls.append({"frame_shape": frames.shape, "target_fps": target_fps, **options})
        if self.error is not None:
            raise self.error
        return iter(self.depths), target_fps


class MotionDepthTests(unittest.TestCase):
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
            ),
            raw_path,
            mapped,
        )

    def _processor(
        self,
        directory: Path,
        model: FakeDepthModel,
        captured: list[np.ndarray],
        *,
        cuda_available: bool = True,
    ) -> DepthProcessor:
        def encode(
            frames: object,
            _metadata: media.VideoMetadata,
            destination: Path,
            _source: Path,
            preserve_audio: bool,
        ) -> media.EncodeResult:
            self.assertFalse(preserve_audio)
            captured.extend(np.asarray(frame).copy() for frame in frames)  # type: ignore[arg-type]
            destination.write_bytes(b"fake mp4")
            return media.EncodeResult(destination=destination, audio_transcoded=False)

        return DepthProcessor(
            cache_root=directory / "cache",
            work_dir=directory / "work",
            model_factory=lambda _assets, _device: model,
            cuda_available=lambda: cuda_available,
            encoder=encode,
        )

    def test_cuda_inference_uses_fp16_mode_and_one_task_local_float16_memmap(self) -> None:
        """Removing CUDA/fp16 options or buffering raw depths breaks this contract."""
        frames = np.zeros((3, 4, 6, 3), dtype=np.uint8)
        raw_depths = [np.full((2, 3), value, dtype=np.float32) for value in (0.1, 0.5, 0.9)]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            model = FakeDepthModel(raw_depths)
            rendered: list[np.ndarray] = []
            store = self._store(directory, frames)
            observed_depth_files: list[tuple[np.dtype[object], tuple[int, ...]]] = []
            original_memmap = np.memmap

            def inspect_memmap(*args: object, **kwargs: object) -> np.memmap:
                mapped = original_memmap(*args, **kwargs)
                if kwargs.get("mode") == "w+":
                    observed_depth_files.append((mapped.dtype, mapped.shape))
                return mapped

            processor = self._processor(directory, model, rendered)
            try:
                with mock.patch("motion_extractor.depth.np.memmap", side_effect=inspect_memmap):
                    result = processor.run(store, directory / "depth.mp4", lambda _value: None, lambda: False)
            finally:
                store.close()

            self.assertEqual(result.state, "completed")
            self.assertEqual(model.calls[0]["device"], "cuda")
            self.assertFalse(model.calls[0]["fp32"])
            self.assertEqual(observed_depth_files, [(np.dtype(np.float16), (3, 2, 3))])
            self.assertFalse(list((directory / "work").glob("*.depth")))

    def test_clip_global_normalization_keeps_equal_depth_equal_and_near_brighter(self) -> None:
        """Per-frame bounds or inverted depth makes this visual contract fail."""
        frames = np.zeros((2, 4, 3, 3), dtype=np.uint8)
        depths = [
            np.array([[0.0, 0.5, 1.0], [0.0, 0.5, 1.0]], dtype=np.float32),
            np.array([[0.2, 0.5, 0.8], [0.2, 0.5, 0.8]], dtype=np.float32),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            rendered: list[np.ndarray] = []
            store = self._store(directory, frames)
            try:
                result = self._processor(directory, FakeDepthModel(depths), rendered).run(
                    store, directory / "depth.mp4", lambda _value: None, lambda: False
                )
            finally:
                store.close()

            self.assertEqual(result.state, "completed")
            self.assertEqual([frame.shape for frame in rendered], [(4, 3, 3), (4, 3, 3)])
            self.assertTrue(np.array_equal(rendered[0][..., 0], rendered[0][..., 1]))
            self.assertTrue(np.array_equal(rendered[0][..., 1], rendered[0][..., 2]))
            self.assertEqual(int(rendered[0][0, 1, 0]), int(rendered[1][0, 1, 0]))
            self.assertGreater(int(rendered[0][0, 2, 0]), int(rendered[0][0, 0, 0]))

    def test_cancellation_stops_between_inference_windows_and_before_encode(self) -> None:
        """Removing either cancellation checkpoint causes inference or encoding after cancellation."""
        frames = np.zeros((3, 4, 6, 3), dtype=np.uint8)
        depths = [np.full((2, 3), value, dtype=np.float32) for value in (0.1, 0.5, 0.9)]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            store = self._store(directory, frames)
            encoded: list[np.ndarray] = []
            checks = 0

            def cancelled() -> bool:
                nonlocal checks
                checks += 1
                return checks >= 3

            try:
                result = self._processor(directory, FakeDepthModel(depths), encoded).run(
                    store, directory / "depth.mp4", lambda _value: None, cancelled
                )
            finally:
                store.close()
            self.assertEqual(result.state, "cancelled")
            self.assertFalse(encoded)

    def test_cancellation_after_inference_prevents_encoding(self) -> None:
        """Removing the pre-encode checkpoint would publish output after cancellation."""
        frames = np.zeros((3, 4, 6, 3), dtype=np.uint8)
        depths = [np.full((2, 3), value, dtype=np.float32) for value in (0.1, 0.5, 0.9)]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            store = self._store(directory, frames)
            encoded: list[np.ndarray] = []
            checks = 0

            def cancelled() -> bool:
                nonlocal checks
                checks += 1
                return checks >= 7

            try:
                result = self._processor(directory, FakeDepthModel(depths), encoded).run(
                    store, directory / "depth.mp4", lambda _value: None, cancelled
                )
            finally:
                store.close()
            self.assertEqual(result.state, "cancelled")
            self.assertFalse(encoded)

    def test_media_error_remains_the_central_public_error(self) -> None:
        """Duplicating the media exception would split callers' error handling."""
        self.assertIs(media.MotionMediaError, MotionMediaError)

    def test_encoding_media_failure_keeps_its_typed_error(self) -> None:
        """Converting a media failure to a generic runtime error loses recovery semantics."""
        frames = np.zeros((1, 4, 6, 3), dtype=np.uint8)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            store = self._store(directory, frames)
            processor = self._processor(
                directory, FakeDepthModel([np.ones((2, 3), dtype=np.float32)]), []
            )

            def fail_encode(*_arguments: object, **_keywords: object) -> media.EncodeResult:
                raise MotionMediaError("媒体文件无效或无法读取。")

            processor._encoder = fail_encode
            try:
                with self.assertRaises(MotionMediaError):
                    processor.run(store, directory / "depth.mp4", lambda _value: None, lambda: False)
            finally:
                store.close()

    def test_cuda_oom_is_typed_and_progress_never_moves_backward(self) -> None:
        """Leaking the raw CUDA exception or regressing progress breaks service handling."""
        frames = np.zeros((2, 4, 6, 3), dtype=np.uint8)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            store = self._store(directory, frames)
            try:
                with self.assertRaises(MotionOutOfMemory) as raised:
                    self._processor(
                        directory,
                        FakeDepthModel([], error=RuntimeError("CUDA out of memory: private details")),
                        [],
                    ).run(store, directory / "depth.mp4", lambda _value: None, lambda: False)
            finally:
                store.close()
            self.assertNotIn("private details", str(raised.exception))

            store = self._store(directory, frames)
            progress: list[float] = []
            try:
                result = self._processor(
                    directory,
                    FakeDepthModel([np.zeros((2, 3), dtype=np.float32), np.ones((2, 3), dtype=np.float32)]),
                    [],
                ).run(store, directory / "depth-progress.mp4", progress.append, lambda: False)
            finally:
                store.close()
            self.assertEqual(result.state, "completed")
            self.assertEqual(progress[0], 0.0)
            self.assertEqual(progress[-1], 1.0)
            self.assertEqual(progress, sorted(progress))


if __name__ == "__main__":
    unittest.main()
