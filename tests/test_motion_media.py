"""Integration coverage for the local motion-media boundary.

The fixtures are deliberately tiny but use real FFmpeg so codec, rotation, and
stdin streaming contracts remain covered without depending on user media.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from motion_extractor import media


def _run_ffmpeg(*arguments: str) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise unittest.SkipTest("ffmpeg is required for motion media tests")
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def _probe_streams(path: Path) -> dict:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise unittest.SkipTest("ffprobe is required for motion media tests")
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-count_frames",
            "-show_entries",
            "stream=codec_type,codec_name,pix_fmt,width,height,avg_frame_rate,nb_read_frames",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class MotionMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._fixture_dir = tempfile.TemporaryDirectory()
        cls.fixture_dir = Path(cls._fixture_dir.name)
        cls.source = cls.fixture_dir / "source.mp4"
        cls.rotated = cls.fixture_dir / "rotated.mp4"
        cls.long_source = cls.fixture_dir / "long.mp4"
        cls.vorbis_source = cls.fixture_dir / "vorbis.mkv"
        _run_ffmpeg(
            "-f", "lavfi", "-i", "testsrc2=size=80x48:rate=12",
            "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=44100",
            "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(cls.source),
        )
        _run_ffmpeg(
            "-i", str(cls.source), "-c", "copy", "-metadata:s:v:0", "rotate=90",
            str(cls.rotated),
        )
        _run_ffmpeg(
            "-f", "lavfi", "-i", "color=c=black:size=16x16:rate=1",
            "-t", "31", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(cls.long_source),
        )
        _run_ffmpeg(
            "-f", "lavfi", "-i", "testsrc2=size=80x48:rate=12",
            "-f", "lavfi", "-i", "sine=frequency=500:sample_rate=44100",
            "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "libvorbis",
            str(cls.vorbis_source),
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._fixture_dir.cleanup()

    def test_missing_and_corrupt_video_fail_with_sanitized_media_error(self) -> None:
        missing = self.fixture_dir / "missing.mp4"
        corrupt = self.fixture_dir / "corrupt.mp4"
        corrupt.write_bytes(b"not a video")

        for path in (missing, corrupt):
            with self.subTest(path=path.name), self.assertRaises(media.MotionMediaError) as raised:
                media.probe_video(path)
            self.assertNotIn(str(path), str(raised.exception))
            self.assertNotIn("not a video", str(raised.exception))

    def test_decode_missing_or_corrupt_media_never_launches_ffmpeg_decode(self) -> None:
        missing = self.fixture_dir / "missing-for-decode.mp4"
        corrupt = self.fixture_dir / "corrupt-for-decode.mp4"
        corrupt.write_bytes(b"not a video")
        ffmpeg = shutil.which("ffmpeg")
        original_run = media.subprocess.run

        for path in (missing, corrupt):
            calls: list[list[str]] = []

            def record_run(command: list[str], *arguments: object, **keywords: object) -> subprocess.CompletedProcess[str]:
                calls.append(command)
                return original_run(command, *arguments, **keywords)

            with self.subTest(path=path.name), tempfile.TemporaryDirectory() as temporary, \
                    patch("motion_extractor.media.subprocess.run", side_effect=record_run):
                with self.assertRaises(media.MotionMediaError):
                    media.decode_video_once(path, Path(temporary))
            self.assertFalse(any(command[0] == ffmpeg for command in calls))

    def test_cleanup_failure_is_sanitized_without_raw_path_leak(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            raw_path = Path(temporary) / "locked-frames.rgb"
            raw_path.write_bytes(b"rgb")
            frames = np.memmap(raw_path, mode="r+", dtype=np.uint8, shape=(1, 1, 1, 3))
            store = media.SharedFrameStore(
                media.VideoMetadata(1, 1, 1, 1, 1, 1.0, 0, False), raw_path, frames
            )
            with patch.object(Path, "unlink", side_effect=PermissionError(str(raw_path))):
                with self.assertRaises(media.MotionMediaError) as raised:
                    store.close()
            self.assertNotIn(str(raw_path), str(raised.exception))
            raw_path.unlink()

    def test_decode_failure_removes_partial_rgb_artifact(self) -> None:
        metadata = media.VideoMetadata(2, 2, 1, 1, 1, 1.0, 0, False)
        with tempfile.TemporaryDirectory() as temporary, \
                patch("motion_extractor.media.probe_video", return_value=metadata):
            work_dir = Path(temporary)

            def fail_after_partial_decode(command: list[str], **_keywords: object) -> None:
                partial = Path(command[-1])
                partial.write_bytes(b"partial")
                raise subprocess.CalledProcessError(1, command, stderr="decoder diagnostic")

            with patch("motion_extractor.media.subprocess.run", side_effect=fail_after_partial_decode):
                with self.assertRaises(media.MotionMediaError) as raised:
                    media.decode_video_once(self.source, work_dir)
            self.assertNotIn(str(work_dir), str(raised.exception))
            self.assertFalse(list(work_dir.glob("*.rgb")))

    def test_encode_failure_removes_partial_mp4_and_spool_without_diagnostics(self) -> None:
        metadata = media.VideoMetadata(2, 2, 1, 1, 1, 1.0, 0, True)
        frame = np.zeros((2, 2, 3), dtype=np.uint8)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            destination = directory / "published.mp4"

            def fail_with_partial_output(
                _frames: object,
                _metadata: object,
                temporary_destination: Path,
                _source: object,
                **_keywords: object,
            ) -> bool:
                temporary_destination.write_bytes(b"incomplete")
                return False

            with patch("motion_extractor.media._encode_attempt", side_effect=fail_with_partial_output):
                with self.assertRaises(media.MotionMediaError) as raised:
                    media.encode_rgb_frames([frame], metadata, destination, self.source, preserve_audio=True)
            self.assertNotIn(str(directory), str(raised.exception))
            self.assertNotIn("incomplete", str(raised.exception))
            self.assertFalse(destination.exists())
            self.assertFalse(list(directory.glob("*.rgb")))
            self.assertFalse(list(directory.glob("*.tmp.mp4")))

    def test_duration_over_thirty_seconds_is_rejected_before_decode(self) -> None:
        with self.assertRaises(media.MotionMediaError):
            media.probe_video(self.long_source)

    def test_rotated_source_uses_display_dimensions(self) -> None:
        metadata = media.probe_video(self.rotated)

        self.assertEqual((metadata.width, metadata.height), (48, 80))
        self.assertEqual(metadata.rotation, 90)
        self.assertEqual((metadata.fps_num, metadata.fps_den), (12, 1))

    def test_decode_exposes_one_display_oriented_rgb_memmap_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with media.decode_video_once(self.rotated, Path(temporary)) as store:
                raw_path = store.raw_path
                expected_frame_bytes = store.metadata.width * store.metadata.height * 3
                self.assertEqual(store.frames.dtype, np.uint8)
                self.assertEqual(store.frames.shape, (36, 80, 48, 3))
                self.assertEqual(store.metadata.frame_count, 36)
                self.assertEqual(raw_path.stat().st_size % expected_frame_bytes, 0)
                self.assertEqual(raw_path.stat().st_size // expected_frame_bytes, store.frames.shape[0])
            self.assertFalse(raw_path.exists())

    def test_default_encode_is_h264_yuv420p_without_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "silent.mp4"
            with media.decode_video_once(self.source, Path(temporary)) as store:
                result = media.encode_rgb_frames(
                    store.frames, store.metadata, destination, self.source, preserve_audio=False
                )

            streams = _probe_streams(destination)["streams"]
            video = next(stream for stream in streams if stream["codec_type"] == "video")
            self.assertEqual(video["codec_name"], "h264")
            self.assertEqual(video["pix_fmt"], "yuv420p")
            self.assertEqual((video["width"], video["height"]), (80, 48))
            self.assertEqual(video["avg_frame_rate"], "12/1")
            self.assertEqual(int(video["nb_read_frames"]), 36)
            self.assertFalse(any(stream["codec_type"] == "audio" for stream in streams))
            self.assertFalse(result.audio_transcoded)

    def test_preserve_audio_copies_compatible_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "audio.mp4"
            with media.decode_video_once(self.source, Path(temporary)) as store:
                result = media.encode_rgb_frames(
                    store.frames, store.metadata, destination, self.source, preserve_audio=True
                )

            streams = _probe_streams(destination)["streams"]
            self.assertTrue(any(stream["codec_type"] == "audio" for stream in streams))
            self.assertFalse(result.audio_transcoded)

    def test_incompatible_source_audio_is_transcoded_to_aac(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "aac-fallback.mp4"
            with media.decode_video_once(self.vorbis_source, Path(temporary)) as store:
                result = media.encode_rgb_frames(
                    store.frames, store.metadata, destination, self.vorbis_source, preserve_audio=True
                )

            streams = _probe_streams(destination)["streams"]
            audio = next(stream for stream in streams if stream["codec_type"] == "audio")
            self.assertEqual(audio["codec_name"], "aac")
            self.assertTrue(result.audio_transcoded)


if __name__ == "__main__":
    unittest.main()
