# Canvas Motion Reference Extractor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local-GPU `动作提取` node that turns one video of at most 30 seconds into an independently connectable depth/white-model MP4 and, when enabled, an independently connectable pose/skeleton MP4.

**Architecture:** FastAPI owns a single-worker asynchronous GPU queue and delegates media/model work to a new `motion_extractor` package. FFmpeg decodes each source once into a task-scoped shared frame store; Video Depth Anything Small and DWPose consume that store independently, publish separate MP4 assets, and report partial success when only one branch succeeds. The canvas adds a backward-compatible optional `fromPort` connection field, a persistent motion-task node, and loop/cascade integration.

**Tech Stack:** Python 3, FastAPI/Pydantic, asyncio, PyTorch CUDA 12.8, Video Depth Anything Small, ONNX Runtime GPU, DWPose, NumPy, OpenCV headless, FFmpeg/FFprobe, vanilla JavaScript, Node.js built-in test runner, Python `unittest`.

## Global Constraints

- Preserve all pre-existing uncommitted work. In particular, do not overwrite or stage unrelated changes already present in `static/js/canvas.js` or `tests/canvas-loop-llm-images.test.js`.
- Never read, print, stage, or commit `.env`, API keys, model checkpoints, source videos, decoded frames, generated MP4 files, or task-local absolute paths.
- Keep existing connections without `fromPort` byte-compatible when canvases are saved and reloaded.
- Resolve incoming media with the existing safe `output_file_from_url()` helper before any background work starts.
- Store downloaded model/source files under `data/motion_models/`; store task temporaries under an application-owned randomized temporary directory; publish only browser-safe `/assets/output/...` URLs.
- Run one GPU motion task at a time. Do not add a cloud fallback or provider/API selection to this node.
- Default node state is exactly: depth enabled, pose disabled, preserve audio disabled.
- Treat a disabled branch, failed branch, and successful branch as three distinct states; never substitute one result for another.
- Use test-first changes and make the smallest commit that satisfies each task.

---

## Task 1: Add the optional local motion runtime and verified model cache

**Files:**

- Create: `requirements-motion.txt`
- Create: `安装动作提取环境.bat`
- Create: `motion_extractor/__init__.py`
- Create: `motion_extractor/models.py`
- Create: `tests/test_motion_model_cache.py`
- Modify: `.gitignore`

**Runtime and model contracts:**

```python
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

def inspect_motion_runtime(cache_root: Path) -> MotionRuntimeStatus: ...
def ensure_motion_assets(
    cache_root: Path,
    progress: Callable[[str, float], None],
    cancelled: Callable[[], bool],
) -> dict[str, Path]: ...
```

Use these exact pinned upstream sources and weights:

- Video Depth Anything source: `https://github.com/DepthAnything/Video-Depth-Anything.git` at commit `4f5ae23172ba60fd7bc11ef671cca678842c7072`.
- Video Depth Anything Small weight: Hugging Face repository `depth-anything/Video-Depth-Anything-Small`, file `video_depth_anything_vits.pth`, SHA-256 `13379300b739e659f076a59d52e9801bd8d38c541a7e71f73bbca4dcfb013609`.
- DWPose source: `https://github.com/IDEA-Research/DWPose.git` at commit `3dca5db79d9f9ffdd378753ddf6ec66535aace88`.
- DWPose pose weight: Hugging Face repository `yzd-v/DWPose`, file `dw-ll_ucoco_384.onnx`, SHA-256 `724f4ff2439ed61afb86fb8a1951ec39c6220682803b4a8bd4f598cd913b1843`.
- DWPose detector weight: Hugging Face repository `yzd-v/DWPose`, file `yolox_l.onnx`, SHA-256 `7860ae79de6c89a3c1eb72ae9a2756c0ccfbe04b7791bb5880afabd97855a411`.

- [ ] **Step 1: Write failing cache-integrity tests**

Test all of the following in `tests/test_motion_model_cache.py`:

1. a valid pre-existing file is reused without calling the downloader;
2. a wrong hash is deleted and downloaded again;
3. an interrupted `.part` file is never accepted;
4. a completed download is atomically renamed only after its SHA-256 matches;
5. a cancelled download raises `MotionCancelled` and leaves no valid-looking final file;
6. a source checkout whose `HEAD` differs from the pinned commit is rejected;
7. serialized runtime status contains package/model names but no absolute path.

- [ ] **Step 2: Run the tests and confirm the expected failure**

Run:

```powershell
.venv\Scripts\python.exe tests\test_motion_model_cache.py
```

