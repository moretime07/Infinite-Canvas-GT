"""All-person DWPose extraction with gap-safe temporal smoothing."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal
import warnings

import numpy as np

from .depth import BranchResult, _MonotonicProgress
from .errors import MotionCancelled, MotionMediaError, MotionRuntimeError
from .media import EncodeResult, SharedFrameStore, encode_rgb_frames
from .models import MotionCancelled as AssetPreparationCancelled
from .models import ensure_pose_assets


_DETECTOR_WEIGHT = "yolox_l.onnx"
_POSE_WEIGHT = "dw-ll_ucoco_384.onnx"
_RUNTIME_MESSAGE = "本地姿态处理失败。"
_CANCELLED_MESSAGE = "任务已取消。"
_NO_PEOPLE_WARNING = "No people were detected; the pose reference is black."
_DEFAULT_CONFIDENCE = 0.30
_LINE_VALUE = np.uint8(224)
_JOINT_VALUE = np.uint8(255)

# COCO WholeBody: body 0..16, feet 17..22, face 23..90, hands 91..132.
_BODY_EDGES = (
    (0, 1), (0, 2), (1, 3), (2, 4), (5, 6), (5, 7), (7, 9),
    (6, 8), (8, 10), (5, 11), (6, 12), (11, 12), (11, 13),
    (13, 15), (12, 14), (14, 16), (15, 17), (15, 18), (15, 19),
    (16, 20), (16, 21), (16, 22),
)


@dataclass(frozen=True)
class PersonPose:
    """One detected person with whole-body keypoints in source-pixel coordinates."""

    bbox: tuple[float, float, float, float]
    keypoints: np.ndarray
    track_id: int | None = None


@dataclass(frozen=True)
class PoseFrame:
    people: tuple[PersonPose, ...]


def _valid_keypoint(point: np.ndarray, threshold: float) -> bool:
    return bool(point.shape == (3,) and np.isfinite(point).all() and point[2] >= threshold)


def _bbox_iou(first: tuple[float, float, float, float], second: tuple[float, float, float, float]) -> float:
    left, top = max(first[0], second[0]), max(first[1], second[1])
    right, bottom = min(first[2], second[2]), min(first[3], second[3])
    overlap = max(0.0, right - left) * max(0.0, bottom - top)
    first_area = max(0.0, first[2] - first[0]) * max(0.0, first[3] - first[1])
    second_area = max(0.0, second[2] - second[0]) * max(0.0, second[3] - second[1])
    union = first_area + second_area - overlap
    return overlap / union if union else 0.0


def _normalized_keypoint_distance(first: PersonPose, second: PersonPose, threshold: float) -> float | None:
    count = min(len(first.keypoints), len(second.keypoints))
    if not count:
        return None
    shared = [
        index for index in range(count)
        if _valid_keypoint(first.keypoints[index], threshold) and _valid_keypoint(second.keypoints[index], threshold)
    ]
    if not shared:
        return None
    distance = np.mean([
        np.linalg.norm(first.keypoints[index, :2] - second.keypoints[index, :2]) for index in shared
    ])
    scale = max(
        first.bbox[2] - first.bbox[0], first.bbox[3] - first.bbox[1],
        second.bbox[2] - second.bbox[0], second.bbox[3] - second.bbox[1], 1.0,
    )
    return float(distance / scale)


def _associate_adjacent(frames: Sequence[PoseFrame], threshold: float) -> list[PoseFrame]:
    """Assign ids from the immediately preceding frame only; gaps end tracks."""
    assigned: list[PoseFrame] = []
    next_track_id = 0
    previous: PoseFrame | None = None
    for frame in frames:
        people = list(frame.people)
        current_ids: list[int | None] = [None] * len(people)
        if previous is not None and previous.people and people:
            candidates: list[tuple[float, int, int]] = []
            for prior_index, prior in enumerate(previous.people):
                for current_index, current in enumerate(people):
                    iou = _bbox_iou(prior.bbox, current.bbox)
                    distance = _normalized_keypoint_distance(prior, current, threshold)
                    if iou >= 0.05 or (distance is not None and distance <= 0.50):
                        candidates.append((iou + (1.0 - distance if distance is not None else 0.0), prior_index, current_index))
            used_prior: set[int] = set()
            used_current: set[int] = set()
            for _score, prior_index, current_index in sorted(candidates, reverse=True):
                if prior_index in used_prior or current_index in used_current:
                    continue
                track_id = previous.people[prior_index].track_id
                if track_id is not None:
                    current_ids[current_index] = track_id
                    used_prior.add(prior_index)
                    used_current.add(current_index)
        current_people = []
        for person, track_id in zip(people, current_ids):
            if track_id is None:
                track_id = next_track_id
                next_track_id += 1
            current_people.append(replace(person, track_id=track_id))
        current = PoseFrame(tuple(current_people))
        assigned.append(current)
        previous = current
    return assigned


def smooth_pose_sequence(frames: Sequence[PoseFrame], confidence_threshold: float) -> list[PoseFrame]:
    """Average present confident points with matching immediate-neighbor tracks only."""
    if confidence_threshold < 0 or not np.isfinite(confidence_threshold):
        raise ValueError("confidence_threshold must be finite and non-negative")
    tracked = _associate_adjacent(frames, confidence_threshold)
    result: list[PoseFrame] = []
    for index, frame in enumerate(tracked):
        prior = {person.track_id: person for person in tracked[index - 1].people} if index else {}
        following = {person.track_id: person for person in tracked[index + 1].people} if index + 1 < len(tracked) else {}
        smoothed_people = []
        for person in frame.people:
            points = np.asarray(person.keypoints, dtype=np.float32)
            if points.ndim != 2 or points.shape[1] != 3:
                raise MotionRuntimeError(_RUNTIME_MESSAGE)
            updated = points.copy()
            neighbors = (prior.get(person.track_id), following.get(person.track_id))
            for point_index, point in enumerate(points):
                if not _valid_keypoint(point, confidence_threshold):
                    continue
                values = [point[:2]]
                for neighbor in neighbors:
                    if neighbor is not None and point_index < len(neighbor.keypoints):
                        candidate = neighbor.keypoints[point_index]
                        if _valid_keypoint(candidate, confidence_threshold):
                            values.append(candidate[:2])
                updated[point_index, :2] = np.mean(values, axis=0)
            smoothed_people.append(replace(person, keypoints=updated))
        result.append(PoseFrame(tuple(smoothed_people)))
    return result


def _draw_line(canvas: np.ndarray, first: np.ndarray, second: np.ndarray) -> None:
    x0, y0 = (int(round(first[0])), int(round(first[1])))
    x1, y1 = (int(round(second[0])), int(round(second[1])))
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    error = dx + dy
    while True:
        if 0 <= y0 < canvas.shape[0] and 0 <= x0 < canvas.shape[1]:
            canvas[y0, x0] = _LINE_VALUE
        if x0 == x1 and y0 == y1:
            return
        doubled = 2 * error
        if doubled >= dy:
            error += dy
            x0 += sx
        if doubled <= dx:
            error += dx
            y0 += sy


def _pose_edges(point_count: int) -> tuple[tuple[int, int], ...]:
    edges = list(_BODY_EDGES)
    # Face contours may be absent in smaller model layouts; sequential links retain them when present.
    edges.extend((index, index + 1) for index in range(23, min(point_count - 1, 90)))
    for start, end in ((91, 111), (112, 132)):
        edges.extend((index, index + 1) for index in range(start, min(point_count - 1, end)))
    return tuple((first, second) for first, second in edges if first < point_count and second < point_count)


def render_pose_frame(frame: PoseFrame, width: int, height: int) -> np.ndarray:
    """Render only light grayscale pose strokes onto a pure RGB-black background."""
    if width <= 0 or height <= 0:
        raise MotionRuntimeError(_RUNTIME_MESSAGE)
    canvas = np.zeros((height, width), dtype=np.uint8)
    for person in frame.people:
        points = np.asarray(person.keypoints, dtype=np.float32)
        if points.ndim != 2 or points.shape[1] != 3:
            raise MotionRuntimeError(_RUNTIME_MESSAGE)
        for first, second in _pose_edges(len(points)):
            if _valid_keypoint(points[first], _DEFAULT_CONFIDENCE) and _valid_keypoint(points[second], _DEFAULT_CONFIDENCE):
                _draw_line(canvas, points[first], points[second])
        for point in points:
            if _valid_keypoint(point, _DEFAULT_CONFIDENCE):
                x, y = int(round(point[0])), int(round(point[1]))
                if 0 <= y < height and 0 <= x < width:
                    canvas[y, x] = _JOINT_VALUE
    return np.repeat(canvas[..., None], 3, axis=2)


class PoseProcessor:
    """Run the two DWPose ONNX models and encode a grayscale pose-only clip."""

    def __init__(
        self,
        cache_root: Path = Path("data"),
        work_dir: Path | None = None,
        *,
        session_factory: Callable[[Path, list[str]], Any] | None = None,
        detector_session: Any | None = None,
        pose_session: Any | None = None,
        encoder: Callable[..., EncodeResult] = encode_rgb_frames,
        confidence_threshold: float = _DEFAULT_CONFIDENCE,
    ) -> None:
        self._cache_root = Path(cache_root)
        self._work_dir = Path(work_dir) if work_dir is not None else self._cache_root / "motion_tasks"
        self._session_factory = session_factory
        self._detector_session = detector_session
        self._pose_session = pose_session
        self._encoder = encoder
        self._confidence_threshold = confidence_threshold

    def run(
        self,
        frame_store: SharedFrameStore,
        output_path: Path,
        progress: Callable[[float], None],
        cancelled: Callable[[], bool],
    ) -> BranchResult:
        reporter = _MonotonicProgress(progress)
        reporter.report(0.0)
        try:
            self._check_cancelled(cancelled)
            detector, pose = self._sessions(cancelled, reporter)
            self._check_cancelled(cancelled)
            self._work_dir.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(prefix="motion-pose-", dir=self._work_dir):
                frames = self._infer_frames(frame_store, detector, pose, cancelled, reporter)
                self._check_cancelled(cancelled)
                smoothed = smooth_pose_sequence(frames, self._confidence_threshold)
                reporter.report(0.68)
                self._encode(smoothed, frame_store, output_path, cancelled, reporter)
            reporter.report(1.0)
            warning = _NO_PEOPLE_WARNING if not any(frame.people for frame in frames) else None
            return BranchResult("completed", Path(output_path), warning)
        except (MotionCancelled, AssetPreparationCancelled):
            return BranchResult("cancelled", None, _CANCELLED_MESSAGE)
        except MotionMediaError:
            raise
        except MotionRuntimeError:
            raise
        except Exception:
            raise MotionRuntimeError(_RUNTIME_MESSAGE) from None

    def _sessions(self, cancelled: Callable[[], bool], reporter: _MonotonicProgress) -> tuple[Any, Any]:
        if self._detector_session is not None and self._pose_session is not None:
            return self._detector_session, self._pose_session
        assets = ensure_pose_assets(self._cache_root, lambda _message, value: reporter.report(0.05 * value), cancelled)
        self._check_cancelled(cancelled)
        return self._load_sessions(assets)

    def _load_sessions(self, assets: Mapping[str, Path]) -> tuple[Any, Any]:
        factory = self._session_factory
        if factory is None:
            import onnxruntime
            factory = lambda path, providers: onnxruntime.InferenceSession(str(path), providers=providers)
        return (
            self._open_session(factory, Path(assets[_DETECTOR_WEIGHT])),
            self._open_session(factory, Path(assets[_POSE_WEIGHT])),
        )

    @staticmethod
    def _open_session(factory: Callable[[Path, list[str]], Any], path: Path) -> Any:
        try:
            return factory(path, ["CUDAExecutionProvider"])
        except Exception:
            warnings.warn("CUDA ONNX provider initialization failed; using CPU fallback.", RuntimeWarning, stacklevel=2)
            return factory(path, ["CPUExecutionProvider"])

    def _infer_frames(
        self, frame_store: SharedFrameStore, detector: Any, pose: Any, cancelled: Callable[[], bool], reporter: _MonotonicProgress
    ) -> list[PoseFrame]:
        frames = []
        total = frame_store.metadata.frame_count
        for index, source in enumerate(frame_store.frames):
            self._check_cancelled(cancelled)
            people = []
            for bbox in self._detect(detector, source):
                self._check_cancelled(cancelled)
                people.append(PersonPose(bbox, self._estimate_pose(pose, source, bbox)))
            frames.append(PoseFrame(tuple(people)))
            reporter.report(0.05 + 0.60 * (index + 1) / total)
        return frames

    @staticmethod
    def _session_input(session: Any, value: np.ndarray) -> Any:
        if hasattr(session, "get_inputs") and hasattr(session, "run"):
            inputs = session.get_inputs()
            if not inputs:
                raise MotionRuntimeError(_RUNTIME_MESSAGE)
            return session.run(None, {inputs[0].name: value})
        raise MotionRuntimeError(_RUNTIME_MESSAGE)

    def _detect(self, session: Any, source: np.ndarray) -> list[tuple[float, float, float, float]]:
        if hasattr(session, "detect"):
            raw = session.detect(source)
        else:
            raw = self._session_input(session, self._image_tensor(session, source))[0]
        array = np.asarray(raw, dtype=np.float32)
        while array.ndim > 2 and array.shape[0] == 1:
            array = array[0]
        if array.size == 0:
            return []
        if array.ndim != 2 or array.shape[1] < 5 or not np.isfinite(array).all():
            raise MotionRuntimeError(_RUNTIME_MESSAGE)
        boxes = []
        for row in array:
            if row.shape[0] >= 85:
                score = float(row[4] * np.max(row[5:]))
                x, y, width, height = row[:4]
                coordinates = (x - width / 2, y - height / 2, x + width / 2, y + height / 2)
            else:
                score = float(row[4])
                coordinates = tuple(row[:4])
            if score < self._confidence_threshold:
                continue
            left, top, right, bottom = coordinates
            left, right = sorted((max(0.0, float(left)), min(float(source.shape[1]), float(right))))
            top, bottom = sorted((max(0.0, float(top)), min(float(source.shape[0]), float(bottom))))
            if right > left and bottom > top:
                boxes.append((left, top, right, bottom))
        return boxes

    @staticmethod
    def _image_tensor(session: Any, image: np.ndarray) -> np.ndarray:
        height, width = image.shape[:2]
        target_height, target_width = height, width
        if hasattr(session, "get_inputs"):
            shape = getattr(session.get_inputs()[0], "shape", ())
            if len(shape) == 4 and isinstance(shape[2], int) and isinstance(shape[3], int):
                target_height, target_width = shape[2], shape[3]
        rows = np.linspace(0, height - 1, target_height).astype(int)
        columns = np.linspace(0, width - 1, target_width).astype(int)
        resized = image[rows][:, columns].astype(np.float32) / 255.0
        return np.moveaxis(resized, -1, 0)[None]

    def _estimate_pose(self, session: Any, source: np.ndarray, bbox: tuple[float, float, float, float]) -> np.ndarray:
        left, top, right, bottom = (
            int(np.floor(bbox[0])), int(np.floor(bbox[1])), int(np.ceil(bbox[2])), int(np.ceil(bbox[3]))
        )
        crop = source[top:bottom, left:right]
        if crop.size == 0:
            raise MotionRuntimeError(_RUNTIME_MESSAGE)
        raw = session.estimate(crop) if hasattr(session, "estimate") else self._session_input(session, self._image_tensor(session, crop))
        values = raw if isinstance(raw, (tuple, list)) else (raw,)
        return self._decode_keypoints(values, bbox)

    @staticmethod
    def _decode_keypoints(values: Sequence[Any], bbox: tuple[float, float, float, float]) -> np.ndarray:
        arrays = [np.asarray(value, dtype=np.float32) for value in values]
        if not arrays:
            raise MotionRuntimeError(_RUNTIME_MESSAGE)
        direct = arrays[0]
        while direct.ndim > 2 and direct.shape[0] == 1:
            direct = direct[0]
        if direct.ndim == 2 and direct.shape[1] == 3 and np.isfinite(direct).all():
            return direct.copy()
        if len(arrays) < 2:
            raise MotionRuntimeError(_RUNTIME_MESSAGE)
        x_scores, y_scores = arrays[:2]
        while x_scores.ndim > 2 and x_scores.shape[0] == 1:
            x_scores = x_scores[0]
        while y_scores.ndim > 2 and y_scores.shape[0] == 1:
            y_scores = y_scores[0]
        if x_scores.ndim != 2 or y_scores.ndim != 2 or x_scores.shape[0] != y_scores.shape[0]:
            raise MotionRuntimeError(_RUNTIME_MESSAGE)
        width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x_index, y_index = np.argmax(x_scores, axis=1), np.argmax(y_scores, axis=1)
        confidence = np.minimum(np.max(x_scores, axis=1), np.max(y_scores, axis=1))
        return np.stack((
            bbox[0] + width * x_index / max(1, x_scores.shape[1] - 1),
            bbox[1] + height * y_index / max(1, y_scores.shape[1] - 1),
            confidence,
        ), axis=1).astype(np.float32)

    def _encode(
        self, frames: Sequence[PoseFrame], frame_store: SharedFrameStore, output_path: Path,
        cancelled: Callable[[], bool], reporter: _MonotonicProgress,
    ) -> None:
        total = frame_store.metadata.frame_count

        def rendered() -> Any:
            for index, frame in enumerate(frames):
                self._check_cancelled(cancelled)
                yield render_pose_frame(frame, frame_store.metadata.width, frame_store.metadata.height)
                reporter.report(0.68 + 0.31 * (index + 1) / total)

        self._check_cancelled(cancelled)
        self._encoder(rendered(), frame_store.metadata, Path(output_path), Path(output_path), preserve_audio=False)

    @staticmethod
    def _check_cancelled(cancelled: Callable[[], bool]) -> None:
        if cancelled():
            raise MotionCancelled(_CANCELLED_MESSAGE)
