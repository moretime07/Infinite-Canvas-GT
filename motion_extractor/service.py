"""Single-worker orchestration for local motion-reference extraction."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
import gc
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
from typing import Any, Callable
from uuid import uuid4

from .depth import BranchResult, DepthProcessor
from .errors import (
    MotionCancelled,
    MotionError,
    MotionMediaError,
    MotionOutOfMemory,
    MotionRuntimeError,
    MotionValidationError,
)
from .media import SharedFrameStore, VideoMetadata, decode_video_once, probe_video
from .pose import PoseProcessor


_TERMINAL_STATES = frozenset({"partial", "completed", "failed", "cancelled"})
_BRANCHES = ("depth", "pose")
_PUBLIC_MEDIA_ERROR = "The video could not be processed."
_PUBLIC_RUNTIME_ERROR = "Local motion processing failed."
_PUBLIC_OOM_ERROR = "Insufficient local GPU memory."
_PUBLIC_CANCELLED = "Task cancelled."
_PUBLIC_WARNING = "Motion processing completed with a warning."
_PROCESS_TIMEOUT_SECONDS = 180
_WINDOWS_PATH = re.compile(r"""(?i)(?:^|[\s"'(])(?:[a-z]:[\\/]|\\\\)""")


def _safe_unlink(path: Path | None) -> None:
    if path is None:
        return
    with suppress(OSError):
        path.unlink(missing_ok=True)


def _run_mux(command: list[str]) -> bool:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            timeout=_PROCESS_TIMEOUT_SECONDS,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def mux_source_audio(video_path: Path, source_path: Path, destination: Path) -> None:
    """Mux source audio onto an encoded branch without decoding its video stream."""
    executable = shutil.which("ffmpeg")
    try:
        encoded = Path(video_path)
        source = Path(source_path)
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        if (
            executable is None
            or encoded.is_symlink()
            or source.is_symlink()
            or not encoded.is_file()
            or not source.is_file()
            or target.is_symlink()
        ):
            raise MotionMediaError(_PUBLIC_MEDIA_ERROR)
        common = [
            executable,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(encoded),
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
        ]
        copy_command = [*common, "-c:a", "copy", "-shortest", "-movflags", "+faststart", str(target)]
        copied = _run_mux(copy_command)
        if not copied or not target.is_file() or target.stat().st_size <= 0:
            _safe_unlink(target)
            aac_command = [*common, "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(target)]
            if not _run_mux(aac_command) or not target.is_file() or target.stat().st_size <= 0:
                raise MotionMediaError(_PUBLIC_MEDIA_ERROR)
    except MotionMediaError:
        _safe_unlink(Path(destination))
        raise
    except (OSError, TypeError, ValueError):
        _safe_unlink(Path(destination))
        raise MotionMediaError(_PUBLIC_MEDIA_ERROR) from None


@dataclass
class _PrivateTask:
    source_path: Path
    preserve_audio: bool = False
    cancel_event: threading.Event = field(default_factory=threading.Event)
    done_event: asyncio.Event = field(default_factory=asyncio.Event)
    active: bool = False
    incomplete_paths: set[Path] = field(default_factory=set)


class MotionTaskService:
    """Own one FIFO worker and path-free public task records."""

    def __init__(
        self,
        output_dir: Path,
        work_dir: Path,
        *,
        depth_factory: Callable[[], Any] = DepthProcessor,
        pose_factory: Callable[[], Any] = PoseProcessor,
        decoder: Callable[[Path, Path], SharedFrameStore] = decode_video_once,
        prober: Callable[[Path], VideoMetadata] = probe_video,
        audio_muxer: Callable[[Path, Path, Path], None] | None = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.work_dir = Path(work_dir)
        self._depth_factory = depth_factory
        self._pose_factory = pose_factory
        self._decoder = decoder
        self._prober = prober
        self._audio_muxer = audio_muxer or mux_source_audio
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._records: dict[str, dict[str, Any]] = {}
        self._private: dict[str, _PrivateTask] = {}
        self._lock = asyncio.Lock()

    def preflight(self, source_path: Path) -> VideoMetadata:
        """Synchronously validate local media before it can enter the GPU queue."""
        return self._prober(Path(source_path))

    async def submit(
        self,
        source_url: str,
        source_path: Path,
        depth_enabled: bool,
        pose_enabled: bool,
        preserve_audio: bool,
    ) -> dict[str, Any]:
        if not depth_enabled and not pose_enabled:
            raise MotionValidationError("At least one motion processor must be enabled.")
        safe_source_url = self._safe_source_url(source_url)
        task_id = f"canvas_motion_{uuid4().hex}"
        async with self._lock:
            queue_position = 1 + sum(
                record["state"] == "queued" for record in self._records.values()
            )
            record = {
                "task_id": task_id,
                "source_url": safe_source_url,
                "state": "queued",
                "stage": "queued",
                "progress": 0.0,
                "queue_position": queue_position,
                "depth_state": "pending" if depth_enabled else "disabled",
                "depth_url": None,
                "depth_error": None,
                "pose_state": "pending" if pose_enabled else "disabled",
                "pose_url": None,
                "pose_error": None,
                "warnings": [],
                "low_memory_retry": False,
            }
            self._records[task_id] = record
            self._private[task_id] = _PrivateTask(Path(source_path), bool(preserve_audio))
            public_record = self._public_copy(record)
        await self._queue.put(task_id)
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._worker_loop(), name="canvas-motion-worker")
        return public_record

    async def get(self, task_id: str) -> dict[str, Any] | None:
        async with self._lock:
            record = self._records.get(task_id)
            return self._public_copy(record) if record is not None else None

    async def cancel(self, task_id: str) -> dict[str, Any] | None:
        async with self._lock:
            record = self._records.get(task_id)
            private = self._private.get(task_id)
            if record is None or private is None:
                return None
            if record["state"] in _TERMINAL_STATES:
                return self._public_copy(record)
            private.cancel_event.set()
            if record["state"] == "queued":
                record.update({
                    "state": "cancelled",
                    "stage": "cancelled",
                    "queue_position": 0,
                })
                self._cancel_unfinished_branches(record)
            done_event = private.done_event
        await done_event.wait()
        return await self.get(task_id)

    async def close(self) -> None:
        """Stop the lazy worker; intended for application/test lifecycle cleanup."""
        async with self._lock:
            private_tasks = list(self._private.values())
            for task_id, private in self._private.items():
                record = self._records[task_id]
                if record["state"] not in _TERMINAL_STATES:
                    private.cancel_event.set()
                    if record["state"] == "queued":
                        record.update({"state": "cancelled", "stage": "cancelled", "queue_position": 0})
                        self._cancel_unfinished_branches(record)
        if self._worker is not None and not self._worker.done():
            if private_tasks:
                await self._queue.join()
            self._worker.cancel()
            with suppress(asyncio.CancelledError):
                await self._worker
        self._worker = None

    async def _worker_loop(self) -> None:
        while True:
            task_id = await self._queue.get()
            try:
                async with self._lock:
                    record = self._records[task_id]
                    private = self._private[task_id]
                    if record["state"] == "cancelled":
                        private.done_event.set()
                        continue
                    private.active = True
                    record["queue_position"] = 0
                    self._refresh_queue_positions_locked()
                await self._run_task(task_id)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                await self._fail_task(task_id, error)
            finally:
                private = self._private.get(task_id)
                if private is not None:
                    await asyncio.to_thread(self._cleanup_incomplete, private)
                    await asyncio.to_thread(self._cleanup_task_directory, task_id)
                    async with self._lock:
                        private.active = False
                        private.done_event.set()
                        self._refresh_queue_positions_locked()
                self._queue.task_done()

    async def _run_task(self, task_id: str) -> None:
        private = self._private[task_id]
        await self._update(task_id, state="downloading", stage="preparing", progress=0.0)
        self._raise_if_cancelled(private)
        await self._update(task_id, progress=10.0)
        self._raise_if_cancelled(private)
        await self._update(task_id, state="running", stage="decoding", progress=10.0)
        task_work_dir = self.work_dir / task_id
        frame_store = await asyncio.to_thread(self._decoder, private.source_path, task_work_dir)
        entered_store = frame_store
        has_context = hasattr(frame_store, "__enter__") and hasattr(frame_store, "__exit__")
        if has_context:
            entered_store = await asyncio.to_thread(frame_store.__enter__)
        try:
            self._raise_if_cancelled(private)
            await self._update(task_id, progress=20.0)
            record = await self.get(task_id)
            assert record is not None
            enabled = [branch for branch in _BRANCHES if record[f"{branch}_state"] != "disabled"]
            share = 65.0 / len(enabled)
            for index, branch in enumerate(enabled):
                if private.cancel_event.is_set():
                    break
                await self._run_branch(
                    task_id,
                    branch,
                    entered_store,
                    20.0 + share * index,
                    20.0 + share * (index + 1),
                )
            if private.cancel_event.is_set():
                await self._mark_cancelled(task_id)
            else:
                await self._update(task_id, stage="publishing", progress=95.0)
                await self._finalize_task(task_id)
        finally:
            if has_context:
                await asyncio.to_thread(frame_store.__exit__, None, None, None)

    async def _run_branch(
        self,
        task_id: str,
        branch: str,
        frame_store: SharedFrameStore,
        progress_start: float,
        progress_end: float,
    ) -> None:
        private = self._private[task_id]
        await self._update_branch(task_id, branch, state="running", error=None)
        await self._update(task_id, stage=branch)
        loop = asyncio.get_running_loop()

        def progress(value: float) -> None:
            normalized = max(0.0, min(1.0, float(value)))
            overall = progress_start + (progress_end - progress_start) * normalized
            loop.call_soon_threadsafe(
                asyncio.create_task,
                self._set_branch_progress(task_id, branch, overall),
            )

        temporary_output = self.output_dir / f".{uuid4().hex}-{branch}.work.mp4"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        private.incomplete_paths.add(temporary_output)
        try:
            if branch == "depth":
                try:
                    result = await asyncio.to_thread(
                        self._invoke_processor,
                        self._depth_factory,
                        frame_store,
                        temporary_output,
                        progress,
                        private.cancel_event.is_set,
                        518,
                    )
                except MotionOutOfMemory:
                    await self._update(task_id, low_memory_retry=True)
                    _safe_unlink(temporary_output)
                    self._raise_if_cancelled(private)
                    result = await asyncio.to_thread(
                        self._invoke_processor,
                        self._depth_factory,
                        frame_store,
                        temporary_output,
                        progress,
                        private.cancel_event.is_set,
                        392,
                    )
            else:
                result = await asyncio.to_thread(
                    self._invoke_processor,
                    self._pose_factory,
                    frame_store,
                    temporary_output,
                    progress,
                    private.cancel_event.is_set,
                    None,
                )
            if not isinstance(result, BranchResult):
                raise MotionRuntimeError(_PUBLIC_RUNTIME_ERROR)
            if result.state == "cancelled" or private.cancel_event.is_set():
                await self._update_branch(task_id, branch, state="cancelled", url=None, error=None)
                return
            if result.state != "completed" or not temporary_output.is_file() or temporary_output.is_symlink():
                raise MotionRuntimeError(_PUBLIC_RUNTIME_ERROR)
            self._raise_if_cancelled(private)
            public_url = await self._publish_branch(task_id, branch, frame_store.metadata, temporary_output)
            await self._update_branch(task_id, branch, state="completed", url=public_url, error=None)
            if result.warning:
                await self._append_warning(task_id, self._sanitize_warning(result.warning))
            await self._update(task_id, progress=progress_end)
        except MotionCancelled:
            await self._update_branch(task_id, branch, state="cancelled", url=None, error=None)
        except Exception as error:
            await self._update_branch(
                task_id,
                branch,
                state="failed",
                url=None,
                error=self._public_error(error),
            )
        finally:
            _safe_unlink(temporary_output)
            private.incomplete_paths.discard(temporary_output)

    async def _publish_branch(
        self,
        task_id: str,
        branch: str,
        metadata: VideoMetadata,
        temporary_output: Path,
    ) -> str:
        private = self._private[task_id]
        final_name = f"{uuid4().hex}-{branch}.mp4"
        final_path = self.output_dir / final_name
        publication_source = temporary_output
        muxed_output: Path | None = None
        preserve_audio = bool(self._private_preserve_audio(task_id))
        if preserve_audio and metadata.has_audio:
            muxed_output = self.output_dir / f".{uuid4().hex}-{branch}.mux.mp4"
            private.incomplete_paths.add(muxed_output)
            await asyncio.to_thread(
                self._audio_muxer,
                temporary_output,
                private.source_path,
                muxed_output,
            )
            if not muxed_output.is_file() or muxed_output.is_symlink():
                raise MotionMediaError(_PUBLIC_MEDIA_ERROR)
            publication_source = muxed_output
        self._raise_if_cancelled(private)
        await asyncio.to_thread(os.replace, publication_source, final_path)
        private.incomplete_paths.discard(publication_source)
        if muxed_output is not None:
            _safe_unlink(temporary_output)
        return f"/assets/output/motion/{final_name}"

    def _private_preserve_audio(self, task_id: str) -> bool:
        return self._private[task_id].preserve_audio

    @staticmethod
    def _invoke_processor(
        factory: Callable[[], Any],
        frame_store: SharedFrameStore,
        output_path: Path,
        progress: Callable[[float], None],
        cancelled: Callable[[], bool],
        input_size: int | None,
    ) -> BranchResult:
        processor = factory()
        try:
            if input_size is None:
                return processor.run(frame_store, output_path, progress, cancelled)
            return processor.run(frame_store, output_path, progress, cancelled, input_size=input_size)
        finally:
            for method_name in ("close", "release"):
                method = getattr(processor, method_name, None)
                if callable(method):
                    with suppress(Exception):
                        method()
            del processor
            gc.collect()
            torch_module = sys.modules.get("torch")
            cuda = getattr(torch_module, "cuda", None)
            empty_cache = getattr(cuda, "empty_cache", None)
            if callable(empty_cache):
                with suppress(Exception):
                    empty_cache()

    async def _set_branch_progress(self, task_id: str, branch: str, progress: float) -> None:
        async with self._lock:
            record = self._records.get(task_id)
            if record is None or record["state"] in _TERMINAL_STATES:
                return
            if record[f"{branch}_state"] == "running":
                record["progress"] = max(float(record["progress"]), min(100.0, progress))

    async def _update(self, task_id: str, **changes: Any) -> None:
        async with self._lock:
            record = self._records[task_id]
            if "progress" in changes:
                changes["progress"] = max(float(record["progress"]), min(100.0, float(changes["progress"])))
            record.update(changes)

    async def _update_branch(
        self,
        task_id: str,
        branch: str,
        *,
        state: str,
        url: str | None = None,
        error: str | None = None,
    ) -> None:
        async with self._lock:
            record = self._records[task_id]
            record[f"{branch}_state"] = state
            record[f"{branch}_url"] = url
            record[f"{branch}_error"] = error

    async def _append_warning(self, task_id: str, warning: str) -> None:
        async with self._lock:
            self._records[task_id]["warnings"].append(warning)

    async def _finalize_task(self, task_id: str) -> None:
        async with self._lock:
            record = self._records[task_id]
            enabled_states = [
                record[f"{branch}_state"]
                for branch in _BRANCHES
                if record[f"{branch}_state"] != "disabled"
            ]
            if enabled_states and all(state == "completed" for state in enabled_states):
                state = "completed"
            elif any(state == "completed" for state in enabled_states):
                state = "partial"
            else:
                state = "failed"
            record.update({"state": state, "stage": state, "progress": 100.0, "queue_position": 0})

    async def _mark_cancelled(self, task_id: str) -> None:
        async with self._lock:
            record = self._records[task_id]
            self._cancel_unfinished_branches(record)
            record.update({"state": "cancelled", "stage": "cancelled", "queue_position": 0})

    async def _fail_task(self, task_id: str, error: Exception) -> None:
        async with self._lock:
            record = self._records.get(task_id)
            private = self._private.get(task_id)
            if record is None:
                return
            if private is not None and private.cancel_event.is_set():
                self._cancel_unfinished_branches(record)
                record.update({"state": "cancelled", "stage": "cancelled", "queue_position": 0})
                return
            public_error = self._public_error(error)
            for branch in _BRANCHES:
                if record[f"{branch}_state"] in {"pending", "running"}:
                    record[f"{branch}_state"] = "failed"
                    record[f"{branch}_url"] = None
                    record[f"{branch}_error"] = public_error
            record.update({"state": "failed", "stage": "failed", "queue_position": 0})

    def _refresh_queue_positions_locked(self) -> None:
        position = 1
        for record in self._records.values():
            if record["state"] == "queued":
                record["queue_position"] = position
                position += 1

    @staticmethod
    def _cancel_unfinished_branches(record: dict[str, Any]) -> None:
        for branch in _BRANCHES:
            if record[f"{branch}_state"] in {"pending", "running"}:
                record[f"{branch}_state"] = "cancelled"
                record[f"{branch}_url"] = None
                record[f"{branch}_error"] = None

    @staticmethod
    def _raise_if_cancelled(private: _PrivateTask) -> None:
        if private.cancel_event.is_set():
            raise MotionCancelled(_PUBLIC_CANCELLED)

    @staticmethod
    def _cleanup_incomplete(private: _PrivateTask) -> None:
        for path in tuple(private.incomplete_paths):
            _safe_unlink(path)
            private.incomplete_paths.discard(path)

    def _cleanup_task_directory(self, task_id: str) -> None:
        task_directory = self.work_dir / task_id
        with suppress(OSError):
            task_directory.rmdir()

    @staticmethod
    def _safe_source_url(source_url: str) -> str:
        safe = str(source_url or "").split("?", 1)[0].replace("\\", "/")
        if not safe.startswith(("/assets/", "/output/")):
            raise MotionValidationError("A local application video URL is required.")
        return safe

    @staticmethod
    def _public_copy(record: dict[str, Any]) -> dict[str, Any]:
        return {
            key: list(value) if key == "warnings" else value
            for key, value in record.items()
            if not key.startswith("_")
        }

    @staticmethod
    def _public_error(error: Exception) -> str:
        if isinstance(error, MotionOutOfMemory):
            return _PUBLIC_OOM_ERROR
        if isinstance(error, MotionMediaError):
            return _PUBLIC_MEDIA_ERROR
        if isinstance(error, MotionCancelled):
            return _PUBLIC_CANCELLED
        if isinstance(error, (MotionRuntimeError, MotionError)):
            return _PUBLIC_RUNTIME_ERROR
        return _PUBLIC_RUNTIME_ERROR

    @staticmethod
    def _sanitize_warning(warning: object) -> str:
        text = str(warning or "").strip()
        lowered = text.lower()
        unsafe_markers = (
            "traceback", "sk-", "api_key", "apikey", "secret", "token=", "bearer ",
            "/users/", "/home/",
        )
        if (
            not text
            or len(text) > 240
            or _WINDOWS_PATH.search(text)
            or any(marker in lowered for marker in unsafe_markers)
        ):
            return _PUBLIC_WARNING
        return text