Expected: import failure for `motion_extractor.models`.

- [ ] **Step 3: Implement dependency and model manifests**

`requirements-motion.txt` must contain bounded compatible dependencies and no packages needed by the base app:

```text
numpy>=1.26,<3
opencv-python-headless>=4.10,<5
onnxruntime-gpu>=1.22,<2
huggingface-hub>=0.30,<2
imageio>=2.37,<3
imageio-ffmpeg>=0.6,<1
einops>=0.8,<1
easydict>=1.13,<2
tqdm>=4.67,<5
```

`安装动作提取环境.bat` must:

1. resolve `.venv\Scripts\python.exe` relative to the batch file;
2. stop with a Chinese message when the venv does not exist;
3. install `torch==2.11.0` and `torchvision==0.26.0` from `https://download.pytorch.org/whl/cu128`;
4. install `requirements-motion.txt`;
5. verify `torch.cuda.is_available()` and that `CUDAExecutionProvider` is present in ONNX Runtime;
6. never accept shell text, paths, or credentials from the browser.

In `models.py`, download to `<filename>.part`, hash the file in chunks, then atomically replace the destination. Clone/check out sources only at the pinned commits and verify `git rev-parse HEAD` before adding them to `sys.path`. Do not execute a moving branch.

- [ ] **Step 4: Ignore runtime artifacts**

Add these exact patterns without disturbing current ignore rules:

```gitignore
data/motion_models/
data/motion_tasks/
assets/output/motion/
```

- [ ] **Step 5: Run the focused tests**

Run:

```powershell
.venv\Scripts\python.exe tests\test_motion_model_cache.py
```

Expected: all model-cache tests pass without downloading real weights.

- [ ] **Step 6: Commit only Task 1 files**

```powershell
git add -- requirements-motion.txt "安装动作提取环境.bat" motion_extractor/__init__.py motion_extractor/models.py tests/test_motion_model_cache.py .gitignore
git commit -m "feat: add verified motion extraction runtime"
```

---

## Task 2: Build safe media probing, one-time decode, and compatible encoding

**Files:**

- Create: `motion_extractor/media.py`
- Create: `tests/test_motion_media.py`

**Media contracts:**

```python
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

class SharedFrameStore:
    metadata: VideoMetadata
    frames: np.memmap  # RGB uint8, shape (N, H, W, 3)

def probe_video(path: Path) -> VideoMetadata: ...
def decode_video_once(path: Path, work_dir: Path) -> SharedFrameStore: ...
def encode_rgb_frames(
    frames: Iterable[np.ndarray],
    metadata: VideoMetadata,
    destination: Path,
    source_path: Path,
    preserve_audio: bool,
) -> EncodeResult: ...
```

- [ ] **Step 1: Write failing synthetic-media tests**

Create a three-second FFmpeg fixture with non-square dimensions, 12 fps, and a sine-wave audio stream. Test:

1. corrupt and missing files fail before decode with a sanitized `MotionMediaError`;
2. duration over `30.0` seconds is rejected;
3. a rotated source reports display dimensions and rotation correctly;
4. the shared raw frame store has exactly one frame sequence and its byte length is divisible by `width * height * 3`;
5. default encoding has H.264 video, `yuv420p`, matching frame count/fps/display dimensions, and no audio stream;
6. `preserve_audio=True` produces an audio stream;
7. an MP4-incompatible source audio codec is transcoded to AAC with `audio_transcoded=True` rather than silently dropping audio;
8. temporary raw frames are removed when the context manager exits.

- [ ] **Step 2: Run the tests and confirm failure**

```powershell
.venv\Scripts\python.exe tests\test_motion_media.py
```

Expected: import failure for `motion_extractor.media`.

- [ ] **Step 3: Implement FFprobe validation and one-time FFmpeg decode**

Use `subprocess.run()` with argument arrays, never `shell=True`. Normalize FFprobe rational values without `eval()`. Decode once to an RGB24 raw file, derive the actual decoded frame count from the raw byte size, and expose it with `numpy.memmap`; do not hold a second complete frame array in memory.

Reject before GPU allocation:

- duration greater than 30 seconds;
- zero/unknown dimensions or fps;
- zero decoded frames;
- an unsafe or unreadable path;
- a decoded byte count that is not a whole number of RGB frames.

- [ ] **Step 4: Implement streaming output encoding**

Feed RGB frames to FFmpeg over stdin and write a task-scoped temporary MP4 before atomic publication. Use:

```text
-c:v libx264 -pix_fmt yuv420p -movflags +faststart
```

