"""Stable, path-free errors for the local motion extraction pipeline."""

from __future__ import annotations


class MotionError(Exception):
    """Base class for stable motion-extraction failures."""


class MotionValidationError(MotionError):
    """The request is invalid before motion processing can begin."""


class MotionMediaError(MotionError):
    """The local source or encoded media could not be processed safely."""


class MotionRuntimeError(MotionError):
    """The local inference runtime failed without exposing diagnostics."""


class MotionRuntimeUnavailable(MotionRuntimeError):
    """A verified local dependency or model asset is unavailable."""


class MotionQueueFull(MotionError):
    """The bounded local motion queue cannot accept another task."""


class MotionOutOfMemory(MotionRuntimeError):
    """Local CUDA memory is insufficient for the requested inference."""


class MotionCancelled(MotionError):
    """The caller cancelled the motion operation."""
