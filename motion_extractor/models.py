"""Verified local artifacts for the optional motion extraction runtime."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import os
import shutil
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator, Mapping, TypeVar
from uuid import uuid4

try:
    from huggingface_hub import hf_hub_download
except ImportError:  # The base application intentionally does not require it.
    hf_hub_download = None


@dataclass(frozen=True)
class ModelArtifact:
    repo_id: str
    filename: str
    sha256: str


@dataclass(frozen=True)
class GitSource:
    name: str
    url: str
    commit: str


@dataclass(frozen=True)
class MotionRuntimeStatus:
    ready: bool
    cuda_available: bool
    onnx_cuda_available: bool
    missing_packages: tuple[str, ...]
    missing_models: tuple[str, ...]

    def to_dict(self) -> dict[str, bool | list[str]]:
        """Return a UI-safe status payload with no local filesystem paths."""
        return {
            "ready": self.ready,
            "cuda_available": self.cuda_available,
            "onnx_cuda_available": self.onnx_cuda_available,
            "missing_packages": list(self.missing_packages),
            "missing_models": list(self.missing_models),
        }


class MotionAssetError(RuntimeError):
    """Base error for optional motion-runtime assets."""


class MotionCancelled(MotionAssetError):
    """Raised when a caller cancels model preparation."""


class MotionIntegrityError(MotionAssetError):
    """Raised when a downloaded artifact does not match its manifest hash."""


class MotionSourceError(MotionAssetError):
    """Raised when a source checkout is not the required immutable revision."""


class MotionDependencyError(MotionAssetError):
    """Raised when the optional model downloader is not installed."""


VIDEO_DEPTH_ANYTHING_SOURCE = GitSource(
    name="video-depth-anything",
    url="https://github.com/DepthAnything/Video-Depth-Anything.git",
    commit="4f5ae23172ba60fd7bc11ef671cca678842c7072",
)
DWPOSE_SOURCE = GitSource(
    name="dwpose",
    url="https://github.com/IDEA-Research/DWPose.git",
    commit="3dca5db79d9f9ffdd378753ddf6ec66535aace88",
)
GIT_SOURCES = (VIDEO_DEPTH_ANYTHING_SOURCE, DWPOSE_SOURCE)

MODEL_ARTIFACTS = (
    ModelArtifact(
        repo_id="depth-anything/Video-Depth-Anything-Small",
        filename="video_depth_anything_vits.pth",
        sha256="13379300b739e659f076a59d52e9801bd8d38c541a7e71f73bbca4dcfb013609",
    ),
    ModelArtifact(
        repo_id="yzd-v/DWPose",
        filename="dw-ll_ucoco_384.onnx",
        sha256="724f4ff2439ed61afb86fb8a1951ec39c6220682803b4a8bd4f598cd913b1843",
    ),
    ModelArtifact(
        repo_id="yzd-v/DWPose",
        filename="yolox_l.onnx",
        sha256="7860ae79de6c89a3c1eb72ae9a2756c0ccfbe04b7791bb5880afabd97855a411",
    ),
)

_PACKAGE_MODULES = {
    "torch": "torch",
    "opencv-python-headless": "cv2",
    "onnxruntime-gpu": "onnxruntime",
    "huggingface-hub": "huggingface_hub",
    "imageio": "imageio",
    "imageio-ffmpeg": "imageio_ffmpeg",
    "einops": "einops",
    "easydict": "easydict",
    "tqdm": "tqdm",
}
_HASH_CHUNK_SIZE = 1024 * 1024
_GIT_TIMEOUT_SECONDS = 180
_HF_ETAG_TIMEOUT_SECONDS = 10
_HF_REQUEST_TIMEOUT_SECONDS = 30
_HF_TOTAL_TIMEOUT_SECONDS = 1800
_PROCESS_POLL_SECONDS = 0.1
_PROCESS_STOP_TIMEOUT_SECONDS = 1.0
_VERIFIED_SOURCE_IMPORT_LOCK = threading.RLock()
_Selection = TypeVar("_Selection")
_HF_DOWNLOAD_SCRIPT = """
import sys
from huggingface_hub import hf_hub_download