When audio preservation is enabled, first attempt stream copy; if the audio codec is incompatible with MP4, transcode that source stream to AAC and report the fallback in `EncodeResult`. Never change video timing to fit audio.

- [ ] **Step 5: Run the focused tests**

```powershell
.venv\Scripts\python.exe tests\test_motion_media.py
```

Expected: all media tests pass.

- [ ] **Step 6: Commit Task 2**

```powershell
git add -- motion_extractor/media.py tests/test_motion_media.py
git commit -m "feat: add shared motion media pipeline"
```

---

## Task 3: Implement clip-consistent Video Depth Anything processing

**Files:**

- Create: `motion_extractor/errors.py`
- Create: `motion_extractor/depth.py`
- Create: `tests/test_motion_depth.py`

**Depth contracts:**

```python
@dataclass(frozen=True)
class BranchResult:
    state: Literal["completed", "failed", "cancelled"]
    output_path: Path | None
    warning: str | None = None

class DepthProcessor:
    def run(
        self,
        frame_store: SharedFrameStore,
        output_path: Path,
        progress: Callable[[float], None],
        cancelled: Callable[[], bool],
        input_size: int = 518,
    ) -> BranchResult: ...
```

- [ ] **Step 1: Write failing processor tests with a fake model**

Tests must prove:

1. inference receives FP16/CUDA mode when CUDA is available;
2. all inferred depth frames are written to one float16 memmap, not retained as a Python list of full-size arrays;
3. normalization uses clip-level robust bounds shared by all frames, so equal depths render to equal grayscale values across frames;
4. near depth renders brighter and far depth darker;
5. output frames return to source display dimensions;
6. cancellation is checked between inference windows and before encoding;
7. CUDA OOM is converted to the typed `MotionOutOfMemory` exception;
8. progress is monotonic from inference through encode.

- [ ] **Step 2: Run and confirm failure**

```powershell
.venv\Scripts\python.exe tests\test_motion_depth.py
```

Expected: import failure for `motion_extractor.depth`.

- [ ] **Step 3: Implement the pinned VDA Small adapter**

Load only `Video-Depth-Anything-Small` using the verified source and weight paths from Task 1. Use the upstream `VideoDepthAnything` small configuration and offline `infer_video_depth(..., input_size=518, fp32=False)` path. Never load Base or Large checkpoints.

Write raw relative-depth results to a float16 task-local memmap. In a second pass, calculate clip-level robust bounds and render one-channel depth to three-channel grayscale. Keep normalization fixed for the entire clip to avoid brightness pumping. Upscale only the rendered depth to the source display size.

- [ ] **Step 4: Add typed, sanitized errors**

`errors.py` must define:

```python
class MotionError(Exception): ...
class MotionValidationError(MotionError): ...
class MotionMediaError(MotionError): ...
class MotionRuntimeError(MotionError): ...
class MotionOutOfMemory(MotionRuntimeError): ...
class MotionCancelled(MotionError): ...
```

Map raw CUDA messages and stack traces to stable Chinese user messages at the service boundary; never serialize exception reprs.

- [ ] **Step 5: Run tests**

```powershell
.venv\Scripts\python.exe tests\test_motion_depth.py
```

Expected: all depth tests pass using the fake model and no real checkpoint.

- [ ] **Step 6: Commit Task 3**

```powershell
git add -- motion_extractor/errors.py motion_extractor/depth.py tests/test_motion_depth.py
git commit -m "feat: add depth white-model processor"
```

---

## Task 4: Implement all-person DWPose extraction and gap-safe smoothing

**Files:**

- Create: `motion_extractor/pose.py`
- Create: `tests/test_motion_pose.py`

**Pose contracts:**

```python
@dataclass(frozen=True)
class PoseFrame:
    people: tuple[PersonPose, ...]

def smooth_pose_sequence(
    frames: Sequence[PoseFrame],
    confidence_threshold: float,
) -> list[PoseFrame]: ...

def render_pose_frame(
    frame: PoseFrame,
    width: int,
    height: int,
) -> np.ndarray: ...

class PoseProcessor:
    def run(
        self,
        frame_store: SharedFrameStore,
        output_path: Path,
        progress: Callable[[float], None],
        cancelled: Callable[[], bool],
    ) -> BranchResult: ...
```

- [ ] **Step 1: Write failing fake-session tests**

Test:

