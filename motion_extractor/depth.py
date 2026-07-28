"""Clip-consistent grayscale depth rendering through Video Depth Anything Small."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal

import numpy as np

from .errors import MotionCancelled, MotionMediaError, MotionOutOfMemory, MotionRuntimeError
from .media import EncodeResult, SharedFrameStore, encode_rgb_frames
from .models import (
    MotionCancelled as AssetPreparationCancelled,
    ensure_motion_assets,
    verified_source_imports,
)


_SMALL_CONFIGURATION = {
    "encoder": "vits",
    "features": 64,
    "out_channels": [48, 96, 192, 384],
}
_DEPTH_WEIGHT_NAME = "video_depth_anything_vits.pth"
_OOM_MESSAGE = "\u672c\u5730\u663e\u5b58\u4e0d\u8db3\uff0c\u65e0\u6cd5\u5b8c\u6210\u6df1\u5ea6\u63d0\u53d6\u3002"
_RUNTIME_MESSAGE = "\u672c\u5730\u6df1\u5ea6\u5904\u7406\u5931\u8d25\u3002"
_CANCELLED_MESSAGE = "\u4efb\u52a1\u5df2\u53d6\u6d88\u3002"
_ROBUST_HISTOGRAM_BINS = 2048


@dataclass(frozen=True)
class BranchResult:
    state: Literal["completed", "failed", "cancelled"]
    output_path: Path | None
    warning: str | None = None


class _MonotonicProgress:
    def __init__(self, callback: Callable[[float], None]) -> None:
        self._callback = callback
        self._value = 0.0

    def report(self, value: float) -> None:
        self._value = max(self._value, min(1.0, float(value)))
        self._callback(self._value)


class DepthProcessor:
    """Run the pinned Video Depth Anything Small model without clip-sized RAM copies."""

    def __init__(
        self,
        cache_root: Path = Path("data"),
        work_dir: Path | None = None,
        *,
        model_factory: Callable[[Mapping[str, Path], str], Any] | None = None,
        cuda_available: Callable[[], bool] | None = None,
        encoder: Callable[..., EncodeResult] = encode_rgb_frames,
    ) -> None:
        self._cache_root = Path(cache_root)
        self._work_dir = Path(work_dir) if work_dir is not None else self._cache_root / "motion_tasks"
        self._model_factory = model_factory
        self._cuda_available = cuda_available
        self._encoder = encoder

    def run(
        self,
        frame_store: SharedFrameStore,
        output_path: Path,
        progress: Callable[[float], None],
        cancelled: Callable[[], bool],
        input_size: int = 518,
    ) -> BranchResult:
        """Infer, normalize, and stream grayscale depth frames to ``output_path``."""
        reporter = _MonotonicProgress(progress)
        reporter.report(0.0)
        try:
            self._check_cancelled(cancelled)
            if input_size <= 0:
                raise MotionRuntimeError(_RUNTIME_MESSAGE)
            device = "cuda" if self._cuda_is_available() else "cpu"
            model = self._load_model(device, cancelled, reporter)
            self._check_cancelled(cancelled)
            inference = model.infer_video_depth(
                frame_store.frames,
                frame_store.metadata.fps_num / frame_store.metadata.fps_den,
                input_size=input_size,
                device=device,
                fp32=False,
            )
            depth_frames = self._depth_frames(inference)
            self._work_dir.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(prefix="motion-depth-", dir=self._work_dir) as temporary:
                temporary_path = Path(temporary)
                depth_shape = self._write_depth_memmap(
                    depth_frames,
                    frame_store.metadata.frame_count,
                    temporary_path / "relative.depth",
                    cancelled,
                    reporter,
                )
                self._check_cancelled(cancelled)
                self._encode_normalized_depth(
                    temporary_path / "relative.depth",
                    depth_shape,
                    frame_store,
                    output_path,
                    cancelled,
                    reporter,
                )
            reporter.report(1.0)
            return BranchResult("completed", Path(output_path))
        except (MotionCancelled, AssetPreparationCancelled):
            return BranchResult("cancelled", None, _CANCELLED_MESSAGE)
        except MotionOutOfMemory:
            raise
        except MotionMediaError:
            raise
        except MotionRuntimeError:
            raise
        except RuntimeError as error:
            if self._is_cuda_oom(error):
                raise MotionOutOfMemory(_OOM_MESSAGE) from None
            raise MotionRuntimeError(_RUNTIME_MESSAGE) from None
        except Exception:
            raise MotionRuntimeError(_RUNTIME_MESSAGE) from None

    def _cuda_is_available(self) -> bool:
        if self._cuda_available is not None:
            return bool(self._cuda_available())
        try:
            import torch

            return bool(torch.cuda.is_available())
        except ImportError:
            return False

    def _load_model(
        self,
        device: str,
        cancelled: Callable[[], bool],
        reporter: _MonotonicProgress,
    ) -> Any:
        if self._model_factory is not None:
            return self._model_factory({}, device)

        assets = ensure_motion_assets(
            self._cache_root,
            lambda _message, value: reporter.report(0.05 * value),
            cancelled,
        )
        with verified_source_imports(self._cache_root, assets):
            import torch
            from video_depth_anything.video_depth import VideoDepthAnything

            model = VideoDepthAnything(**_SMALL_CONFIGURATION)
            model.load_state_dict(torch.load(assets[_DEPTH_WEIGHT_NAME], map_location="cpu"), strict=True)
            return model.to(device).eval()

    @staticmethod
    def _depth_frames(inference: object) -> Iterator[object]:
        payload = inference[0] if isinstance(inference, tuple) else inference
        if isinstance(payload, np.ndarray):
            return iter(payload)
        if isinstance(payload, Iterable):
            return iter(payload)
        raise MotionRuntimeError(_RUNTIME_MESSAGE)

    def _write_depth_memmap(
        self,
        depths: Iterator[object],
        frame_count: int,
        path: Path,
        cancelled: Callable[[], bool],
        reporter: _MonotonicProgress,
    ) -> tuple[int, int]:
        self._check_cancelled(cancelled)
        try:
            first = self._depth_frame(next(depths))
        except StopIteration:
            raise MotionRuntimeError(_RUNTIME_MESSAGE) from None
        height, width = first.shape
        mapped = np.memmap(path, mode="w+", dtype=np.float16, shape=(frame_count, height, width))
        try:
            mapped[0] = first
            reporter.report(0.05 + 0.60 / frame_count)
            for index in range(1, frame_count):
                self._check_cancelled(cancelled)
                try:
                    mapped[index] = self._depth_frame(next(depths))
                except StopIteration:
                    raise MotionRuntimeError(_RUNTIME_MESSAGE) from None
                reporter.report(0.05 + 0.60 * (index + 1) / frame_count)
            self._check_cancelled(cancelled)
            try:
                next(depths)
            except StopIteration:
                return height, width
            raise MotionRuntimeError(_RUNTIME_MESSAGE)
        finally:
            mapped.flush()
            mapping = getattr(mapped, "_mmap", None)
            if mapping is not None:
                mapping.close()

    @staticmethod
    def _depth_frame(value: object) -> np.ndarray:
        array = np.asarray(value)
        while array.ndim > 2 and array.shape[0] == 1:
            array = array[0]
        if array.ndim != 2 or not np.isfinite(array).all():
            raise MotionRuntimeError(_RUNTIME_MESSAGE)
        return array.astype(np.float16, copy=False)

    def _encode_normalized_depth(
        self,
        path: Path,
        depth_shape: tuple[int, int],
        frame_store: SharedFrameStore,
        output_path: Path,
        cancelled: Callable[[], bool],
        reporter: _MonotonicProgress,
    ) -> None:
        count = frame_store.metadata.frame_count
        mapped = np.memmap(path, mode="r", dtype=np.float16, shape=(count, *depth_shape))
        depths = mapped
        try:
            lower, upper = self._robust_bounds(depths)
            self._check_cancelled(cancelled)
            reporter.report(0.68)

            def rendered_frames() -> Iterator[np.ndarray]:
                for index in range(count):
                    yield self._render_depth(depths[index], lower, upper, frame_store)
                    reporter.report(0.68 + 0.30 * (index + 1) / count)

            self._check_cancelled(cancelled)
            self._encoder(
                rendered_frames(),
                frame_store.metadata,
                Path(output_path),
                Path(output_path),
                preserve_audio=False,
            )
        finally:
            mapping = getattr(mapped, "_mmap", None)
            if mapping is not None:
                mapping.close()

    @staticmethod
    def _robust_bounds(depths: np.ndarray) -> tuple[float, float]:
        minimum = float("inf")
        maximum = float("-inf")
        for frame in depths:
            minimum = min(minimum, float(np.min(frame)))
            maximum = max(maximum, float(np.max(frame)))
        if not np.isfinite(minimum) or not np.isfinite(maximum) or maximum <= minimum:
            return minimum, maximum
        histogram = np.zeros(_ROBUST_HISTOGRAM_BINS, dtype=np.int64)
        for frame in depths:
            counts, _edges = np.histogram(
                frame.astype(np.float32, copy=False),
                bins=_ROBUST_HISTOGRAM_BINS,
                range=(minimum, maximum),
            )
            histogram += counts
        total = int(histogram.sum())
        if total == 0:
            return minimum, maximum
        edges = np.linspace(minimum, maximum, _ROBUST_HISTOGRAM_BINS + 1)
        lower_index = int(np.searchsorted(np.cumsum(histogram), max(1, int(total * 0.02))))
        upper_index = int(np.searchsorted(np.cumsum(histogram), max(1, int(total * 0.98))))
        return float(edges[min(lower_index, _ROBUST_HISTOGRAM_BINS - 1)]), float(
            edges[min(upper_index + 1, _ROBUST_HISTOGRAM_BINS)]
        )

    @staticmethod
    def _render_depth(
        depth: np.ndarray,
        lower: float,
        upper: float,
        frame_store: SharedFrameStore,
    ) -> np.ndarray:
        if upper <= lower:
            grayscale = np.zeros(depth.shape, dtype=np.uint8)
        else:
            grayscale = np.clip((depth - lower) * (255.0 / (upper - lower)), 0, 255).astype(np.uint8)
        height, width = frame_store.metadata.height, frame_store.metadata.width
        if grayscale.shape != (height, width):
            try:
                import cv2

                grayscale = cv2.resize(grayscale, (width, height), interpolation=cv2.INTER_LINEAR)
            except ImportError:
                row_index = np.linspace(0, grayscale.shape[0] - 1, height).astype(int)
                column_index = np.linspace(0, grayscale.shape[1] - 1, width).astype(int)
                grayscale = grayscale[row_index][:, column_index]
        return np.repeat(grayscale[..., None], 3, axis=2)

    @staticmethod
    def _check_cancelled(cancelled: Callable[[], bool]) -> None:
        if cancelled():
            raise MotionCancelled(_CANCELLED_MESSAGE)

    @staticmethod
    def _is_cuda_oom(error: RuntimeError) -> bool:
        message = str(error).lower()
        return "out of memory" in message and ("cuda" in message or "cudnn" in message)
