# Infinite Canvas Motion Reference Extractor Design

## Goal

Add a local `动作提取` node to Infinite Canvas that converts one source video into reusable motion-reference videos:

- a temporally stable grayscale depth video;
- a whole-body pose video for every detectable person in the frame.

The outputs are intended to guide downstream video-generation nodes while reducing contamination from the source video's character identity, clothing, texture, and environment style.

## Confirmed Product Requirements

- The usual source video is 30 seconds or shorter.
- Preserve the source composition, people, foreground objects, camera movement, orientation, duration, frame count, frame rate, and output dimensions.
- Do not choose or crop a main subject. Pose extraction processes every detectable person.
- Provide independent switches for:
  - `深度白模`;
  - `骨骼姿态`;
  - `保留原音频`.
- Default settings:
  - depth enabled;
  - pose disabled;
  - source audio disabled.
- At least one of depth or pose must remain enabled.
- When both processors are enabled, produce two independent MP4 files.
- The depth and pose results must be independently connectable to downstream nodes.
- Batch-loop execution preserves source order and does not mix outputs between loop items.

## Selected Technical Approach

Use a local GPU pipeline rather than a cloud conversion API or a ComfyUI dependency.

### Depth model

Use the relative-depth `Video-Depth-Anything-Small` model in offline inference mode with FP16:

- the video-specific model provides better temporal consistency than running an image depth model independently on each frame;
- the Small checkpoint is suitable for the available NVIDIA RTX 5080 with 16 GB VRAM;
- its Apache-2.0 license is compatible with a commercial advertising workflow.

Do not use the Base or Large checkpoints by default because their published checkpoints use a non-commercial license.

Reference: <https://github.com/DepthAnything/Video-Depth-Anything>

### Pose model

Use the official DWPose ONNX pipeline:

- detect and render every person found in a frame;
- include body, hand, foot, and face keypoints where confidence permits;
- use a black background with low-semantic, light-colored bones and joints;
- apply light temporal smoothing to detected keypoints without changing timing or holding the previous pose over missing detections.

Reference: <https://github.com/IDEA-Research/DWPose>

### Media processing

Use the project's existing FFmpeg/FFprobe integration to:

- inspect and validate the source;
- decode the source once for all enabled processors;
- preserve the source timing, display orientation, dimensions, and frame count;
- encode H.264 MP4 outputs with broad playback compatibility;
- omit audio by default, or copy the original audio track when `保留原音频` is enabled.

## Canvas Node Design

### Node identity

- Node type: `motionExtract`
- Display name: `动作提取`
- Subtitle: `DEPTH · POSE`
- Runtime badge: `本地 GPU`

### Input

The node has one standard input port and accepts exactly one video for each execution. Compatible upstream sources include:

- a video media node;
- an Output node containing video media;
- a batch-loop item containing one video;
- another node whose selected output port resolves to one video.

If an upstream source contains more than one video outside a loop iteration, preflight fails and asks the user to select or loop the videos instead of silently choosing one.

### Controls

The node body contains:

1. source-video preview and metadata;
2. `深度白模` switch, enabled by default;
3. `骨骼姿态` switch, disabled by default;
4. `保留原音频` switch, disabled by default;
5. progress stage, percentage, and queue state;
6. `开始提取` / `取消` / `重试` action;
7. completed-result previews and non-fatal warnings.

Disabling a processor prevents its model from loading or running.

### Output ports

The node exposes two named output ports:

- `DEPTH`
- `POSE`

Only an enabled, successfully completed output port resolves to media. A disabled or failed branch cannot silently fall back to the other branch.

Canvas connections gain an optional field:

```json
{
  "from": "motion-node-id",
  "to": "video-generator-id",
  "fromPort": "depth"
}
```

Valid port values for this node are `depth` and `pose`. Existing connections without `fromPort` retain their current behavior, preserving old canvases.

When a downstream video node reads a named connection, it receives only the corresponding MP4. Connecting both ports to separate downstream nodes keeps the two reference types isolated.

## Task API and Persistence

Add an asynchronous local task API:

- `POST /api/canvas-motion-tasks`
- `GET /api/canvas-motion-tasks/{task_id}`
- `POST /api/canvas-motion-tasks/{task_id}/cancel`

The creation request contains:

- a safe local asset/output URL;
- `depth_enabled`;
- `pose_enabled`;
- `preserve_audio`.

It must not contain API credentials.

The task response reports:

- task state: `queued`, `downloading`, `running`, `partial`, `completed`, `failed`, or `cancelled`;
- active stage and overall percentage;
- sanitized error or warning messages;
- `depth_url` and `pose_url` independently;
- source/output media metadata;
- whether a low-memory retry occurred.

Task metadata is persisted using the project's existing canvas task pattern so reopening or refreshing a canvas resumes status polling instead of creating another task.

## Processing Pipeline

### 1. Preflight

Before allocating the GPU:

- resolve the source through the existing safe local media resolver;
- reject missing, unreadable, or unsupported files;
- probe duration, frame rate, frame count, dimensions, rotation, codecs, and audio presence;
- reject videos longer than 30 seconds;
- ensure at least one processor is enabled;
- confirm FFmpeg and the selected model runtime are available.

### 2. Model preparation

Model files are not committed to Git.

On first use, download missing model files into an application-owned local model cache. Show an explicit download stage and verify expected file integrity before inference. A failed or interrupted download remains recoverable and cannot be treated as a valid checkpoint.

### 3. Shared decode

Decode the source once and feed the resulting frame stream to every enabled processor. The implementation may use bounded frame batches, but must not duplicate a full decoded video in memory for each processor.

### 4. Depth processing

