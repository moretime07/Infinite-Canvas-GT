"""End-to-end verification for the local motion-reference pipeline.

Model inference is replaced with deterministic processors, but source creation,
decode, branch encoding, optional audio muxing, probing, and cleanup all use the
production pipeline and the real FFmpeg/FFprobe executables.
"""

from __future__ import annotations

import asyncio
from fractions import Fraction
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import warnings

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from motion_extractor import media
from motion_extractor.depth import BranchResult
from motion_extractor.service import MotionTaskService


def _required_executable(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise unittest.SkipTest(f"{name} is required for motion pipeline integration")
    return executable


def _run_ffmpeg(*arguments: str) -> None:
    result = subprocess.run(
        [
            _required_executable("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            *arguments,
        ],
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise AssertionError("FFmpeg integration fixture command failed")


def _probe(path: Path) -> dict[str, object]:
    result = subprocess.run(
        [
            _required_executable("ffprobe"),
            "-v",
            "error",
            "-count_frames",
            "-show_entries",
            (
                "format=duration:"
                "stream=codec_type,width,height,avg_frame_rate,nb_read_frames:"
                "stream_tags=rotate:stream_side_data=rotation"
            ),
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise AssertionError("FFprobe integration command failed")
    try:
        payload = json.loads(result.stdout)
    except (TypeError, ValueError):
        raise AssertionError("FFprobe returned an invalid integration payload") from None
    if not isinstance(payload, dict):
        raise AssertionError("FFprobe returned an invalid integration payload")
    return payload


def _video_facts(path: Path) -> dict[str, object]:
    payload = _probe(path)
    streams = payload.get("streams")
    if not isinstance(streams, list):
        raise AssertionError("FFprobe returned no integration streams")
    video = next(
        (
            stream
            for stream in streams
            if isinstance(stream, dict) and stream.get("codec_type") == "video"
        ),
        None,
    )
    if not isinstance(video, dict):
        raise AssertionError("FFprobe returned no integration video stream")
    fps = Fraction(str(video.get("avg_frame_rate")))
    frame_count = int(str(video.get("nb_read_frames")))
    duration = float(payload.get("format", {}).get("duration"))
    rotation = int(video.get("tags", {}).get("rotate", 0) or 0)
    for item in video.get("side_data_list", ()):
        if isinstance(item, dict) and item.get("rotation") is not None:
            rotation = int(item["rotation"]) % 360
    return {
        "width": int(video["width"]),
        "height": int(video["height"]),
        "fps": fps,
        "frame_count": frame_count,
        "duration": duration,
        "rotation": rotation % 360,
        "has_audio": any(
            isinstance(stream, dict) and stream.get("codec_type") == "audio"
            for stream in streams
        ),
    }


def _decode_rgb(path: Path, width: int, height: int) -> np.ndarray:
    result = subprocess.run(
        [
            _required_executable("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ],
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise AssertionError("FFmpeg integration decode failed")
    frame_bytes = width * height * 3
    if not result.stdout or len(result.stdout) % frame_bytes:
        raise AssertionError("FFmpeg returned malformed integration frames")
    return np.frombuffer(result.stdout, dtype=np.uint8).reshape(
        -1, height, width, 3
    )


def _encode_branch(
    frames: object,
    frame_store: media.SharedFrameStore,
    output_path: Path,
) -> None:
    media.encode_rgb_frames(
        frames,
        frame_store.metadata,
        output_path,
        output_path,
        preserve_audio=False,
    )


class FakeDepthProcessor:
    """Produce deterministic grayscale depth while exercising real encoding."""

    def run(
        self,
        frame_store: media.SharedFrameStore,
        output_path: Path,
        progress,
        cancelled,
        input_size: int = 518,
    ) -> BranchResult:
        if input_size != 518:
            raise AssertionError("unexpected integration depth retry")
        scratch = frame_store.raw_path.parent / "fake-relative.depth"
        scratch.write_bytes(b"task-local depth scratch")
        warnings.warn(
            f"fake processor used {frame_store.raw_path}",
            RuntimeWarning,
            stacklevel=1,
        )

        def depth_frames():
            progress(0.0)
            for index, source in enumerate(frame_store.frames):
                if cancelled():
                    return
                grayscale = np.asarray(source, dtype=np.uint16).mean(axis=2).astype(np.uint8)
                yield np.repeat(grayscale[..., None], 3, axis=2)
                progress((index + 1) / frame_store.metadata.frame_count)

        _encode_branch(depth_frames(), frame_store, Path(output_path))
        return BranchResult("completed", Path(output_path))


class FakePoseProcessor:
    """Render two moving stick subjects over a black background."""

    def run(
        self,
        frame_store: media.SharedFrameStore,
        output_path: Path,
        progress,
        cancelled,
    ) -> BranchResult:
        def pose_frames():
            progress(0.0)
            for index in range(frame_store.metadata.frame_count):
                if cancelled():
                    return
                canvas = np.zeros(
                    (frame_store.metadata.height, frame_store.metadata.width, 3),
                    dtype=np.uint8,
                )
                first_x = 18 + index
                second_x = 72 - index
                canvas[24:78, first_x : first_x + 2] = 255
                canvas[38:40, first_x - 8 : first_x + 10] = 224
                canvas[92:142, second_x : second_x + 2] = 255
                canvas[108:110, second_x - 9 : second_x + 10] = 224
                yield canvas
                progress((index + 1) / frame_store.metadata.frame_count)

        _encode_branch(pose_frames(), frame_store, Path(output_path))
        return BranchResult("completed", Path(output_path))


class MotionPipelineIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "moving-subjects.mp4"
        _run_ffmpeg(
            "-f",
            "lavfi",
            "-i",
            "color=c=0x101820:size=96x160:rate=12:duration=1.5",
            "-f",
            "lavfi",
            "-i",
            "color=c=white:size=16x28:rate=12:duration=1.5",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x00A0FF:size=22x18:rate=12:duration=1.5",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=660:sample_rate=44100:duration=1.5",
            "-filter_complex",
            (
                "[0:v][1:v]overlay=x='6+20*t':y='18+8*t':shortest=1[v1];"
                "[v1][2:v]overlay=x='64-15*t':y='112-10*t':shortest=1[v]"
            ),
            "-map",
            "[v]",
            "-map",
            "3:a:0",
            "-t",
            "1.5",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(self.source),
        )

    async def asyncTearDown(self) -> None:
        self.temporary.cleanup()

    async def _wait_for_completion(
        self,
        service: MotionTaskService,
        task_id: str,
    ) -> dict[str, object]:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            record = await service.get(task_id)
            if record is not None and record["state"] in {
                "partial",
                "completed",
                "failed",
                "cancelled",
            }:
                return record
            await asyncio.sleep(0.01)
        self.fail("motion integration task did not reach a terminal state")

    async def _run_service(
        self,
        *,
        preserve_audio: bool,
        work_name: str,
    ) -> tuple[dict[str, object], dict[str, object], list[Path]]:
        output_dir = self.root / "published"
        work_dir = self.root / work_name
        service = MotionTaskService(
            output_dir=output_dir,
            work_dir=work_dir,
            depth_factory=FakeDepthProcessor,
            pose_factory=FakePoseProcessor,
        )
        try:
            created = await service.submit(
                "/assets/input/moving-subjects.mp4",
                self.source,
                depth_enabled=True,
                pose_enabled=True,
                preserve_audio=preserve_audio,
            )
            completed = await self._wait_for_completion(service, str(created["task_id"]))
        finally:
            await service.close()

        self.assertEqual(completed["state"], "completed")
        self.assertEqual(completed["depth_state"], "completed")
        self.assertEqual(completed["pose_state"], "completed")
        self.assertEqual(
            completed["warnings"],
            ["Motion processing completed with a warning."],
        )

        payload = json.dumps([created, completed], ensure_ascii=False)
        leaked_root = str(self.root).replace("\\", "/").lower() in payload.replace(
            "\\", "/"
        ).lower()
        self.assertFalse(leaked_root, "public task/log payload leaked a temporary path")
        self.assertNotIn("fake-relative.depth", payload)
        self.assertNotIn(".work.mp4", payload)

        task_local_entries = sum(1 for _entry in work_dir.rglob("*"))
        self.assertEqual(
            task_local_entries,
            0,
            "task-local raw frame/depth artifacts were not cleaned",
        )

        urls = [str(completed["depth_url"]), str(completed["pose_url"])]
        self.assertEqual(len(set(urls)), 2)
        self.assertTrue(
            all(
                url.startswith("/assets/output/motion/")
                and url.endswith(".mp4")
                and "\\" not in url
                and ".." not in url
                for url in urls
            )
        )
        paths = [output_dir / Path(url).name for url in urls]
        self.assertTrue(all(path.is_file() and path.stat().st_size > 0 for path in paths))
        return created, completed, paths

    async def test_dual_branch_pipeline_preserves_media_and_cleanup_contracts(self) -> None:
        source = _video_facts(self.source)
        self.assertEqual((source["width"], source["height"]), (96, 160))
        self.assertEqual(source["fps"], Fraction(12, 1))
        self.assertEqual(source["frame_count"], 18)
        self.assertEqual(source["rotation"], 0)
        self.assertTrue(source["has_audio"])
        self.assertLess(source["width"], source["height"])

        _created, silent_record, silent_paths = await self._run_service(
            preserve_audio=False,
            work_name="silent-work",
        )
        silent_facts = [_video_facts(path) for path in silent_paths]
        for facts in silent_facts:
            self.assertEqual((facts["width"], facts["height"]), (96, 160))
            self.assertEqual(facts["fps"], Fraction(12, 1))
            self.assertEqual(facts["frame_count"], 18)
            self.assertEqual(facts["rotation"], 0)
            self.assertLess(facts["width"], facts["height"])
            self.assertAlmostEqual(
                facts["duration"],
                source["duration"],
                delta=0.1,
            )
            self.assertFalse(facts["has_audio"])

        depth_frames = _decode_rgb(silent_paths[0], 96, 160)
        depth_channel_delta = np.max(
            np.abs(depth_frames.astype(np.int16) - depth_frames[..., :1].astype(np.int16))
        )
        self.assertLessEqual(depth_channel_delta, 2)

        pose_frames = _decode_rgb(silent_paths[1], 96, 160)
        brightness = pose_frames.max(axis=3)
        self.assertGreater(int(np.count_nonzero(brightness > 160)), 100)
        self.assertGreater(float(np.mean(brightness < 16)), 0.9)

        _created, audio_record, audio_paths = await self._run_service(
            preserve_audio=True,
            work_name="audio-work",
        )
        self.assertNotEqual(
            {silent_record["depth_url"], silent_record["pose_url"]},
            {audio_record["depth_url"], audio_record["pose_url"]},
        )
        for facts in (_video_facts(path) for path in audio_paths):
            self.assertEqual((facts["width"], facts["height"]), (96, 160))
            self.assertEqual(facts["fps"], Fraction(12, 1))
            self.assertEqual(facts["frame_count"], 18)
            self.assertAlmostEqual(
                facts["duration"],
                source["duration"],
                delta=0.1,
            )
            self.assertTrue(facts["has_audio"])

        published_files = tuple((self.root / "published").iterdir())
        self.assertEqual(len(published_files), 4)
        self.assertTrue(
            all(path.suffix == ".mp4" and not path.name.startswith(".") for path in published_files)
        )

    async def test_runtime_failure_points_to_the_local_environment_installer(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is required for motion runtime guidance integration")
        script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
function productionFunction(name){
    const match = new RegExp(`(?:async )?function ${name}\\(`).exec(source);
    if(!match) process.exit(2);
    const start = match.index;
    const next = /\n(?:async )?function \w+\(/g;
    next.lastIndex = start + match[0].length;
    const following = next.exec(source);
    return source.slice(start, following ? following.index : source.length);
}
const guidance = 'Local motion extraction is unavailable. Run 安装动作提取环境.bat, then restart the app.';
const context = {
    tr:key => key === 'canvas.motionRuntimeUnavailable' ? guidance : key,
    render:() => {},
    scheduleSave:() => {},
    JSON,
    Math,
    String,
    Number,
    Array,
    Object,
    RegExp,
    URL,
};
vm.createContext(context);
const names = [
    'motionTaskIdIsSafe',
    'motionTaskIsTerminal',
    'motionTaskIsPolling',
    'motionTaskStateIsKnown',
    'motionTaskSafeState',
    'motionTaskSafeStage',
    'motionTaskSafeBranchState',
    'motionTaskCanTransition',
    'motionTaskSafeUrl',
    'motionTaskSafeMessage',
    'motionTaskPersist',
    'applyCanvasMotionTask',
];
vm.runInContext(
    names.map(productionFunction)
        .concat(names.map(name => `this.${name} = ${name};`))
        .join('\n'),
    context,
);
const node = {
    id:'motion-1',
    type:'motionExtract',
    motionTaskId:'canvas_motion_11111111111111111111111111111111',
    motionState:'running',
    motionStage:'depth',
    motionProgress:50,
    depthState:'running',
    depthUrl:'',
    depthError:'',
    poseState:'disabled',
    poseUrl:'',
    poseError:'',
    motionWarnings:[],
    motionError:'',
};
context.applyCanvasMotionTask(node, {
    task_id:node.motionTaskId,
    state:'failed',
    stage:'failed',
    progress:100,
    depth_state:'failed',
    depth_error:'Local motion processing failed.',
    pose_state:'disabled',
    warnings:[],
});
process.stdout.write(JSON.stringify({
    safe:context.motionTaskSafeMessage('Local motion processing failed.'),
    branch:node.depthError,
    task:node.motionError,
}));
"""
        result = subprocess.run(
            [
                node,
                "-e",
                script,
                str(ROOT / "static" / "js" / "canvas.js"),
            ],
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(
            result.returncode,
            0,
            "runtime guidance integration harness failed",
        )
        expected = (
            "Local motion extraction is unavailable. "
            "Run 安装动作提取环境.bat, then restart the app."
        )
        self.assertEqual(
            json.loads(result.stdout.decode("utf-8")),
            {"safe": expected, "branch": expected, "task": expected},
        )


if __name__ == "__main__":
    unittest.main()
