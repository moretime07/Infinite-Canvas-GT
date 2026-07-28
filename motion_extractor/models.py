"""Verified local artifacts for the optional motion extraction runtime."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

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


def _model_directory(cache_root: Path) -> Path:
    return _cache_path(cache_root, "motion_models")


def _source_directory(cache_root: Path) -> Path:
    return _cache_path(cache_root, "motion_models", "sources")


def _source_bytecode_directory(cache_root: Path) -> Path:
    return _cache_path(cache_root, "motion_models", "source_bytecode")


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
) -> Path:
    _is_cancelled(cancelled)
    if hf_hub_download is None:
        raise MotionDependencyError("huggingface-hub is required to download motion models")

    destination.parent.mkdir(parents=True, exist_ok=True)
    part_path = destination.with_name(f"{destination.name}.part")
    part_path.unlink(missing_ok=True)
    progress(f"Downloading {artifact.filename}", 0.0)
    try:
        downloaded = Path(
            hf_hub_download(
                repo_id=artifact.repo_id,
                filename=artifact.filename,
                cache_dir=str(_cache_path(cache_root, "motion_models", ".hf-cache")),
                local_dir=str(_cache_path(cache_root, "motion_models", ".hf-downloads")),
            )
        )
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


def ensure_model_artifact(
    cache_root: Path,
    artifact: ModelArtifact,
    progress: Callable[[str, float], None],
    cancelled: Callable[[], bool],
) -> Path:
    """Return a manifest-verified model, downloading it only when necessary."""
    destination = _cache_path(cache_root, "motion_models", artifact.filename)
    if _has_expected_hash(destination, artifact):
        progress(f"Reusing {artifact.filename}", 1.0)
        return destination
    destination.unlink(missing_ok=True)
    return _download_artifact(cache_root, artifact, destination, progress, cancelled)


def verify_source_checkout(checkout: Path, source: GitSource, source_root: Path) -> None:
    """Reject a checkout unless it is clean, contained, and at its pinned commit."""
    if (
        not checkout.is_dir()
        or checkout.is_symlink()
        or not _is_within(checkout, source_root)
        or any(path.is_symlink() and not _is_within(path, checkout) for path in checkout.rglob("*"))
    ):
        raise MotionSourceError(f"Source {source.name} escapes its verified cache directory")
    try:
        head = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        status = subprocess.run(
            ["git", "-C", str(checkout), "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored=matching"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise MotionSourceError(f"Unable to verify pinned source {source.name}") from error
    if head.stdout.strip().lower() != source.commit.lower():
        raise MotionSourceError(f"Source {source.name} is not at its pinned commit")
    if status.stdout.strip():
        raise MotionSourceError(f"Source {source.name} contains local modifications")


def ensure_source_checkout(cache_root: Path, source: GitSource) -> Path:
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
        subprocess.run(
            ["git", "clone", "--no-checkout", source.url, str(checkout)],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(checkout), "checkout", "--detach", source.commit],
            check=True,
            capture_output=True,
            text=True,
        )
        verify_source_checkout(checkout, source, source_root)
    except (OSError, subprocess.CalledProcessError) as error:
        raise MotionSourceError(f"Unable to prepare pinned source {source.name}") from error
    return checkout


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


def ensure_motion_assets(
    cache_root: Path,
    progress: Callable[[str, float], None],
    cancelled: Callable[[], bool],
) -> dict[str, Path]:
    """Prepare only verified models and immutable source revisions below ``cache_root``."""
    assets: dict[str, Path] = {}
    for source in GIT_SOURCES:
        _is_cancelled(cancelled)
        checkout = ensure_source_checkout(cache_root, source)
        verify_source_checkout(checkout, source, _source_directory(cache_root))
        source_bytecode_directory = _source_bytecode_directory(cache_root)
        source_bytecode_directory.mkdir(parents=True, exist_ok=True)
        sys.pycache_prefix = str(source_bytecode_directory)
        checkout_string = str(checkout)
        if checkout_string not in sys.path:
            sys.path.insert(0, checkout_string)
        assets[source.name] = checkout
    for artifact in MODEL_ARTIFACTS:
        _is_cancelled(cancelled)
        assets[artifact.filename] = ensure_model_artifact(cache_root, artifact, progress, cancelled)
    return assets