- Run Video Depth Anything in offline FP16 mode.
- Use relative rather than metric depth.
- Normalize depth consistently across the complete clip, not independently per frame.
- Render near regions brighter and far regions darker.
- Apply only the temporal stabilization needed to prevent visible brightness pumping.
- Preserve all scene geometry, including people, props, foreground occlusion, background depth, and camera motion.
- Do not add text, watermarks, colors, generated texture, or semantic labels.

Inference may use a bounded internal resolution for efficiency. The encoded result must return to the source display dimensions without changing timing or framing.

### 5. Pose processing

- Detect every person independently on every frame.
- Render confident whole-body, hand, foot, and face keypoints on black.
- Smooth valid keypoint trajectories lightly over time.
- If a person or keypoint is missing on a frame, omit it for that frame.
- Do not copy the previous frame's pose into a missed frame.
- If no person is detected, emit a black pose frame and record a warning rather than failing unrelated depth work.

### 6. Encode and publish

Encode each successful branch as an independent MP4/H.264 asset under the application's output storage:

- source frame rate;
- source frame count and duration within normal codec tolerance;
- source display orientation and dimensions;
- `yuv420p`-compatible playback;
- no audio track by default;
- copied source audio only when `preserve_audio` is enabled and an audio stream exists.

Return browser-safe application URLs, not raw filesystem paths.

## Queueing, Cancellation, and Batch Loops

- Run one local motion-extraction GPU task at a time by default.
- Additional jobs remain visibly queued.
- A batch loop submits one task per current loop item and preserves loop order.
- One failed loop item is recorded as failed without stopping subsequent items.
- Retry operates on the failed item only.
- Cancellation stops active inference, removes incomplete temporary frames and files, and preserves only branches that were already fully encoded and published.
- Closing or refreshing the page does not duplicate paid or GPU work; polling resumes from the saved task identifier.

## Partial Success and Error Handling

Depth and pose are independent result branches.

- If depth succeeds and pose fails, publish depth and mark the task `partial`.
- If pose succeeds and depth fails, publish pose and mark the task `partial`.
- A disabled branch is neither successful nor failed.
- No-person detection is a warning, not a pipeline failure.
- A corrupt video, unsupported media, excessive duration, missing model, or unsafe path fails before inference with a clear message.
- On CUDA out-of-memory, retry once at a lower internal inference resolution.
- If the retry fails, return a sanitized GPU-memory error without exposing stack traces or local paths.
- Never silently switch to a cloud API, another model, or the wrong output branch.

## Downstream Reference Behavior

- A connection from `DEPTH` supplies only the depth MP4.
- A connection from `POSE` supplies only the pose MP4.
- Downstream generator preflight displays the exact selected reference role and media count.
- One-click cascade waits for the selected motion branch to complete before submitting the downstream video task.
- If the selected branch is disabled, failed, or missing, cascade stops with a branch-specific message.
- The existing prompt should explain that the reference provides motion/spatial guidance and that the downstream model must not reproduce the grayscale or skeleton appearance. Prompt assistance can be added later, but is not required for this node to function.

## Security and Repository Hygiene

- Do not send source media to third parties.
- Do not log complete local paths, frame data, model tensors, or generated data URLs.
- Do not store API keys in node data, task data, logs, test fixtures, or commits.
- Do not commit downloaded checkpoints, decoded frames, temporary files, or generated MP4 files.
- Resolve every input and output path through existing safe application path helpers.
- Use randomized, task-scoped temporary directories and clean them after completion, failure, or cancellation.

## Compatibility

- Existing node types and saved canvases remain valid.
- Existing connections without `fromPort` behave exactly as before.
- Named output-port support is introduced generically but used only by `motionExtract` in this scope.
- Existing video generation, RunningHub, OpenRouter, OminiLink, and Output-node behavior remains unchanged unless receiving a new named motion output.
- The feature has no API-provider default and requires no provider key.

## Verification

### Frontend tests

- New nodes default to depth on, pose off, and audio off.
- The UI prevents both processors from being disabled.
- Named port connections serialize and reload correctly.
- Old connections without `fromPort` remain valid.
- Depth connections resolve only depth media; pose connections resolve only pose media.
- Disabled or failed branches cannot be connected as if complete.
- Batch-loop items preserve input/output order.
- Page reload resumes polling the same task.

### Backend tests

- Request validation rejects no processor, multiple source videos, unsafe paths, corrupt media, and duration over 30 seconds.
- Model downloads use the cache and reject incomplete or invalid files.
- A shared decode feeds enabled processors without two complete decodes.
- Depth normalization is clip-consistent.
- Pose gaps render empty frames rather than held poses.
- Audio is absent by default and copied only when requested.
- Partial success, cancellation, queueing, and one-time low-memory retry follow the specified state transitions.
- Responses never expose API keys or raw filesystem paths.

### Media integration tests

Use short synthetic fixtures to verify:

- output width, height, display orientation, frame rate, frame count, and duration;
- H.264 MP4 browser playback;
- audio absence and optional audio preservation;
- independent depth and pose files;
- cleanup of temporary artifacts.

### Manual acceptance

On the target RTX 5080 machine:

1. Add a source video shorter than 30 seconds.
2. Run the default node and confirm only a depth result is produced.
3. Enable pose and confirm a separate pose result is produced.
4. Toggle audio preservation and inspect the output streams.
5. Connect each named port to a separate downstream video node and confirm reference isolation without submitting a paid generation request.
6. Run two source videos through a loop and verify order, progress, cancellation, retry, and persisted task recovery.

## Non-Goals

- Reconstructing an editable 3D mesh or point cloud.
- Selecting, masking, or replacing a main character.
- Re-identifying people across unrelated shots.
- Generating new motion.
- Cloud inference fallback.
- Videos longer than 30 seconds.
- Automatic downstream prompt rewriting in the first version.