1. both verified ONNX files are loaded with `CUDAExecutionProvider` first and CPU as an explicit fallback only when CUDA provider initialization fails;
2. every detected person is retained, not just the highest-score box;
3. body, feet, hands, and face keypoints are rendered when confident;
4. black is the only background color;
5. smoothing changes valid adjacent coordinates but never invents a keypoint on a missing frame;
6. a completely missed frame is fully black, not a copy of the previous pose;
7. no-person clips complete with a warning rather than throwing;
8. cancellation and progress behavior match the depth processor.

- [ ] **Step 2: Run and confirm failure**

```powershell
.venv\Scripts\python.exe tests\test_motion_pose.py
```

Expected: import failure for `motion_extractor.pose`.

- [ ] **Step 3: Implement adjacent-frame tracking and smoothing**

Associate people only between adjacent frames using bounding-box IoU plus normalized keypoint distance. End a track immediately on a missed frame; do not bridge gaps and do not perform identity re-identification across shots. Smooth only coordinates present in the current frame and its valid immediate neighbors.

- [ ] **Step 4: Implement low-semantic pose rendering**

Render thin light-gray/white bones and joints on RGB black. Preserve original frame dimensions, timing, and composition. Do not render names, boxes, confidence text, source pixels, watermarks, or colored semantic regions.

- [ ] **Step 5: Run tests**

```powershell
.venv\Scripts\python.exe tests\test_motion_pose.py
```

Expected: all pose tests pass.

- [ ] **Step 6: Commit Task 4**

```powershell
git add -- motion_extractor/pose.py tests/test_motion_pose.py
git commit -m "feat: add all-person pose processor"
```

---

## Task 5: Add the single-worker task service and FastAPI endpoints

**Files:**

- Create: `motion_extractor/service.py`
- Create: `tests/test_canvas_motion_tasks.py`
- Modify: `main.py`

**HTTP contract:**

```python
class CanvasMotionTaskRequest(BaseModel):
    source_url: str
    depth_enabled: bool = True
    pose_enabled: bool = False
    preserve_audio: bool = False

# POST /api/canvas-motion-tasks -> 202
{
  "task_id": "...",
  "state": "queued",
  "stage": "queued",
  "progress": 0.0,
  "queue_position": 1,
  "depth_state": "pending",
  "depth_url": null,
  "depth_error": null,
  "pose_state": "disabled",
  "pose_url": null,
  "pose_error": null,
  "warnings": [],
  "low_memory_retry": False
}

# GET /api/canvas-motion-tasks/{task_id}
# POST /api/canvas-motion-tasks/{task_id}/cancel
```

Allowed states are exactly `queued`, `downloading`, `running`, `partial`, `completed`, `failed`, and `cancelled`. Branch states are exactly `disabled`, `pending`, `running`, `completed`, `failed`, and `cancelled`.

- [ ] **Step 1: Write failing API and state-machine tests**

Use `unittest.IsolatedAsyncioTestCase` and injected fake processors. Cover:

1. missing/unsafe/non-local `source_url` is rejected through `output_file_from_url()`;
2. both processors disabled returns HTTP 422;
3. corrupt or over-30-second video fails before queueing GPU work;
4. POST returns immediately with a queued task;
5. two submissions execute in FIFO order with at most one active processor job;
6. depth success plus pose failure yields `partial` and keeps only `depth_url`;
7. cancellation stops an active task, removes incomplete files, and preserves a branch already atomically published;
8. exactly one retry occurs after `MotionOutOfMemory`, using lower depth input size `392`;
9. a second OOM becomes a sanitized branch failure;
10. a completed response contains `/assets/output/motion/...` URLs and no raw Windows path, exception traceback, or secret-shaped values;
11. unknown task IDs return 404;
12. disabled branches are not counted as failures.

- [ ] **Step 2: Run and confirm failure**

```powershell
.venv\Scripts\python.exe tests\test_canvas_motion_tasks.py
```

Expected: route/service imports or endpoint assertions fail.

- [ ] **Step 3: Implement the task service**

`MotionTaskService` owns:

- one lazy `asyncio.Queue`;
- one worker coroutine;
- task public records protected by an `asyncio.Lock`;
- private, non-serialized source/output paths;
- a per-task cancellation event;
- independent branch execution/results;
- task-scoped cleanup in `finally`.

Run blocking inference with `asyncio.to_thread()`. Decode once before either branch and pass the same `SharedFrameStore` to both enabled processors. Execute branches sequentially on the single GPU worker to stay within the 16 GB VRAM budget, and release each model/session before loading the next branch.

Overall progress ranges:

