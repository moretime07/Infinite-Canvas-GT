"""Behavior coverage for clip-consistent Video Depth Anything output."""

from __future__ import annotations

from itertools import chain
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from motion_extractor import media
from motion_extractor.depth import DepthProcessor, _VDAWindowAdapter
from motion_extractor.errors import MotionMediaError, MotionOutOfMemory, MotionRuntimeError


class FakeDepthModel:
    """A bounded fake for the VDA ``infer_depth_window`` boundary."""

    def __init__(self, depths: list[np.ndarray], *, error: Exception | None = None) -> None:
        self.depths = depths
        self.error = error
        self.calls: list[dict[str, object]] = []

    def infer_depth_window(self, frames: np.ndarray, start: int, **options: object) -> np.ndarray:
        self.calls.append({"frame_shape": frames.shape, "start": start, **options})
        if self.error is not None:
            raise self.error
        return np.stack([self.depths[min(start + index, len(self.depths) - 1)] for index in range(32)])


class FakeWindowedDepthModel:
    """A fake VDA window boundary; it never returns a full-clip depth result."""

    def __init__(self, depths: list[np.ndarray], *, error: Exception | None = None, work_dir: Path | None = None) -> None:
        self.depths = depths
        self.error = error
        self.work_dir = work_dir
        self.calls: list[dict[str, object]] = []
        self.persisted_before_second_window = False

    def infer_depth_window(self, frames: np.ndarray, start: int, **options: object) -> np.ndarray:
        self.calls.append({"start": start, "frame_shape": frames.shape, **options})
        if len(self.calls) == 2 and self.work_dir is not None:
            self.persisted_before_second_window = bool(list(self.work_dir.rglob("*.depth")))
        if self.error is not None:
            raise self.error
        return np.stack([self.depths[min(start + index, len(self.depths) - 1)] for index in range(32)])


class FakeTensor:
    def __init__(self, array: np.ndarray) -> None:
        self.array = array

    @property
    def dtype(self) -> np.dtype:
        return self.array.dtype

    def unsqueeze(self, dimension: int) -> "FakeTensor":
        return FakeTensor(np.expand_dims(self.array, dimension))

    def to(self, _target: object) -> "FakeTensor":
        return self

    def detach(self) -> "FakeTensor":
        return self

    def cpu(self) -> "FakeTensor":
        return self

    def numpy(self) -> np.ndarray:
        return self.array

    def __getitem__(self, key: object) -> "FakeTensor":
        return FakeTensor(self.array[key])

    def __setitem__(self, key: object, value: "FakeTensor") -> None:
        self.array[key] = value.array


class FakeTorch:
    class _Context:
        def __enter__(self) -> None:
            return None

        def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
            return None

    class _Functional:
        @staticmethod
        def interpolate(value: FakeTensor, *, size: tuple[int, int], **_options: object) -> FakeTensor:
            return FakeTensor(np.zeros((*value.array.shape[:2], *size), dtype=value.array.dtype))

    class _NN:
        def __init__(self) -> None:
            self.functional = FakeTorch._Functional()

    @staticmethod
    def from_numpy(value: np.ndarray) -> FakeTensor:
        return FakeTensor(value)

    @staticmethod
    def cat(values: list[FakeTensor], dim: int) -> FakeTensor:
        return FakeTensor(np.concatenate([value.array for value in values], axis=dim))

    @staticmethod
    def no_grad() -> "FakeTorch._Context":
        return FakeTorch._Context()

    @staticmethod
    def autocast(**_options: object) -> "FakeTorch._Context":
        return FakeTorch._Context()


FakeTorch.nn = FakeTorch._NN()


