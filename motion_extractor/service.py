"""Single-worker orchestration for local motion-reference extraction."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
import gc
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Callable
from uuid import uuid4
import warnings

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
_NO_PEOPLE_WARNING = "No people were detected; the pose reference is black."
_CPU_INITIALIZATION_WARNING = "CUDA ONNX provider initialization failed; using CPU fallback."
_CPU_UNAVAILABLE_WARNING = "CUDA ONNX provider was unavailable; rebuilding with the CPU fallback."
_AUDIO_TRANSCODE_WARNING = "Source audio was transcoded to AAC for MP4 compatibility."
_APPROVED_PUBLIC_WARNINGS = frozenset({
    _NO_PEOPLE_WARNING,
    _CPU_INITIALIZATION_WARNING,
    _CPU_UNAVAILABLE_WARNING,
    _AUDIO_TRANSCODE_WARNING,
})
_PROCESS_TIMEOUT_SECONDS = 180
_PROCESS_POLL_SECONDS = 0.01
_PROCESS_STOP_TIMEOUT_SECONDS = 1.0


def _safe_unlink(path: Path | None) -> None:
    if path is None:
        return
    with suppress(OSError):
        path.unlink(missing_ok=True)


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


def _run_mux(command: list[str], cancelled: Callable[[], bool]) -> bool:
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    deadline = time.monotonic() + _PROCESS_TIMEOUT_SECONDS
    try:
        while True:
            if cancelled():
                _stop_process(process)
                raise MotionCancelled(_PUBLIC_CANCELLED)
            if time.monotonic() >= deadline:
                _stop_process(process)
                return False
            return_code = process.poll()
            if return_code is not None:
                return return_code == 0
            time.sleep(_PROCESS_POLL_SECONDS)
    except BaseException:
        _stop_process(process)
        raise


def mux_source_audio(
    video_path: Path,
    source_path: Path,
    destination: Path,
    cancelled: Callable[[], bool] = lambda: False,
) -> bool:
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
        copied = _run_mux(copy_command, cancelled)
        if not copied or not target.is_file() or target.stat().st_size <= 0:
            _safe_unlink(target)
            aac_command = [*common, "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(target)]
            if not _run_mux(aac_command, cancelled) or not target.is_file() or target.stat().st_size <= 0:
                raise MotionMediaError(_PUBLIC_MEDIA_ERROR)
            return True
        return False
    except MotionCancelled:
        _safe_unlink(Path(destination))
        raise
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


@dataclass(frozen=True)
class _ProcessorOutcome:
    result: BranchResult
    warnings: tuple[str, ...]


class MotionTaskService:
    """Own one FIFO worker and path-free public task records."""

    def __init__(
        self,
        output_dir: Path,
        work_dir: Path,
        *,
        depth_factory: Callable[[], Any] = DepthProcessor,
        pose_factory: Callable[[], Any] = PoseProcessor,
        decoder: Callable[[Path, Path, Callable[[], bool]], SharedFrameStore] = decode_video_once,
        prober: Callable[[Path], VideoMetadata] = probe_video,
        audio_muxer: Callable[[Path, Path, Path, Callable[[], bool]], bool] | None = None,
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
                private.done_event.set()
                self._refresh_queue_positions_locked()
                return self._public_copy(record)
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
            terminal_state: str | None = None
            failure: Exception | None = None
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
                terminal_state = await self._run_task(task_id)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                private = self._private.get(task_id)
                terminal_state = "cancelled" if private is not None and private.cancel_event.is_set() else "failed"
                failure = error
            finally:
                private = self._private.get(task_id)
                if private is not None:
                    try:
                        await asyncio.to_thread(self._cleanup_incomplete, private)
                        await asyncio.to_thread(self._cleanup_task_directory, task_id)
                    except Exception as cleanup_error:
                        if terminal_state != "cancelled":
                            terminal_state = "failed"
                            failure = cleanup_error
                    async with self._lock:
                        if terminal_state is not None:
                            self._apply_terminal_locked(
                                self._records[task_id],
                                terminal_state,
                                failure,
                            )
                        private.active = False
                        private.done_event.set()
                        self._refresh_queue_positions_locked()
                self._queue.task_done()

    async def _run_task(self, task_id: str) -> str:
        private = self._private[task_id]
        await self._update(task_id, state="downloading", stage="preparing", progress=0.0)
        self._raise_if_cancelled(private)
        await self._update(task_id, progress=10.0)
        self._raise_if_cancelled(private)
        await self._update(task_id, state="running", stage="decoding", progress=10.0)
        task_work_dir = self.work_dir / task_id
        frame_store = await asyncio.to_thread(
            self._decoder,
            private.source_path,
            task_work_dir,
            private.cancel_event.is_set,
        )
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
                return "cancelled"
            await self._update(task_id, stage="publishing", progress=95.0)
            return await self._derived_terminal_state(task_id)
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
                    outcome = await asyncio.to_thread(
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
                    outcome = await asyncio.to_thread(
                        self._invoke_processor,
                        self._depth_factory,
                        frame_store,
                        temporary_output,
                        progress,
                        private.cancel_event.is_set,
                        392,
                    )
            else:
                outcome = await asyncio.to_thread(
                    self._invoke_processor,
                    self._pose_factory,
                    frame_store,
                    temporary_output,
                    progress,
                    private.cancel_event.is_set,
                    None,
                )
            result = outcome.result
            if not isinstance(result, BranchResult):
                raise MotionRuntimeError(_PUBLIC_RUNTIME_ERROR)
            if result.state == "cancelled" or private.cancel_event.is_set():
                await self._update_branch(task_id, branch, state="cancelled", url=None, error=None)
                return
            if result.state != "completed" or not temporary_output.is_file() or temporary_output.is_symlink():
                raise MotionRuntimeError(_PUBLIC_RUNTIME_ERROR)
            self._raise_if_cancelled(private)
            public_url, audio_transcoded = await self._publish_branch(
                task_id,
                branch,
                frame_store.metadata,
                temporary_output,
            )
            await self._update_branch(task_id, branch, state="completed", url=public_url, error=None)
            for runtime_warning in outcome.warnings:
                await self._append_warning(task_id, self._public_warning(runtime_warning))
            if result.warning:
                await self._append_warning(task_id, self._public_warning(result.warning))
            if audio_transcoded:
                await self._append_warning(task_id, _AUDIO_TRANSCODE_WARNING)
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
            if not temporary_output.exists():
                private.incomplete_paths.discard(temporary_output)

    async def _publish_branch(
        self,
        task_id: str,
        branch: str,
        metadata: VideoMetadata,
        temporary_output: Path,
    ) -> tuple[str, bool]:
        private = self._private[task_id]
        final_name = f"{uuid4().hex}-{branch}.mp4"
        final_path = self.output_dir / final_name
        publication_source = temporary_output
        muxed_output: Path | None = None
        preserve_audio = bool(self._private_preserve_audio(task_id))
        if preserve_audio and metadata.has_audio:
            muxed_output = self.output_dir / f".{uuid4().hex}-{branch}.mux.mp4"
            private.incomplete_paths.add(muxed_output)
            audio_transcoded = bool(await asyncio.to_thread(
                self._audio_muxer,
                temporary_output,
                private.source_path,
                muxed_output,
                private.cancel_event.is_set,
            ))
            if not muxed_output.is_file() or muxed_output.is_symlink():
                raise MotionMediaError(_PUBLIC_MEDIA_ERROR)
            publication_source = muxed_output
        self._raise_if_cancelled(private)
        await asyncio.to_thread(os.replace, publication_source, final_path)
        private.incomplete_paths.discard(publication_source)
        if muxed_output is not None:
            _safe_unlink(temporary_output)
        return f"/assets/output/motion/{final_name}", bool(muxed_output is not None and audio_transcoded)

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
    ) -> _ProcessorOutcome:
        processor = factory()
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                if input_size is None:
                    result = processor.run(frame_store, output_path, progress, cancelled)
                else:
                    result = processor.run(
                        frame_store,
                        output_path,
                        progress,
                        cancelled,
                        input_size=input_size,
                    )
            return _ProcessorOutcome(result, tuple(str(item.message) for item in caught))
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
            if warning not in self._records[task_id]["warnings"]:
                self._records[task_id]["warnings"].append(warning)

    async def _derived_terminal_state(self, task_id: str) -> str:
        async with self._lock:
            record = self._records[task_id]
            enabled_states = [
                record[f"{branch}_state"]
                for branch in _BRANCHES
                if record[f"{branch}_state"] != "disabled"
            ]
            if enabled_states and all(state == "completed" for state in enabled_states):
                return "completed"
            if any(state == "completed" for state in enabled_states):
                return "partial"
            return "failed"

    def _apply_terminal_locked(
        self,
        record: dict[str, Any],
        state: str,
        error: Exception | None,
    ) -> None:
        if state == "cancelled":
            self._cancel_unfinished_branches(record)
        elif state == "failed" and error is not None:
            public_error = self._public_error(error)
            for branch in _BRANCHES:
                if record[f"{branch}_state"] in {"pending", "running"}:
                    record[f"{branch}_state"] = "failed"
                    record[f"{branch}_url"] = None
                    record[f"{branch}_error"] = public_error
        changes = {"state": state, "stage": state, "queue_position": 0}
        if state != "cancelled":
            changes["progress"] = 100.0
        record.update(changes)

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
            try:
                path.unlink(missing_ok=True)
                private.incomplete_paths.discard(path)
            except OSError:
                raise MotionMediaError(_PUBLIC_MEDIA_ERROR) from None

    def _cleanup_task_directory(self, task_id: str) -> None:
        task_directory = self.work_dir / task_id
        try:
            if not task_directory.exists():
                return
            work_root = self.work_dir.resolve(strict=True)
            resolved = task_directory.resolve(strict=True)
            if self.work_dir.is_symlink() or task_directory.is_symlink() or resolved.parent != work_root:
                raise MotionMediaError(_PUBLIC_MEDIA_ERROR)
            shutil.rmtree(resolved)
        except MotionMediaError:
            raise
        except (OSError, ValueError):
            raise MotionMediaError(_PUBLIC_MEDIA_ERROR) from None

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
    def _public_warning(warning: object) -> str:
        text = str(warning or "").strip()
        return text if text in _APPROVED_PUBLIC_WARNINGS else _PUBLIC_WARNING
