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


class MotionOutOfMemory(MotionRuntimeError):
    """Local CUDA memory is insufficient for the requested inference."""


class MotionCancelled(MotionError):
    """The caller cancelled the motion operation."""