class FakeTensorModel:
    def __init__(self) -> None:
        self.window_values: list[np.ndarray] = []
        self.window_shapes: list[tuple[int, ...]] = []

    def forward(self, inputs: FakeTensor) -> FakeTensor:
        self.window_values.append(inputs.array[0, :, 0, 0, 0].copy())
        self.window_shapes.append(inputs.array.shape)
        return FakeTensor(inputs.array[:, :, 0, :2, :3])


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

    def test_vda_adapter_hands_prior_keyframes_to_each_bounded_window(self) -> None:
        """Dropping VDA's pre-input handoff changes cross-window temporal semantics."""
        frames = np.stack([
            np.full((4, 6, 3), index, dtype=np.uint8) for index in range(76)
        ])
        model = FakeTensorModel()
        adapter = _VDAWindowAdapter(
            model,
            FakeTorch(),
            type("FakeCV2", (), {"INTER_CUBIC": 1})(),
            lambda _steps: lambda item: {"image": np.moveaxis(item["image"], -1, 0)},
            (lambda **_options: None, lambda **_options: None, lambda: None),
        )

        for start in (0, 22, 44):
            depth = adapter.infer_depth_window(frames, start, input_size=518, device="cuda", fp32=False)
            self.assertEqual(depth.shape, (32, 2, 3))

        first, second, third = model.window_values
        expected_second = first[[0, 12, 24, 25, 26, 27, 28, 29, 30, 31]]
        expected_third = second[[0, 12, 24, 25, 26, 27, 28, 29, 30, 31]]
        self.assertTrue(np.array_equal(second[:10], expected_second))
        self.assertTrue(np.array_equal(third[:10], expected_third))
        self.assertEqual(model.window_shapes, [(1, 32, 3, 4, 6)] * 3)
        self.assertEqual(adapter._pre_input.array.shape[1], 32)

    def test_model_sized_depth_memmap_renders_back_to_high_source_resolution(self) -> None:
        """Upscaling before persistence would make the task memmap source-sized."""
        frames = np.zeros((2, 80, 120, 3), dtype=np.uint8)
        depths = [np.full((5, 7), value, dtype=np.float32) for value in (0.1, 0.9)]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            store = self._store(directory, frames)
            rendered: list[np.ndarray] = []
            observed: list[tuple[int, ...]] = []
            original_memmap = np.memmap

            def inspect_memmap(*arguments: object, **keywords: object) -> np.memmap:
                mapped = original_memmap(*arguments, **keywords)
                if keywords.get("mode") == "w+":
                    observed.append(mapped.shape)
                return mapped

            try:
                with mock.patch("motion_extractor.depth.np.memmap", side_effect=inspect_memmap):
                    result = self._processor(directory, FakeWindowedDepthModel(depths), rendered).run(
                        store, directory / "depth.mp4", lambda _value: None, lambda: False
                    )
            finally:
                store.close()
            self.assertEqual(result.state, "completed")
            self.assertEqual(observed, [(2, 5, 7)])
            self.assertEqual([frame.shape for frame in rendered], [(80, 120, 3), (80, 120, 3)])

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

    def test_window_adapter_uses_bounded_overlapping_windows_and_persists_between_calls(self) -> None:
        """Replacing the window path with infer_video_depth would lose bounded persistence."""
        frames = np.zeros((46, 4, 6, 3), dtype=np.uint8)
        depths = [np.full((2, 3), index / 46, dtype=np.float32) for index in range(46)]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            store = self._store(directory, frames)
            model = FakeWindowedDepthModel(depths, work_dir=directory / "work")
            rendered: list[np.ndarray] = []
            try:
                result = self._processor(directory, model, rendered).run(
                    store, directory / "depth.mp4", lambda _value: None, lambda: False
                )
            finally:
                store.close()
            self.assertEqual(result.state, "completed")
            self.assertEqual([call["start"] for call in model.calls], [0, 22, 44])
            self.assertTrue(model.persisted_before_second_window)
            self.assertEqual(len(rendered), 46)

    def test_cancellation_at_a_window_boundary_skips_the_next_inference_call(self) -> None:
        """Checking cancellation only after inference would still run one unwanted window."""
        frames = np.zeros((46, 4, 6, 3), dtype=np.uint8)
        depths = [np.full((2, 3), index / 46, dtype=np.float32) for index in range(46)]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            store = self._store(directory, frames)
            model = FakeWindowedDepthModel(depths)
            cancelled = False
            infer_window = model.infer_depth_window

            def cancel_after_first_window(*arguments, **keywords):
                nonlocal cancelled
                result = infer_window(*arguments, **keywords)
                cancelled = True
                return result

            model.infer_depth_window = cancel_after_first_window
            try:
                result = self._processor(directory, model, []).run(
                    store, directory / "depth.mp4", lambda _value: None, lambda: cancelled
                )
            finally:
                store.close()
            self.assertEqual(result.state, "cancelled")
            self.assertEqual([call["start"] for call in model.calls], [0])

    def test_missing_cuda_is_a_sanitized_runtime_error(self) -> None:
        """Falling back to CPU violates the local FP16/CUDA processing contract."""
        frames = np.zeros((1, 4, 6, 3), dtype=np.uint8)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            store = self._store(directory, frames)
            try:
                with self.assertRaises(MotionRuntimeError):
                    self._processor(
                        directory, FakeWindowedDepthModel([np.ones((2, 3), dtype=np.float32)]), [], cuda_available=False
                    ).run(store, directory / "depth.mp4", lambda _value: None, lambda: False)
            finally:
                store.close()

    def test_cancellation_inside_encode_generator_aborts_without_publishing_output(self) -> None:
        """Checking only before encode allows a cancelled frame stream to publish an MP4."""
        frames = np.zeros((2, 4, 6, 3), dtype=np.uint8)
        depths = [np.full((2, 3), value, dtype=np.float32) for value in (0.1, 0.9)]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            store = self._store(directory, frames)
            cancel_requested = False

            def cancelled() -> bool:
                return cancel_requested

            def encode(frame_iterable, metadata, destination, source, preserve_audio):
                nonlocal cancel_requested
                iterator = iter(frame_iterable)
                first = next(iterator)
                cancel_requested = True
                return media.encode_rgb_frames(
                    chain((first,), iterator), metadata, destination, source, preserve_audio
                )

            processor = DepthProcessor(
                cache_root=directory / "cache", work_dir=directory / "work",
                model_factory=lambda _assets, _device: FakeWindowedDepthModel(depths),
                cuda_available=lambda: True, encoder=encode,
            )
            output = directory / "depth.mp4"
            try:
                result = processor.run(store, output, lambda _value: None, cancelled)
            finally:
                store.close()
            self.assertEqual(result.state, "cancelled")
            self.assertFalse(output.exists())
            self.assertFalse(list(directory.glob("*.tmp.mp4")))


if __name__ == "__main__":
    unittest.main()