- model preparation: `0–10`;
- shared decode: `10–20`;
- depth: its proportional share of `20–85`;
- pose: its proportional share of `20–85`;
- encode/publish: `85–100`.

- [ ] **Step 4: Wire safe routes into `main.py`**

Resolve and preflight `source_url` synchronously in POST. Pass a `Path` privately to the service, but persist only the safe original application URL in the public task record. Publish files below `OUTPUT_OUTPUT_DIR / "motion"` and return `/assets/output/motion/<randomized-name>.mp4`.

Do not add any API provider, provider default, or key field.

- [ ] **Step 5: Run focused and existing backend tests**

```powershell
.venv\Scripts\python.exe tests\test_canvas_motion_tasks.py
.venv\Scripts\python.exe tests\test_canvas_video_tasks.py
```

Expected: new task tests pass and existing video task behavior remains green.

- [ ] **Step 6: Commit Task 5**

```powershell
git add -- motion_extractor/service.py tests/test_canvas_motion_tasks.py main.py
git commit -m "feat: expose queued canvas motion tasks"
```

---

## Task 6: Add backward-compatible named output ports to canvas connections

**Files:**

- Modify: `static/js/canvas.js`
- Modify: `static/css/canvas.css`
- Create: `tests/canvas-motion-ports.test.js`

**Connection contract:**

```js
// Legacy connection, unchanged
{ id, from, to }

// Named motion connection
{ id, from, to, fromPort: 'depth' }

function nodeOutputPorts(node) {}
function normalizedFromPort(connection) {}
function connectionKey(fromId, toId, fromPort = '') {}
function portPoint(nodeId, kind, portName = '') {}
function startLink(event, originId, originKind, originPort = '') {}
```

- [ ] **Step 1: Write failing source-extracted JavaScript tests**

Follow the repository's existing `vm`-based test pattern. Assert:

1. old connections normalize to an empty port and remain valid;
2. `depth` and `pose` connections between the same node pair are not deduplicated;
3. an exact duplicate `(from, to, fromPort)` is rejected;
4. JSON save/reload preserves `fromPort`;
5. `portPoint()` returns distinct anchors for `DEPTH` and `POSE`;
6. link rendering reads `connection.fromPort`;
7. named-port dragging never writes `fromPort` to unrelated legacy nodes.

- [ ] **Step 2: Run and confirm failure**

```powershell
node tests\canvas-motion-ports.test.js
```

Expected: named-port helper assertions fail.

- [ ] **Step 3: Implement generic named output support**

Change link creation, duplicate detection, hit testing, and SVG path anchoring to accept an optional output-port name. Keep `fromPort` omitted, not `""`, for legacy nodes. Do not change `to`-port semantics in this scope.

Render multiple right-side output anchors only when `nodeOutputPorts(node)` returns named ports. Add stable DOM attributes:

```html
data-port-kind="out"
data-port-name="depth"
```

- [ ] **Step 4: Style named ports without changing existing node geometry**

Add compact labels and separate vertical anchors for `DEPTH` and `POSE`. Disabled ports use a visually disabled class and cannot start a drag.

- [ ] **Step 5: Run named-port and legacy connection tests**

```powershell
node tests\canvas-motion-ports.test.js
node tests\canvas-middle-button-pan.test.js
node tests\canvas-openrouter-video-reference.test.js
```

Expected: all tests pass.

- [ ] **Step 6: Commit only intended hunks**

Review `git diff -- static/js/canvas.js` carefully because that file already had user changes before this plan. Stage only Task 6 hunks plus the new CSS/test:

```powershell
git add -p -- static/js/canvas.js
git add -- static/css/canvas.css tests/canvas-motion-ports.test.js
git commit -m "feat: support named canvas output ports"
```

---

## Task 7: Add the `动作提取` node UI, defaults, and input resolver

**Files:**

- Modify: `static/canvas.html`
- Modify: `static/js/canvas.js`
- Modify: `static/css/canvas.css`
- Modify: `static/js/i18n/canvas.js`
- Create: `tests/canvas-motion-node.test.js`

**Node state contract:**

```js
{
  id,
  type: 'motionExtract',
  x, y, w, h,
  depthEnabled: true,
  poseEnabled: false,
  preserveAudio: false,
  motionTaskId: '',
  motionState: 'idle',
  motionStage: '',
  motionProgress: 0,
  depthState: 'pending',
  depthUrl: '',
  poseState: 'disabled',
  poseUrl: '',
  motionWarnings: [],
  motionError: ''
}
```

- [ ] **Step 1: Write failing node/default/input tests**

Assert:

