"""Safe, local video I/O shared by the motion-reference processors.

This module deliberately knows nothing about models or GPU allocation.  It
validates with FFprobe, decodes each source into one RGB memmap, and encodes
processed frames without retaining a complete output clip in memory.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import suppress
from dataclasses import dataclass, replace
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
from typing import Any, Callable
from uuid import uuid4

import numpy as np

from .errors import (
    MotionCancelled,
    MotionMediaError,
    MotionRuntimeUnavailable,
    MotionValidationError,
)


_MAX_DURATION_SECONDS = 30.0
_MAX_DIMENSION = 4096
_MAX_PIXELS = 3840 * 2160
_MAX_FPS = 60.0
_MAX_FRAMES = 1800
_MAX_RAW_BYTES = 24 * 1024 * 1024 * 1024
_PUBLIC_MEDIA_ERROR = "媒体文件无效或无法读取"
_PUBLIC_VFR_ERROR = (
    "Variable-frame-rate video is unsupported; convert it to constant frame rate before retrying."
)
_PUBLIC_DURATION_ERROR = (
    "Video duration is unavailable; remux or convert the video to constant frame rate before retrying."
)
_PUBLIC_RUNTIME_UNAVAILABLE = "The local FFmpeg runtime is unavailable."
_PROCESS_TIMEOUT_SECONDS = 180
_PROCESS_POLL_SECONDS = 0.01
_PROCESS_STOP_TIMEOUT_SECONDS = 1.0
_MP4_STREAM_COPY_AUDIO_CODECS = frozenset({"aac", "ac3", "alac", "eac3", "mp3"})


def _cleanup_artifact(path: Path | None, *, preserve_failure: bool = False) -> None:
    """Remove one task artifact without allowing its path to escape in errors."""
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        if not preserve_failure:
            raise _media_error() from None


@dataclass(frozen=True)
class VideoMetadata:
    width: int
    height: int
    fps_num: int
    fps_den: int
    frame_count: int
    duration_seconds: float
    rotation: int
    has_audio: bool


@dataclass(frozen=True)
class EncodeResult:
    destination: Path
    audio_transcoded: bool


class SharedFrameStore:
    """One task-scoped RGB24 memmap, removed deterministically on close."""

    def __init__(self, metadata: VideoMetadata, raw_path: Path, frames: np.memmap) -> None:
        self.metadata = metadata
        self.raw_path = raw_path
        self.frames = frames
        self._closed = False

    def __enter__(self) -> "SharedFrameStore":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.frames.flush()
            mapping = getattr(self.frames, "_mmap", None)
            if mapping is not None:
                mapping.close()
        except OSError:
            _cleanup_artifact(self.raw_path, preserve_failure=True)
            raise _media_error() from None
        _cleanup_artifact(self.raw_path)


def _media_error() -> MotionMediaError:
    return MotionMediaError(_PUBLIC_MEDIA_ERROR)


def _required_executable(name: str) -> str:
    executable = shutil.which(name)
    if not executable:
        raise MotionRuntimeUnavailable(_PUBLIC_RUNTIME_UNAVAILABLE)
    return executable


def _safe_readable_file(path: Path) -> Path:
    try:
        candidate = Path(path)
        if candidate.is_symlink() or not candidate.is_file() or not os.access(candidate, os.R_OK):
            raise _media_error()
        return candidate.resolve(strict=True)
    except (OSError, TypeError, ValueError):
        raise _media_error() from None


def _parse_rational(value: object) -> tuple[int, int]:
    if not isinstance(value, str) or value.count("/") != 1:
        raise _media_error()
    numerator_text, denominator_text = value.split("/", 1)
    try:
        numerator, denominator = int(numerator_text), int(denominator_text)
    except ValueError:
        raise _media_error() from None
    if numerator <= 0 or denominator <= 0:
        raise _media_error()
    divisor = math.gcd(numerator, denominator)
    return numerator // divisor, denominator // divisor


def _rotation(stream: dict[str, Any]) -> int:
    raw_rotation: object | None = None
    for side_data in stream.get("side_data_list", ()):
        if isinstance(side_data, dict) and "rotation" in side_data:
            raw_rotation = side_data["rotation"]
            break
    if raw_rotation is None:
        tags = stream.get("tags", {})
        if isinstance(tags, dict):
            raw_rotation = tags.get("rotate")
    try:
        rotation = int(float(raw_rotation or 0)) % 360
    except (TypeError, ValueError):
        raise _media_error() from None
    return rotation if rotation in {0, 90, 180, 270} else 0


def _positive_int(value: object) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise _media_error() from None
    if number <= 0:
        raise _media_error()
    return number


def _optional_frame_count(value: object) -> int:
    if value in (None, "N/A", ""):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _optional_positive_float(value: object) -> float | None:
    if value in (None, "N/A", ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _duration_from_time_base(stream: dict[str, Any]) -> float | None:
    try:
        duration_ts = int(stream.get("duration_ts"))
        time_base_num, time_base_den = _parse_rational(stream.get("time_base"))
    except (MotionMediaError, TypeError, ValueError):
        return None
    if duration_ts <= 0:
        return None
    return _optional_positive_float(duration_ts * time_base_num / time_base_den)


def _video_duration_from_evidence(
    stream: dict[str, Any],
    timestamps: list[float],
    fps_num: int,
    fps_den: int,
    frame_count: int,
) -> float:
    duration = _optional_positive_float(stream.get("duration"))
    if duration is None:
        duration = _duration_from_time_base(stream)
    frame_duration = fps_den / fps_num
    if duration is None and frame_count and len(timestamps) == frame_count:
        duration = _optional_positive_float(frame_count * frame_duration)
    if duration is None and timestamps:
        duration = _optional_positive_float(
            timestamps[-1] - timestamps[0] + frame_duration
        )
    if duration is None:
        raise MotionValidationError(_PUBLIC_DURATION_ERROR)
    return duration


def _ffprobe(path: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [
                _required_executable("ffprobe"),
                "-v", "error",
                "-show_entries",
                "stream=codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate,nb_frames,duration,duration_ts,time_base:stream_tags=rotate:stream_side_data=rotation",
                "-of", "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=_PROCESS_TIMEOUT_SECONDS,
        )
        payload = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, UnicodeDecodeError):
        raise _media_error() from None
    if not isinstance(payload, dict):
        raise _media_error()
    return payload


def _video_timestamps(path: Path) -> list[float]:
    try:
        result = subprocess.run(
            [
                _required_executable("ffprobe"),
                "-v", "error",
                "-select_streams", "v:0",
                "-read_intervals", f"%+#{_MAX_FRAMES + 1}",
                "-show_entries", "frame=best_effort_timestamp_time",
                "-of", "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=_PROCESS_TIMEOUT_SECONDS,
        )
        payload = json.loads(result.stdout)
        frames = payload.get("frames")
        if not isinstance(frames, list):
            raise _media_error()
        timestamps: list[float] = []
        for frame in frames:
            if not isinstance(frame, dict):
                raise _media_error()
            value = float(frame.get("best_effort_timestamp_time"))
            if not math.isfinite(value):
                raise _media_error()
            timestamps.append(value)
        return timestamps
    except MotionRuntimeUnavailable:
        raise
    except (
        OSError,
        subprocess.SubprocessError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        TypeError,
        ValueError,
    ):
        raise _media_error() from None


def _reject_variable_frame_rate(timestamps: list[float], fps_num: int, fps_den: int) -> None:
    if len(timestamps) > _MAX_FRAMES:
        raise _media_error()
    if len(timestamps) < 2:
        return
    expected = fps_den / fps_num
    tolerance = max(0.001, expected * 0.05)
    deltas = [later - earlier for earlier, later in zip(timestamps, timestamps[1:])]
    if any(delta <= 0 or abs(delta - expected) > tolerance for delta in deltas):
        raise MotionValidationError(_PUBLIC_VFR_ERROR)


def probe_video(path: Path) -> VideoMetadata:
    """Return validated display metadata without decoding any frames."""
    source = _safe_readable_file(path)
    payload = _ffprobe(source)
    streams = payload.get("streams")
    if not isinstance(streams, list):
        raise _media_error()
    video_stream = next(
        (stream for stream in streams if isinstance(stream, dict) and stream.get("codec_type") == "video"),
        None,
    )
    if video_stream is None:
        raise _media_error()
    encoded_width = _positive_int(video_stream.get("width"))
    encoded_height = _positive_int(video_stream.get("height"))
    fps_value = video_stream.get("avg_frame_rate")
    if fps_value in (None, "0/0"):
        fps_value = video_stream.get("r_frame_rate")
    fps_num, fps_den = _parse_rational(fps_value)
    fps = fps_num / fps_den
    rotation = _rotation(video_stream)
    width, height = (encoded_height, encoded_width) if rotation in {90, 270} else (encoded_width, encoded_height)
    frame_count = _optional_frame_count(video_stream.get("nb_frames"))
    if (
        encoded_width % 2
        or encoded_height % 2
        or width > _MAX_DIMENSION
        or height > _MAX_DIMENSION
        or width * height > _MAX_PIXELS
        or fps > _MAX_FPS
        or frame_count > _MAX_FRAMES
        or width * height * 3 * frame_count > _MAX_RAW_BYTES
    ):
        raise _media_error()
    timestamps = _video_timestamps(source)
    _reject_variable_frame_rate(timestamps, fps_num, fps_den)
    duration = _video_duration_from_evidence(
        video_stream,
        timestamps,
        fps_num,
        fps_den,
        frame_count,
    )
    if not math.isfinite(duration) or duration <= 0 or duration > _MAX_DURATION_SECONDS:
        raise _media_error()
    expected_frames = max(frame_count, math.ceil(duration * fps))
    if (
        expected_frames > _MAX_FRAMES
        or width * height * 3 * expected_frames > _MAX_RAW_BYTES
    ):
        raise _media_error()
    return VideoMetadata(
        width=width,
        height=height,
        fps_num=fps_num,
        fps_den=fps_den,
        frame_count=frame_count,
        duration_seconds=duration,
        rotation=rotation,
        has_audio=any(
            isinstance(stream, dict) and stream.get("codec_type") == "audio" for stream in streams
        ),
    )


def _safe_work_directory(work_dir: Path) -> Path:
    try:
        directory = Path(work_dir)
        directory.mkdir(parents=True, exist_ok=True)
        if directory.is_symlink() or not directory.is_dir():
            raise _media_error()
        return directory.resolve(strict=True)
    except (OSError, TypeError, ValueError):
        raise _media_error() from None


def _stop_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    with suppress(OSError):
        process.terminate()
    try:
        process.wait(timeout=_PROCESS_STOP_TIMEOUT_SECONDS)
        return
    except (OSError, subprocess.TimeoutExpired):
        pass
    with suppress(OSError):
        process.kill()
    with suppress(OSError, subprocess.TimeoutExpired):
        process.wait(timeout=_PROCESS_STOP_TIMEOUT_SECONDS)


def _decode_to_raw(command: list[str], cancelled: Callable[[], bool]) -> None:
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        raise _media_error() from None
    deadline = time.monotonic() + _PROCESS_TIMEOUT_SECONDS
    try:
        while True:
            if cancelled():
                _stop_process(process)
                raise MotionCancelled("Task cancelled.")
            if time.monotonic() >= deadline:
                _stop_process(process)
                raise _media_error()
            return_code = process.poll()
            if return_code is not None:
                if return_code != 0:
                    raise _media_error()
                return
            time.sleep(_PROCESS_POLL_SECONDS)
    except BaseException:
        _stop_process(process)
        raise


def decode_video_once(
    path: Path,
    work_dir: Path,
    cancelled: Callable[[], bool] = lambda: False,
) -> SharedFrameStore:
    """Decode a source once into a display-oriented, task-local RGB memmap."""
    source = _safe_readable_file(path)
    metadata = probe_video(source)
    directory = _safe_work_directory(work_dir)
    raw_path = directory / f"motion-frames-{uuid4().hex}.rgb"
    frame_bytes = metadata.width * metadata.height * 3
    try:
        _decode_to_raw(
            [
                _required_executable("ffmpeg"),
                "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(source), "-map", "0:v:0", "-an", "-sn", "-dn",
                "-t", str(_MAX_DURATION_SECONDS + (metadata.fps_den / metadata.fps_num)),
                "-frames:v", str(_MAX_FRAMES + 1),
                "-fs", str(_MAX_RAW_BYTES),
                "-pix_fmt", "rgb24", "-f", "rawvideo", str(raw_path),
            ],
            cancelled,
        )
        byte_count = raw_path.stat().st_size
        if byte_count == 0 or byte_count % frame_bytes:
            raise _media_error()
        frame_count = byte_count // frame_bytes
        actual_duration = frame_count * metadata.fps_den / metadata.fps_num
        timing_tolerance = max(0.25, 2 * metadata.fps_den / metadata.fps_num)
        if (
            frame_count <= 0
            or frame_count > _MAX_FRAMES
            or byte_count > _MAX_RAW_BYTES
            or actual_duration > _MAX_DURATION_SECONDS + timing_tolerance
            or abs(actual_duration - metadata.duration_seconds) > timing_tolerance
            or (
                metadata.frame_count
                and abs(frame_count - metadata.frame_count) > 1
            )
        ):
            raise _media_error()
        actual_metadata = replace(metadata, frame_count=frame_count)
        frames = np.memmap(
            raw_path,
            mode="r+",
            dtype=np.uint8,
            shape=(frame_count, metadata.height, metadata.width, 3),
        )
        return SharedFrameStore(actual_metadata, raw_path, frames)
    except (MotionCancelled, MotionMediaError):
        _cleanup_artifact(raw_path, preserve_failure=True)
        raise
    except (OSError, subprocess.SubprocessError, ValueError):
        _cleanup_artifact(raw_path, preserve_failure=True)
        raise _media_error() from None


def _frame_bytes(frame: np.ndarray, metadata: VideoMetadata) -> bytes:
    array = np.asarray(frame)
    if array.dtype != np.uint8 or array.shape != (metadata.height, metadata.width, 3):
        raise _media_error()
    return np.ascontiguousarray(array).tobytes()


def _spool_frames(frames: Iterable[np.ndarray], metadata: VideoMetadata, spool_path: Path) -> None:
    count = 0
    try:
        with spool_path.open("xb") as handle:
            for frame in frames:
                handle.write(_frame_bytes(frame, metadata))
                count += 1
    except (OSError, ValueError, TypeError, MotionMediaError):
        _cleanup_artifact(spool_path, preserve_failure=True)
        raise _media_error() from None
    if count != metadata.frame_count:
        _cleanup_artifact(spool_path, preserve_failure=True)
        raise _media_error()


def _spooled_frames(spool_path: Path, metadata: VideoMetadata) -> Iterator[np.ndarray]:
    frame_size = metadata.width * metadata.height * 3
    with spool_path.open("rb") as handle:
        while data := handle.read(frame_size):
            if len(data) != frame_size:
                raise _media_error()
            yield np.frombuffer(data, dtype=np.uint8).reshape(metadata.height, metadata.width, 3)


def _close_process_pipe(pipe: object | None) -> None:
    try:
        if pipe is not None:
            pipe.close()
    except OSError:
        pass


def _encode_attempt(
    frames: Iterable[np.ndarray],
    metadata: VideoMetadata,
    temporary_destination: Path,
    source_path: Path | None,
    copy_audio: bool,
    cancelled: Callable[[], bool],
) -> bool:
    command = [
        _required_executable("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pixel_format", "rgb24",
        "-video_size", f"{metadata.width}x{metadata.height}",
        "-framerate", f"{metadata.fps_num}/{metadata.fps_den}", "-i", "pipe:0",
    ]
    if source_path is not None:
        command.extend(["-i", str(source_path), "-map", "0:v:0", "-map", "1:a:0?"])
    else:
        command.extend(["-map", "0:v:0"])
    command.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p"])
    if source_path is not None:
        command.extend(["-c:a", "copy" if copy_audio else "aac"])
    command.extend(["-movflags", "+faststart", str(temporary_destination)])
    state: dict[str, Any] = {"count": 0, "error": None, "stderr": False}
    feeder_done = threading.Event()
    stderr_done = threading.Event()
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        assert process.stdin is not None

        def feed() -> None:
            try:
                for frame in frames:
                    if cancelled():
                        raise MotionCancelled("Task cancelled.")
                    process.stdin.write(_frame_bytes(frame, metadata))
                    state["count"] += 1
            except BaseException as error:
                state["error"] = error
            finally:
                _close_process_pipe(process.stdin)
                feeder_done.set()

        def drain_stderr() -> None:
            try:
                if process.stderr is not None:
                    while chunk := process.stderr.read(64 * 1024):
                        state["stderr"] = True
            except (OSError, ValueError):
                pass
            finally:
                _close_process_pipe(process.stderr)
                stderr_done.set()

        feeder = threading.Thread(target=feed, name="motion-ffmpeg-feeder", daemon=True)
        drainer = threading.Thread(target=drain_stderr, name="motion-ffmpeg-stderr", daemon=True)
        feeder.start()
        drainer.start()
        deadline = time.monotonic() + _PROCESS_TIMEOUT_SECONDS
        cancellation_requested = False
        timed_out = False
        while process.poll() is None:
            if cancelled():
                cancellation_requested = True
                break
            if time.monotonic() >= deadline:
                timed_out = True
                break
            if feeder_done.is_set() and state["error"] is not None:
                break
            time.sleep(_PROCESS_POLL_SECONDS)
        if cancellation_requested or timed_out or state["error"] is not None:
            _stop_process(process)
        _close_process_pipe(process.stdin)
        _close_process_pipe(process.stderr)
        feeder.join(timeout=_PROCESS_STOP_TIMEOUT_SECONDS)
        drainer.join(timeout=_PROCESS_STOP_TIMEOUT_SECONDS)
        if feeder.is_alive() or drainer.is_alive():
            _stop_process(process)
            _close_process_pipe(process.stdin)
            _close_process_pipe(process.stderr)
            feeder.join(timeout=_PROCESS_STOP_TIMEOUT_SECONDS)
            drainer.join(timeout=_PROCESS_STOP_TIMEOUT_SECONDS)
        if cancellation_requested or isinstance(state["error"], MotionCancelled):
            raise MotionCancelled("Task cancelled.")
        if (
            timed_out
            or feeder.is_alive()
            or drainer.is_alive()
            or state["error"] is not None
            or process.poll() != 0
            or state["stderr"]
        ):
            return False
        return state["count"] == metadata.frame_count
    except (OSError, subprocess.SubprocessError, ValueError, MotionMediaError):
        if "process" in locals():
            _stop_process(process)
        if "process" in locals():
            _close_process_pipe(process.stdin)
            _close_process_pipe(process.stderr)
        return False


def _audio_codec(path: Path) -> str | None:
    payload = _ffprobe(path)
    streams = payload.get("streams", ())
    if not isinstance(streams, list):
        return None
    for stream in streams:
        if isinstance(stream, dict) and stream.get("codec_type") == "audio":
            codec = stream.get("codec_name")
            return codec if isinstance(codec, str) else None
    return None


def encode_rgb_frames(
    frames: Iterable[np.ndarray],
    metadata: VideoMetadata,
    destination: Path,
    source_path: Path,
    preserve_audio: bool,
    cancelled: Callable[[], bool] = lambda: False,
) -> EncodeResult:
    """Stream RGB frames to an atomically-published H.264 MP4."""
    try:
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.is_symlink():
            raise _media_error()
        parent = target.parent.resolve(strict=True)
    except (OSError, TypeError, ValueError):
        raise _media_error() from None
    temporary_destination = parent / f".{target.name}.{uuid4().hex}.tmp.mp4"
    audio_source = _safe_readable_file(source_path) if preserve_audio and metadata.has_audio else None
    spool_path = parent / f".motion-encode-{uuid4().hex}.rgb" if audio_source else None
    try:
        if spool_path is None:
            if not _encode_attempt(
                frames, metadata, temporary_destination, None, copy_audio=False, cancelled=cancelled
            ):
                raise _media_error()
            audio_transcoded = False
        else:
            _spool_frames(frames, metadata, spool_path)
            copied = _encode_attempt(
                _spooled_frames(spool_path, metadata), metadata, temporary_destination, audio_source,
                copy_audio=True, cancelled=cancelled,
            )
            copied_codec = _audio_codec(temporary_destination) if copied else None
            if copied and copied_codec in _MP4_STREAM_COPY_AUDIO_CODECS:
                audio_transcoded = False
            else:
                _cleanup_artifact(temporary_destination)
                if not _encode_attempt(
                    _spooled_frames(spool_path, metadata), metadata, temporary_destination, audio_source,
                    copy_audio=False, cancelled=cancelled,
                ):
                    raise _media_error()
                audio_transcoded = True
        _cleanup_artifact(spool_path)
        spool_path = None
        os.replace(temporary_destination, target)
        return EncodeResult(destination=target, audio_transcoded=audio_transcoded)
    except MotionCancelled:
        _cleanup_artifact(temporary_destination, preserve_failure=True)
        _cleanup_artifact(spool_path, preserve_failure=True)
        raise
    except (OSError, MotionMediaError):
        _cleanup_artifact(temporary_destination, preserve_failure=True)
        _cleanup_artifact(spool_path, preserve_failure=True)
        raise _media_error() from None