result = hf_hub_download(
    repo_id=sys.argv[1],
    filename=sys.argv[2],
    cache_dir=sys.argv[3],
    local_dir=sys.argv[4],
    etag_timeout=int(sys.argv[5]),
)
print(result)
""".strip()


def _model_directory(cache_root: Path) -> Path:
    return _cache_path(cache_root, "motion_models")


def _source_directory(cache_root: Path) -> Path:
    return _cache_path(cache_root, "motion_models", "sources")


def _cache_path(cache_root: Path, *parts: str) -> Path:
    root = Path(cache_root).resolve()
    candidate = root.joinpath(*parts)
    try:
        candidate.resolve(strict=False).relative_to(root)
    except ValueError as error:
        raise MotionAssetError("Motion runtime path escapes the supplied cache root") from error
    return candidate


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _is_cancelled(cancelled: Callable[[], bool]) -> None:
    if cancelled():
        raise MotionCancelled("Motion asset preparation was cancelled")


def _stop_asset_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
    except OSError:
        pass
    try:
        process.wait(timeout=_PROCESS_STOP_TIMEOUT_SECONDS)
        return
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=_PROCESS_STOP_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _run_asset_command(
    command: list[str],
    *,
    cancelled: Callable[[], bool],
    timeout_seconds: float,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one asset command with cancellation, deadline, and process cleanup."""
    _is_cancelled(cancelled)
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=dict(env) if env is not None else None,
    )
    deadline = time.monotonic() + max(_PROCESS_POLL_SECONDS, float(timeout_seconds))
    try:
        while True:
            if cancelled():
                _stop_asset_process(process)
                raise MotionCancelled("Motion asset preparation was cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _stop_asset_process(process)
                raise subprocess.TimeoutExpired(command, timeout_seconds)
            try:
                stdout, stderr = process.communicate(
                    timeout=min(_PROCESS_POLL_SECONDS, remaining)
                )
                break
            except subprocess.TimeoutExpired:
                continue
        if process.returncode != 0:
            raise subprocess.CalledProcessError(
                int(process.returncode or 1),
                command,
                output=stdout,
                stderr=stderr,
            )
        return subprocess.CompletedProcess(command, 0, stdout, stderr)
    except BaseException:
        _stop_asset_process(process)
        raise


def _download_from_hub(
    *,
    repo_id: str,
    filename: str,
    cache_dir: str,
    local_dir: str,
    etag_timeout: int,
    cancelled: Callable[[], bool],
) -> Path:
    environment = os.environ.copy()
    environment["HF_HUB_ETAG_TIMEOUT"] = str(etag_timeout)
    environment["HF_HUB_DOWNLOAD_TIMEOUT"] = str(_HF_REQUEST_TIMEOUT_SECONDS)
    try:
        result = _run_asset_command(
            [
                sys.executable,
                "-c",
                _HF_DOWNLOAD_SCRIPT,
                repo_id,
                filename,
                cache_dir,
                local_dir,
                str(etag_timeout),
            ],
            cancelled=cancelled,
            timeout_seconds=_HF_TOTAL_TIMEOUT_SECONDS,
            env=environment,
        )
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if not lines:
            raise MotionDependencyError("Unable to download the required motion model")
        return Path(lines[-1])
    except MotionCancelled:
        raise
    except (OSError, subprocess.SubprocessError):
        raise MotionDependencyError("Unable to download the required motion model") from None


def _sha256(path: Path, cancelled: Callable[[], bool] | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        while chunk := file_handle.read(_HASH_CHUNK_SIZE):
            if cancelled is not None:
                _is_cancelled(cancelled)
            digest.update(chunk)
    return digest.hexdigest()


def _has_expected_hash(path: Path, artifact: ModelArtifact) -> bool:
    return path.is_file() and _sha256(path) == artifact.sha256


def _download_artifact(
    cache_root: Path,
    artifact: ModelArtifact,
    destination: Path,
    progress: Callable[[str, float], None],
    cancelled: Callable[[], bool],
    downloader: Callable[..., str] | None = None,
) -> Path:
    _is_cancelled(cancelled)
    if downloader is None and hf_hub_download is None:
        raise MotionDependencyError("huggingface-hub is required to download motion models")

    destination.parent.mkdir(parents=True, exist_ok=True)
    legacy_part_path = destination.with_name(f"{destination.name}.part")
    legacy_part_path.unlink(missing_ok=True)
    part_path = destination.with_name(f".{destination.name}.{uuid4().hex}.part")
    staging_directory = _cache_path(
        cache_root, "motion_models", ".hf-downloads", uuid4().hex
    )
    progress(f"Downloading {artifact.filename}", 0.0)
    try:
        download_arguments = {
            "repo_id": artifact.repo_id,
            "filename": artifact.filename,
            "cache_dir": str(_cache_path(cache_root, "motion_models", ".hf-cache")),
            "local_dir": str(staging_directory),
            "etag_timeout": _HF_ETAG_TIMEOUT_SECONDS,
        }
        if downloader is None:
            downloaded = _download_from_hub(
                **download_arguments,
                cancelled=cancelled,
            )
        else:
            downloaded = Path(downloader(**download_arguments))
        if not _is_within(downloaded, Path(cache_root)):
            raise MotionIntegrityError("Downloaded model escaped the supplied cache root")
        _is_cancelled(cancelled)
        with downloaded.open("rb") as source_handle, part_path.open("wb") as target_handle:
            while chunk := source_handle.read(_HASH_CHUNK_SIZE):
                _is_cancelled(cancelled)
                target_handle.write(chunk)
        if _sha256(part_path, cancelled) != artifact.sha256:
            raise MotionIntegrityError(f"Hash verification failed for {artifact.filename}")
        _is_cancelled(cancelled)
        os.replace(part_path, destination)
        progress(f"Verified {artifact.filename}", 1.0)
        return destination
    except Exception:
        part_path.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(staging_directory, ignore_errors=True)


def ensure_model_artifact(
    cache_root: Path,
    artifact: ModelArtifact,
    progress: Callable[[str, float], None],
    cancelled: Callable[[], bool],
    *,
    downloader: Callable[..., str] | None = None,
) -> Path:
    """Return a manifest-verified model, downloading it only when necessary."""
    destination = _cache_path(cache_root, "motion_models", artifact.filename)
    if _has_expected_hash(destination, artifact):
        progress(f"Reusing {artifact.filename}", 1.0)
        return destination
    destination.unlink(missing_ok=True)
    return _download_artifact(
        cache_root,
        artifact,
        destination,
        progress,
        cancelled,
        downloader,
    )


def verify_source_checkout(checkout: Path, source: GitSource, source_root: Path) -> None:
    """Reject a checkout unless it is clean, contained, and at its pinned commit."""
    checkout_entries = tuple(checkout.rglob("*")) if checkout.is_dir() else ()
    if (
        not checkout.is_dir()
        or checkout.is_symlink()
        or not _is_within(checkout, source_root)
        or any(path.is_symlink() and not _is_within(path, checkout) for path in checkout_entries)
    ):
        raise MotionSourceError(f"Source {source.name} escapes its verified cache directory")
    if any(
        path.is_file() and path.suffix.lower() in {".pyc", ".pyo"}
        for path in checkout_entries
    ):
        raise MotionSourceError(f"Source {source.name} contains persistent bytecode")
    try:
        head = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
        status = subprocess.run(
            ["git", "-C", str(checkout), "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored=matching"],
            check=True,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise MotionSourceError(f"Unable to verify pinned source {source.name}") from error
    if head.stdout.strip().lower() != source.commit.lower():
        raise MotionSourceError(f"Source {source.name} is not at its pinned commit")
    if status.stdout.strip():
        raise MotionSourceError(f"Source {source.name} contains local modifications")


def ensure_source_checkout(
    cache_root: Path,
    source: GitSource,
    cancelled: Callable[[], bool] = lambda: False,
) -> Path:
    """Clone a source without checking out a branch, then detach at its pinned commit."""
    try:
        source_root = _source_directory(cache_root)
        checkout = _cache_path(cache_root, "motion_models", "sources", source.name)
    except MotionAssetError as error:
        raise MotionSourceError(f"Source {source.name} escapes its verified cache directory") from error
    if checkout.is_symlink():
        raise MotionSourceError(f"Source {source.name} escapes its verified cache directory")
    if checkout.exists():
        verify_source_checkout(checkout, source, source_root)
        return checkout

    checkout.parent.mkdir(parents=True, exist_ok=True)
    try:
        staging = _cache_path(
            cache_root,
            "motion_models",
            "sources",
            f".{source.name}.{uuid4().hex}.staging",
        )
    except MotionAssetError as error:
        raise MotionSourceError(f"Source {source.name} escapes its verified cache directory") from error
    _is_cancelled(cancelled)
    promoted = False
    try:
        _run_asset_command(
            ["git", "clone", "--no-checkout", source.url, str(staging)],
            cancelled=cancelled,
            timeout_seconds=_GIT_TIMEOUT_SECONDS,
        )
        _is_cancelled(cancelled)
        _run_asset_command(
            ["git", "-C", str(staging), "checkout", "--detach", source.commit],
            cancelled=cancelled,
            timeout_seconds=_GIT_TIMEOUT_SECONDS,
        )
        _is_cancelled(cancelled)
        verify_source_checkout(staging, source, source_root)
        _is_cancelled(cancelled)
        os.replace(staging, checkout)
        promoted = True
        verify_source_checkout(checkout, source, source_root)
        _is_cancelled(cancelled)
    except MotionCancelled:
        if promoted:
            shutil.rmtree(checkout, ignore_errors=True)
        raise
    except MotionSourceError:
        if promoted:
            shutil.rmtree(checkout, ignore_errors=True)
        raise
    except (OSError, subprocess.SubprocessError) as error:
        if promoted:
            shutil.rmtree(checkout, ignore_errors=True)
        raise MotionSourceError(f"Unable to prepare pinned source {source.name}") from error
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return checkout


def _verified_source_checkouts(
    cache_root: Path,
    assets: Mapping[str, Path],
    sources: Iterable[GitSource] | None = None,
) -> tuple[tuple[GitSource, Path], ...]:
    source_root = _source_directory(cache_root)
    verified = []
    for source in GIT_SOURCES if sources is None else sources:
        expected_checkout = _cache_path(cache_root, "motion_models", "sources", source.name)
        try:
            checkout = Path(assets[source.name])
        except (KeyError, TypeError) as error:
            raise MotionSourceError(f"Verified source path is missing for {source.name}") from error
        if checkout.resolve(strict=False) != expected_checkout.resolve(strict=False):
            raise MotionSourceError(f"Source {source.name} escapes its verified cache directory")
        verify_source_checkout(checkout, source, source_root)
        verified.append((source, checkout))
    return tuple(verified)


@contextmanager
def verified_source_imports(
    cache_root: Path,
    assets: Mapping[str, Path],
    source_names: Iterable[str] | None = None,
) -> Iterator[None]:
    """Temporarily expose pinned sources without reading or writing bytecode caches."""
    with _VERIFIED_SOURCE_IMPORT_LOCK:
        selected_sources = _select_named(GIT_SOURCES, source_names, lambda source: source.name)
        verified_checkouts = _verified_source_checkouts(cache_root, assets, selected_sources)
        prior_path = sys.path.copy()
        prior_dont_write_bytecode = sys.dont_write_bytecode
        prior_pycache_prefix = sys.pycache_prefix
        checkout_paths = [str(checkout.resolve()) for _source, checkout in verified_checkouts]
        try:
            sys.path[:] = checkout_paths + [path for path in prior_path if path not in checkout_paths]
            sys.dont_write_bytecode = True
            sys.pycache_prefix = None
            importlib.invalidate_caches()
            yield
        finally:
            sys.path[:] = prior_path
            sys.dont_write_bytecode = prior_dont_write_bytecode
            sys.pycache_prefix = prior_pycache_prefix
            importlib.invalidate_caches()
            for source, checkout in verified_checkouts:
                verify_source_checkout(checkout, source, _source_directory(cache_root))


def inspect_motion_runtime(cache_root: Path) -> MotionRuntimeStatus:
    """Inspect the optional local runtime without installing or downloading anything."""
    missing_packages = tuple(
        package for package, module in _PACKAGE_MODULES.items() if importlib.util.find_spec(module) is None
    )
    cuda_available = False
    onnx_cuda_available = False
    if "torch" not in missing_packages:
        import torch

        cuda_available = bool(torch.cuda.is_available())
    if "onnxruntime-gpu" not in missing_packages:
        import onnxruntime

        onnx_cuda_available = "CUDAExecutionProvider" in onnxruntime.get_available_providers()
    missing_models = tuple(
        artifact.filename
        for artifact in MODEL_ARTIFACTS
        if not _has_expected_hash(_model_directory(cache_root) / artifact.filename, artifact)
    )
    ready = not missing_packages and not missing_models and cuda_available and onnx_cuda_available
    return MotionRuntimeStatus(
        ready=ready,
        cuda_available=cuda_available,
        onnx_cuda_available=onnx_cuda_available,
        missing_packages=missing_packages,
        missing_models=missing_models,
    )


def _select_named(
    candidates: Iterable[_Selection],
    requested_names: Iterable[str] | None,
    name: Callable[[_Selection], str],
) -> tuple[_Selection, ...]:
    available = tuple(candidates)
    if requested_names is None:
        return available
    by_name = {name(candidate): candidate for candidate in available}
    requested = tuple(requested_names)
    if len(set(requested)) != len(requested) or any(item not in by_name for item in requested):
        raise MotionAssetError("Requested motion runtime asset is unavailable")
    return tuple(by_name[item] for item in requested)


def ensure_motion_assets(
    cache_root: Path,
    progress: Callable[[str, float], None],
    cancelled: Callable[[], bool],
    *,
    source_names: Iterable[str] | None = None,
    artifact_names: Iterable[str] | None = None,
    downloader: Callable[..., str] | None = None,
) -> dict[str, Path]:
    """Prepare only verified models and immutable source revisions below ``cache_root``."""
    assets: dict[str, Path] = {}
    for source in _select_named(GIT_SOURCES, source_names, lambda item: item.name):
        _is_cancelled(cancelled)
        checkout = ensure_source_checkout(cache_root, source, cancelled)
        verify_source_checkout(checkout, source, _source_directory(cache_root))
        assets[source.name] = checkout
    for artifact in _select_named(MODEL_ARTIFACTS, artifact_names, lambda item: item.filename):
        _is_cancelled(cancelled)
        if downloader is None:
            assets[artifact.filename] = ensure_model_artifact(
                cache_root, artifact, progress, cancelled
            )
        else:
            assets[artifact.filename] = ensure_model_artifact(
                cache_root,
                artifact,
                progress,
                cancelled,
                downloader=downloader,
            )
    return assets


def ensure_depth_assets(
    cache_root: Path,
    progress: Callable[[str, float], None],
    cancelled: Callable[[], bool],
) -> dict[str, Path]:
    """Prepare exactly the verified VDA source and Small checkpoint for depth inference."""
    return ensure_motion_assets(
        cache_root,
        progress,
        cancelled,
        source_names=(VIDEO_DEPTH_ANYTHING_SOURCE.name,),
        artifact_names=("video_depth_anything_vits.pth",),
    )


def ensure_pose_assets(
    cache_root: Path,
    progress: Callable[[str, float], None],
    cancelled: Callable[[], bool],
) -> dict[str, Path]:
    """Prepare exactly the pinned DWPose source and its two ONNX artifacts."""
    return ensure_motion_assets(
        cache_root,
        progress,
        cancelled,
        source_names=(DWPOSE_SOURCE.name,),
        artifact_names=("yolox_l.onnx", "dw-ll_ucoco_384.onnx"),
    )