1. `addMotionExtractNode()` creates exactly the confirmed defaults;
2. turning the only enabled processor off is prevented with a Chinese validation message;
3. pose may be enabled independently and both may be enabled together;
4. audio defaults off and can be toggled without changing either processor;
5. one incoming video resolves successfully;
6. zero videos and more than one video produce different actionable validation messages;
7. loop context uses exactly the current video item from `loopInputVideoRefs()`;
8. image-only inputs are rejected rather than converted or silently ignored;
9. a downstream read from `depth` returns only `depthUrl`, and a read from `pose` returns only `poseUrl`;
10. disabled/failed/missing branches resolve to no media and a branch-specific error.

- [ ] **Step 2: Run and confirm failure**

```powershell
node tests\canvas-motion-node.test.js
```

Expected: node constructor and resolver are missing.

- [ ] **Step 3: Add the toolbar/menu entry and constructor**

Add `动作提取` with subtitle `DEPTH · POSE` and badge `本地 GPU`. Assign a default size large enough for one source preview, three switches, progress, two result previews, warnings, and action buttons.

- [ ] **Step 4: Render controls and enforce switch invariants**

The node body must show:

- resolved source thumbnail, filename, duration, dimensions, and fps;
- switches `深度白模`, `骨骼姿势`, `保留原音频`;
- progress stage, percent, and queue position;
- `开始提取`, `取消`, or `重试` according to state;
- separate depth and pose result cards;
- non-fatal warnings.

When a processor is disabled, clear its stale URL and mark its named port disabled.

- [ ] **Step 5: Implement exact single-video input resolution**

Add `motionInputVideoRefs(node, context)` that reads incoming video-capable nodes and the active loop item. It must not silently pick the first of multiple videos. Extend the shared media resolver so a connection from a motion named port returns only that port's completed MP4.

- [ ] **Step 6: Add i18n entries**

Add all new labels/errors under the existing canvas namespace in both supported languages. Do not hard-code mojibake or duplicate English fallback text in render functions.

- [ ] **Step 7: Run focused UI tests**

```powershell
node tests\canvas-motion-node.test.js
node tests\canvas-loop-llm-images.test.js
```

Expected: the new test and the existing loop/LLM image regression pass.

- [ ] **Step 8: Commit Task 7**

```powershell
git add -p -- static/js/canvas.js
git add -- static/canvas.html static/css/canvas.css static/js/i18n/canvas.js tests/canvas-motion-node.test.js
git commit -m "feat: add canvas motion extraction node"
```

---

## Task 8: Implement frontend task lifecycle, cancellation, and reload recovery

**Files:**

- Modify: `static/js/canvas.js`
- Create: `tests/canvas-motion-task-lifecycle.test.js`

**Frontend task contracts:**

```js
async function createCanvasMotionTask(node, sourceUrl) {}
async function pollCanvasMotionTask(nodeId, taskId) {}
async function cancelCanvasMotionTask(nodeId) {}
function applyCanvasMotionTask(node, payload) {}
function resumePendingCanvasMotionTasks() {}
async function runMotionExtractNode(nodeId, context = {}) {}
```

- [ ] **Step 1: Write failing lifecycle tests with mocked `fetch`**

Cover:

1. POST sends only `source_url`, `depth_enabled`, `pose_enabled`, and `preserve_audio`;
2. POST never sends provider names, API keys, data URLs, raw paths, or image fields;
3. queued/running updates are monotonic and queue position is displayed;
4. `partial` stores only successful branch URLs and preserves failed-branch error text;
5. completed task maps depth and pose independently;
6. cancel calls the correct endpoint and stops local polling;
7. reload with a saved non-terminal `motionTaskId` polls GET instead of creating a second POST;
8. a stale poll response cannot overwrite a newer retry, using a per-run token;
9. terminal tasks stop polling;
10. saving the canvas persists task ID/status/URLs but no raw path.

- [ ] **Step 2: Run and confirm failure**

```powershell
node tests\canvas-motion-task-lifecycle.test.js
```

Expected: lifecycle helpers are missing.

- [ ] **Step 3: Implement create/poll/apply/cancel**

Use the existing canvas task polling conventions. Poll with bounded backoff while the task is `queued`, `downloading`, or `running`. Save node state after every meaningful transition, but avoid saving on every identical poll.

When task creation returns a non-2xx response, display its sanitized message inside the node and leave the upstream source untouched.

- [ ] **Step 4: Resume saved tasks during canvas load**

