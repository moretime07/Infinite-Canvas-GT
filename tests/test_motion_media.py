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
import threading
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
        cls.vfr_source = cls.fixture_dir / "variable-frame-rate.mp4"
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
        _run_ffmpeg(
            "-f", "lavfi", "-i", "testsrc2=size=80x48:rate=30:duration=3",
            "-vf", "select='if(lt(t,1.5),not(mod(n,2)),not(mod(n,3)))'",
            "-fps_mode", "vfr", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(cls.vfr_source),
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

            class FailedDecodeProcess:
                def __init__(self, command: list[str], **_keywords: object) -> None:
                    self.returncode = 1
                    Path(command[-1]).write_bytes(b"partial")

                def poll(self) -> int:
                    return self.returncode

            with patch("motion_extractor.media.subprocess.Popen", FailedDecodeProcess):
                with self.assertRaises(media.MotionMediaError) as raised:
                    media.decode_video_once(self.source, work_dir)
            self.assertNotIn(str(work_dir), str(raised.exception))
            self.assertFalse(list(work_dir.glob("*.rgb")))

    def test_decode_cancellation_terminates_kills_and_removes_partial_rgb(self) -> None:
        metadata = media.VideoMetadata(2, 2, 1, 1, 1, 1.0, 0, False)
        processes = []

        class StubbornProcess:
            def __init__(self, command, **_kwargs):
                self.command = command
                self.returncode = None
                self.terminated = False
                self.killed = False
                Path(command[-1]).write_bytes(b"partial")
                processes.append(self)

            def poll(self):
                return self.returncode

            def terminate(self):
                self.terminated = True

            def wait(self, timeout=None):
                if not self.killed:
                    raise subprocess.TimeoutExpired(self.command, timeout)
                self.returncode = -9
                return self.returncode

            def kill(self):
                self.killed = True

        with tempfile.TemporaryDirectory() as temporary, \
                patch("motion_extractor.media.probe_video", return_value=metadata), \
                patch("motion_extractor.media.subprocess.Popen", StubbornProcess):
            work_dir = Path(temporary)
            with self.assertRaises(media.MotionCancelled):
                media.decode_video_once(self.source, work_dir, lambda: True)

            self.assertEqual(len(processes), 1)
            self.assertTrue(processes[0].terminated)
            self.assertTrue(processes[0].killed)
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

    def test_variable_frame_rate_is_rejected_with_actionable_path_free_guidance(self) -> None:
        with self.assertRaises(media.MotionValidationError) as raised:
            media.probe_video(self.vfr_source)
        self.assertIn("constant frame rate", str(raised.exception).lower())
        self.assertNotIn(str(self.vfr_source), str(raised.exception))

    def test_supported_portrait_sixty_fps_budget_is_not_rejected(self) -> None:
        payload = {
            "format": {"duration": "30.0"},
            "streams": [{
                "codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920,
                "avg_frame_rate": "60/1", "r_frame_rate": "60/1", "nb_frames": "1800",
            }],
        }
        timestamps = [index / 60 for index in range(1800)]
        with patch("motion_extractor.media._ffprobe", return_value=payload), \
                patch("motion_extractor.media._video_timestamps", return_value=timestamps):
            metadata = media.probe_video(self.source)
        self.assertEqual((metadata.width, metadata.height), (1080, 1920))
        self.assertEqual(metadata.frame_count, 1800)

    def test_probe_rejects_odd_dimensions_excessive_pixels_and_excessive_fps(self) -> None:
        cases = (
            ({"width": 81, "height": 48, "avg_frame_rate": "12/1"}, "odd width"),
            ({"width": 4096, "height": 2162, "avg_frame_rate": "30/1"}, "pixel budget"),
            ({"width": 1920, "height": 1080, "avg_frame_rate": "61/1"}, "fps budget"),
        )
        for overrides, label in cases:
            stream = {
                "codec_type": "video", "codec_name": "h264", "width": 80, "height": 48,
                "avg_frame_rate": "12/1", "r_frame_rate": "12/1", "nb_frames": "36",
                **overrides,
            }
            payload = {"format": {"duration": "3.0"}, "streams": [stream]}
            with self.subTest(label=label), \
                    patch("motion_extractor.media._ffprobe", return_value=payload), \
                    patch("motion_extractor.media._video_timestamps", return_value=[0.0, 1 / 12, 2 / 12]):
                with self.assertRaises(media.MotionMediaError):
                    media.probe_video(self.source)

    def test_actual_decode_count_cannot_exceed_declared_timing_or_frame_budget(self) -> None:
        metadata = media.VideoMetadata(2, 2, 1, 1, 1, 1.0, 0, False)

        def oversized_decode(command, _cancelled):
            frame_bytes = metadata.width * metadata.height * 3
            Path(command[-1]).write_bytes(b"\0" * frame_bytes * 31)

        with tempfile.TemporaryDirectory() as temporary, \
                patch("motion_extractor.media.probe_video", return_value=metadata), \
                patch("motion_extractor.media._decode_to_raw", side_effect=oversized_decode):
            work_dir = Path(temporary)
            with self.assertRaises(media.MotionMediaError):
                media.decode_video_once(self.source, work_dir)
            self.assertFalse(list(work_dir.glob("*.rgb")))

    def test_truncated_decode_cannot_silently_publish_a_shorter_timeline(self) -> None:
        metadata = media.VideoMetadata(2, 2, 10, 1, 20, 2.0, 0, False)

        def truncated_decode(command, _cancelled):
            frame_bytes = metadata.width * metadata.height * 3
            Path(command[-1]).write_bytes(b"\0" * frame_bytes)

        with tempfile.TemporaryDirectory() as temporary, \
                patch("motion_extractor.media.probe_video", return_value=metadata), \
                patch("motion_extractor.media._decode_to_raw", side_effect=truncated_decode):
            work_dir = Path(temporary)
            with self.assertRaises(media.MotionMediaError):
                media.decode_video_once(self.source, work_dir)
            self.assertFalse(list(work_dir.glob("*.rgb")))

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

    def test_cancelled_encoder_supervision_terminates_kills_joins_and_cleans(self) -> None:
        metadata = media.VideoMetadata(2, 2, 1, 1, 1, 1.0, 0, False)
        frame = np.zeros((2, 2, 3), dtype=np.uint8)
        processes = []

        class BlockingPipe:
            def __init__(self):
                self.started = threading.Event()
                self.released = threading.Event()
                self.closed = False

            def write(self, _data):
                self.started.set()
                self.released.wait(timeout=2)
                raise BrokenPipeError

            def read(self, *_arguments):
                self.started.set()
                self.released.wait(timeout=2)
                return b""

            def close(self):
                self.closed = True
                self.released.set()

        class StubbornEncodeProcess:
            def __init__(self, _command, **_kwargs):
                self.stdin = BlockingPipe()
                self.stderr = BlockingPipe()
                self.returncode = None
                self.terminated = False
                self.killed = False
                processes.append(self)

            def poll(self):
                return self.returncode

            def terminate(self):
                self.terminated = True

            def wait(self, timeout=None):
                if not self.killed:
                    raise subprocess.TimeoutExpired("ffmpeg", timeout)
                self.returncode = -9
                return self.returncode

            def kill(self):
                self.killed = True

        with tempfile.TemporaryDirectory() as temporary, \
                patch("motion_extractor.media._required_executable", return_value="ffmpeg"), \
                patch("motion_extractor.media.subprocess.Popen", StubbornEncodeProcess):
            destination = Path(temporary) / "cancelled.mp4"
            with self.assertRaises(media.MotionCancelled):
                media.encode_rgb_frames(
                    [frame],
                    metadata,
                    destination,
                    self.source,
                    preserve_audio=False,
                    cancelled=lambda: True,
                )
            self.assertEqual(len(processes), 1)
            process = processes[0]
            self.assertTrue(process.terminated)
            self.assertTrue(process.killed)
            self.assertTrue(process.stdin.closed)
            self.assertTrue(process.stderr.closed)
            self.assertFalse(destination.exists())
            self.assertFalse(list(Path(temporary).glob("*.tmp.mp4")))


if __name__ == "__main__":
    unittest.main()
