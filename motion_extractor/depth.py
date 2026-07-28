"""Clip-consistent grayscale depth rendering through Video Depth Anything Small."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal

import numpy as np

from .errors import MotionCancelled, MotionMediaError, MotionOutOfMemory, MotionRuntimeError
from .media import EncodeResult, SharedFrameStore, encode_rgb_frames
from .models import (
    MotionCancelled as AssetPreparationCancelled,
    ensure_depth_assets,
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
_INFER_LENGTH = 32
_OVERLAP = 10
_INTERPOLATION_LENGTH = 8
_WINDOW_STEP = _INFER_LENGTH - _OVERLAP
_ALIGNMENT_KEYFRAMES = (0, 12)
_VDA_KEYFRAMES = (0, 12, 24, 25, 26, 27, 28, 29, 30, 31)


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


class _VDAWindowAdapter:
    """Pinned VDA inference reduced to one bounded, model-sized depth window."""

    def __init__(self, model: Any, torch_module: Any, cv2_module: Any, compose: Any, transforms: tuple[Any, Any, Any]) -> None:
        self._model = model
        self._torch = torch_module
        self._cv2 = cv2_module
        self._compose = compose
        self._resize, self._normalize, self._prepare = transforms
        self._pre_input: Any | None = None

    def infer_depth_window(
        self,
        frames: np.ndarray,
        start: int,
        *,
        input_size: int,
        device: str,
        fp32: bool,
    ) -> np.ndarray:
        height, width = frames.shape[1:3]
        ratio = max(height, width) / min(height, width)
        effective_size = int(input_size * 1.777 / ratio) if ratio > 1.78 else input_size
        effective_size = round(effective_size / 14) * 14
        transform = self._compose([
            self._resize(
                width=effective_size, height=effective_size, resize_target=False,
                keep_aspect_ratio=True, ensure_multiple_of=14, resize_method="lower_bound",
                image_interpolation_method=self._cv2.INTER_CUBIC,
            ),
            self._normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
            self._prepare(),
        ])
        tensors = []
        for offset in range(_INFER_LENGTH):
            frame = np.asarray(frames[min(start + offset, len(frames) - 1)], dtype=np.float32) / 255.0
            tensors.append(self._torch.from_numpy(transform({"image": frame})["image"]).unsqueeze(0).unsqueeze(0))
        inputs = self._torch.cat(tensors, dim=1).to(device)
        if self._pre_input is not None:
            inputs[:, :_OVERLAP, ...] = self._pre_input[:, _VDA_KEYFRAMES, ...]
        with self._torch.no_grad():
            with self._torch.autocast(device_type=device, enabled=not fp32):
                depth = self._model.forward(inputs).to(inputs.dtype)
        self._pre_input = inputs
        return depth[0].detach().cpu().numpy()


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
            if not self._cuda_is_available():
                raise MotionRuntimeError(_RUNTIME_MESSAGE)
            device = "cuda"
            model = self._load_model(device, cancelled, reporter)
            self._check_cancelled(cancelled)
            self._work_dir.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(prefix="motion-depth-", dir=self._work_dir) as temporary:
                temporary_path = Path(temporary)
                depth_shape = self._write_windowed_depth_memmap(
                    model,
                    frame_store,
                    temporary_path / "relative.depth",
                    cancelled,
                    reporter,
                    input_size,
                    device,
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

        assets = ensure_depth_assets(
            self._cache_root,
            lambda _message, value: reporter.report(0.05 * value),
            cancelled,
        )
        with verified_source_imports(self._cache_root, assets, source_names=("video-depth-anything",)):
            import torch
            import cv2
            from torchvision.transforms import Compose
            from video_depth_anything.util.transform import NormalizeImage, PrepareForNet, Resize
            from video_depth_anything.video_depth import VideoDepthAnything

            model = VideoDepthAnything(**_SMALL_CONFIGURATION)
            model.load_state_dict(torch.load(assets[_DEPTH_WEIGHT_NAME], map_location="cpu"), strict=True)
            return _VDAWindowAdapter(
                model.to(device).eval(), torch, cv2, Compose, (Resize, NormalizeImage, PrepareForNet)
            )

    def _write_windowed_depth_memmap(
        self,
        model: Any,
        frame_store: SharedFrameStore,
        path: Path,
        cancelled: Callable[[], bool],
        reporter: _MonotonicProgress,
        input_size: int,
        device: str,
    ) -> tuple[int, int]:
        frame_count = frame_store.metadata.frame_count
        mapped: np.memmap | None = None
        written = 0
        pending: np.ndarray | None = None
        reference: np.ndarray | None = None
        depth_shape: tuple[int, int] | None = None
        try:
            for start in range(0, frame_count, _WINDOW_STEP):
                self._check_cancelled(cancelled)
                window = self._depth_window(
                    model.infer_depth_window(
                        frame_store.frames, start, input_size=input_size, device=device, fp32=False
                    )
                )
                if mapped is None:
                    depth_shape = window.shape[1:]
                    mapped = np.memmap(path, mode="w+", dtype=np.float16, shape=(frame_count, *depth_shape))

                def persist(frames: np.ndarray) -> None:
                    nonlocal written
                    assert mapped is not None
                    available = min(len(frames), frame_count - written)
                    if available:
                        mapped[written:written + available] = frames[:available]
                        written += available
                        reporter.report(0.05 + 0.60 * written / frame_count)

                if pending is None:
                    reference = window[list(_ALIGNMENT_KEYFRAMES)].copy()
                    persist(window[: _INFER_LENGTH - _INTERPOLATION_LENGTH])
                    pending = window[_INFER_LENGTH - _INTERPOLATION_LENGTH :].copy()
                else:
                    assert reference is not None
                    scale, shift = self._alignment_scale_shift(window[: len(_ALIGNMENT_KEYFRAMES)], reference)
                    post = np.maximum(window[len(_ALIGNMENT_KEYFRAMES) : _OVERLAP] * scale + shift, 0)
                    persist(self._interpolate_overlap(pending, post))
                    aligned = np.maximum(window[_OVERLAP:] * scale + shift, 0)
                    persist(aligned[: _INFER_LENGTH - _OVERLAP - _INTERPOLATION_LENGTH])
                    pending = aligned[-_INTERPOLATION_LENGTH:].copy()
                    reference = np.stack((reference[0], np.maximum(window[_ALIGNMENT_KEYFRAMES[1]] * scale + shift, 0)))
                mapped.flush()
            if mapped is None or depth_shape is None or written != frame_count:
                raise MotionRuntimeError(_RUNTIME_MESSAGE)
            return depth_shape
        finally:
            if mapped is not None:
                mapped.flush()
                mapping = getattr(mapped, "_mmap", None)
                if mapping is not None:
                    mapping.close()

    @staticmethod
    def _depth_window(value: object) -> np.ndarray:
        array = np.asarray(value, dtype=np.float32)
        if array.ndim != 3 or array.shape[0] != _INFER_LENGTH or not np.isfinite(array).all():
            raise MotionRuntimeError(_RUNTIME_MESSAGE)
        return array

    @staticmethod
    def _alignment_scale_shift(prediction: np.ndarray, target: np.ndarray) -> tuple[float, float]:
        prediction = prediction.astype(np.float32, copy=False).reshape(-1)
        target = target.astype(np.float32, copy=False).reshape(-1)
        a00 = float(np.dot(prediction, prediction))
        a01 = float(np.sum(prediction))
        a11 = float(prediction.size)
        b0 = float(np.dot(prediction, target))
        b1 = float(np.sum(target))
        determinant = a00 * a11 - a01 * a01
        if determinant == 0:
            return 1.0, 0.0
        return (a11 * b0 - a01 * b1) / determinant, (-a01 * b0 + a00 * b1) / determinant

    @staticmethod
    def _interpolate_overlap(previous: np.ndarray, current: np.ndarray) -> np.ndarray:
        weights = np.linspace(0.0, 1.0, _INTERPOLATION_LENGTH, dtype=np.float32).reshape(-1, 1, 1)
        return previous * (1.0 - weights) + current * weights

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
                    self._check_cancelled(cancelled)
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