After nodes and connections are restored, scan `motionExtract` nodes with a non-terminal `motionTaskId` and restart GET polling. Never POST as part of recovery.

- [ ] **Step 5: Run lifecycle and task regression tests**

```powershell
node tests\canvas-motion-task-lifecycle.test.js
node tests\canvas-video-task-lifecycle.test.js
```

Expected: both motion and existing video task tests pass.

- [ ] **Step 6: Commit Task 8**

```powershell
git add -p -- static/js/canvas.js
git add -- tests/canvas-motion-task-lifecycle.test.js
git commit -m "feat: persist canvas motion task lifecycle"
```

---

## Task 9: Integrate video loops and one-click cascade without cross-item contamination

**Files:**

- Modify: `static/js/canvas.js`
- Modify: `static/js/i18n/canvas.js`
- Create: `tests/canvas-motion-loop-cascade.test.js`

- [ ] **Step 1: Write failing loop and cascade tests**

Test:

1. the loop UI no longer forces `node.videoInput = false`;
2. enabling video input exposes and persists `videoBatchSize`;
3. each loop iteration resolves only its current video;
4. two input videos produce two motion task POSTs in source order;
5. one failed item is recorded and the next item still runs;
6. retry resubmits only the failed item;
7. one-click cascade recognizes `motionExtract` as runnable;
8. downstream generation waits for the selected branch to complete;
9. cascade from disabled/failed `pose` stops with a pose-specific error even if depth succeeded;
10. depth and pose output connections cannot exchange or merge their URLs;
11. backend queue state does not cause duplicate frontend submissions.

- [ ] **Step 2: Run and confirm failure**

```powershell
node tests\canvas-motion-loop-cascade.test.js
```

Expected: motion loop/cascade assertions fail.

- [ ] **Step 3: Enable the already-modeled loop video input**

`addLoopNode()` and `loopInputVideoRefs()` already carry video fields. Remove the render-time assignment that resets `videoInput` to false, expose the existing video toggle and batch-size control, and preserve all current image/prompt loop behavior.

- [ ] **Step 4: Add motion execution to cascade dispatch**

Keep `CANVAS_GENERATOR_TYPES` limited to generation nodes. Introduce or extend a runnable-type helper so `motionExtract` participates in dependency ordering without being treated as an image/video generator provider. Add `motionExtract` to `runCascadeNodeByType()` and wait on its selected named branch before dispatching its child.

- [ ] **Step 5: Preserve loop item identity**

Attach the loop iteration ID/index to frontend execution context, not to the backend public API. Store each item's `taskId`, selected branch URLs, status, and error under that item. Do not reuse the node-level result from a preceding item.

- [ ] **Step 6: Run loop/cascade and prior loop tests**

```powershell
node tests\canvas-motion-loop-cascade.test.js
node tests\canvas-loop-llm-images.test.js
```

Also run every existing loop/cascade test:

```powershell
$tests = rg -l "runCascadeNodeByType|loopContext|loopInputVideoRefs" tests
foreach ($test in $tests) { node $test }
```

Expected: all tests pass, including the user's pre-existing loop/LLM image regression.

- [ ] **Step 7: Commit Task 9 without absorbing prior unrelated changes**

```powershell
git add -p -- static/js/canvas.js
git add -- static/js/i18n/canvas.js tests/canvas-motion-loop-cascade.test.js
git commit -m "feat: run motion extraction in loops and cascades"
```

If `tests/canvas-loop-llm-images.test.js` is still an unrelated pre-existing untracked file, do not stage it in this commit.

---

## Task 10: Add end-to-end media verification, runtime guidance, and final regression

**Files:**

- Create: `tests/test_motion_pipeline_integration.py`
- Modify: `README.md`
- Modify: `static/js/canvas.js`
- Modify: `static/js/i18n/canvas.js`

- [ ] **Step 1: Write an integration test using fake processors and real FFmpeg**

The test must:

1. create a short portrait video with two moving geometric subjects and audio;
2. run the service with both branches enabled;
3. verify two distinct MP4 files and URLs;
4. verify source width, height, fps, frame count, duration tolerance, and orientation;
5. verify both outputs omit audio by default;
6. rerun with audio enabled and verify both outputs contain audio;
7. assert the depth file contains grayscale frames;
8. assert the pose file has a black background and non-black skeleton pixels;
9. assert task and log-facing payloads contain no temporary absolute path;
10. assert all task-local raw frame/depth files are cleaned up.

- [ ] **Step 2: Run and confirm any missing integration behavior**

```powershell
.venv\Scripts\python.exe tests\test_motion_pipeline_integration.py
```

Expected before final fixes: at least one end-to-end assertion exposes any remaining wiring gap.

- [ ] **Step 3: Add runtime-unavailable guidance**

When dependencies or model assets are missing, the node must display a concise message that points to `安装动作提取环境.bat`. Do not run package installation automatically from an API endpoint. The first extraction may enter `downloading`, show model name/progress, and continue after verified weights finish.

Document in `README.md`:

- supported NVIDIA/Windows local setup;
- the one-time environment installer;
- first-use model download size and local cache location without an absolute user path;
- default depth-only/no-audio behavior;
- how to enable pose and audio;
- the 30-second limit;
- named `DEPTH`/`POSE` connections;
- how to cancel and retry;
- that media never falls back to a cloud API.

- [ ] **Step 4: Run all focused suites**

```powershell
.venv\Scripts\python.exe tests\test_motion_model_cache.py
.venv\Scripts\python.exe tests\test_motion_media.py
.venv\Scripts\python.exe tests\test_motion_depth.py
.venv\Scripts\python.exe tests\test_motion_pose.py
.venv\Scripts\python.exe tests\test_canvas_motion_tasks.py
.venv\Scripts\python.exe tests\test_motion_pipeline_integration.py
node tests\canvas-motion-ports.test.js
node tests\canvas-motion-node.test.js
node tests\canvas-motion-task-lifecycle.test.js
node tests\canvas-motion-loop-cascade.test.js
```

Expected: every focused suite passes.

- [ ] **Step 5: Run project regressions**

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
$jsTests = Get-ChildItem -LiteralPath tests -Filter "*.test.js" | Sort-Object Name
foreach ($test in $jsTests) { node $test.FullName }
```

Expected: no existing backend, OpenRouter, RunningHub, loop, connection, or canvas interaction regression.

- [ ] **Step 6: Perform target-machine manual acceptance**

On the RTX 5080 machine:

1. run `安装动作提取环境.bat` and restart the app;
2. add a source video shorter than 30 seconds;
3. create `动作提取` and confirm the initial state is depth on, pose off, audio off;
4. run and confirm only `DEPTH` publishes a playable MP4;
5. enable pose and confirm two independent playable outputs;
6. enable audio and inspect both MP4 streams with FFprobe;
7. connect each named output to a separate downstream video node and verify its reference preview identifies the selected role without submitting a paid generation;
8. run two short videos through a loop and verify order;
9. refresh during a running job and verify polling resumes the same task;
10. cancel a queued and an active task and verify no orphan process or partial temporary file remains.

- [ ] **Step 7: Inspect repository hygiene**

```powershell
git status --short
git diff --check
git ls-files | rg -i '(\.env|\.pth$|\.onnx$|motion_models|motion_tasks|assets/output/motion|\.mp4$)'
rg -n --hidden -g '!/.git/**' -g '!data/motion_models/**' '(sk-[A-Za-z0-9_-]{12,}|OPENROUTER_API_KEY\s*=|API_PROVIDER_.*_KEY\s*=)' .
```

Expected:

- no new secret, checkpoint, generated media, or temp-task file is tracked;
- secret scan returns no newly introduced credential;
- only intended source, test, docs, dependency, and installer files remain in the feature diff.

- [ ] **Step 8: Commit final integration and docs**

```powershell
git add -- tests/test_motion_pipeline_integration.py README.md
git add -p -- static/js/canvas.js static/js/i18n/canvas.js
git commit -m "test: verify canvas motion extraction workflow"
```

---

## Final Acceptance Checklist

- [ ] A new node defaults to depth only and no audio.
- [ ] Pose and audio switches are independently user-controlled.
- [ ] At least one visual processor always remains enabled.
- [ ] One source video is decoded once per task.
- [ ] Source timing, orientation, dimensions, frame count, and camera motion are preserved.
- [ ] Depth is clip-consistent grayscale with near brighter and far darker.
- [ ] Pose includes all detected people and never holds stale poses across missed frames.
- [ ] Depth and pose publish independent MP4 files and independent named ports.
- [ ] Existing connections without `fromPort` still behave identically.
- [ ] Batch loops preserve source order and isolate per-item results.
- [ ] One GPU task runs at a time; queued, cancel, retry, partial success, and one OOM retry are visible.
- [ ] Reload resumes polling the existing task rather than duplicating work.
- [ ] No provider key or cloud fallback exists for this feature.
- [ ] No key, model checkpoint, input video, generated MP4, raw path, or decoded frame is committed or leaked.
