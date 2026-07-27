# OpenRouter Video Reference Design

## Goal

Make Infinite Canvas video references reach OpenRouter video models, especially `bytedance/seedance-2.0`, without requiring users to manually upload every local clip to a public host. Prevent paid requests when the chosen reference mode would silently ignore connected media.

## Confirmed Root Cause

The canvas correctly classifies connected MP4 assets as video references and includes them in `CanvasVideoRequest.videos`. The OpenRouter branch in `/api/canvas-video` currently builds only `frame_images` and image-only `input_references`; it never reads `payload.videos` or `payload.audios`. OpenRouter therefore receives a prompt and optional image references but no reference video.

OpenRouter's normalized video API accepts image, audio, and video items through `input_references`. Video items use `type: "video_url"` with `video_url.url`; audio items use `type: "audio_url"` with `audio_url.url`.

## Selected Approach

Use a hybrid reference transport:

- Pass public HTTP(S) and existing data URLs through unchanged.
- Convert local `/assets/...` and `/output/...` video or audio files to MIME-correct data URLs on the backend.
- Preserve the existing optional cloud-upload workflow for users who prefer a public URL or encounter an upstream payload-size limit.
- Build one normalized OpenRouter request body in a dedicated helper so behavior is testable without making a paid request.

## Reference Mode Rules

The request builder distinguishes exact frame control from multimodal reference guidance.

1. Explicit `first_frame` or `last_frame` image roles produce `frame_images`.
2. When `multimodal` is enabled, unroled images, videos, and audios produce `input_references`.
3. When a video or audio reference is present, unroled images join `input_references` even if `multimodal` is false. This prevents an automatically promoted first frame from overriding the video reference.
4. With images only and `multimodal` disabled, the first unroled image retains the existing image-to-video behavior and becomes `first_frame`.
5. Explicit frame control cannot be combined with video or audio reference guidance because OpenRouter gives `frame_images` precedence. The backend rejects that combination before contacting OpenRouter and explains how to resolve it.

## Backend Components

### Media normalization

A focused helper converts a reference value into an OpenRouter-compatible URL:

- HTTP(S) and `data:` values are returned as-is.
- Local asset/output URLs are resolved through the existing safe local-path resolver and encoded as data URLs with the detected MIME type.
- Missing, unreadable, or unsupported local files raise a clear HTTP 400 error instead of being omitted silently.

### OpenRouter request builder

A pure or dependency-light helper builds:

- base fields: `model`, `prompt`, `duration`, aspect/size/resolution, audio generation, and seed;
- `frame_images` for explicit frame control;
- `input_references` containing normalized `image_url`, `video_url`, and `audio_url` items;
- a non-secret reference summary containing submitted image, video, and audio counts.

The `/api/canvas-video` route uses this helper only for OpenRouter, leaving APIMart, Volcengine, RunningHub, Yuli, Agnes, and generic providers unchanged.

## Frontend Feedback

The video node already separates image and video references. Add a compact preflight/status line for OpenRouter runs showing the reference counts that will be submitted, for example `将提交：图片 1 · 视频 1 · 音频 0`.

The count must come from the same classified media collection used to build the request. It is informational; backend validation remains authoritative.

## Error Handling

- Missing local reference: return HTTP 400 with the affected filename/path preview.
- Unsupported or unreadable media: return HTTP 400 and suggest replacing the file or using `上传云端`.
- Explicit frame plus video/audio conflict: return HTTP 400 before the upstream request and tell the user to disable `首尾帧` or remove video/audio references.
- Upstream payload-size rejection: preserve the upstream message and add a suggestion to use `上传云端` for the local media.
- Empty reference list: keep current text-to-video behavior.

No fallback may silently remove a requested video or audio reference.

## Compatibility

- Existing saved canvases and node schemas remain valid.
- Existing manual video URLs and cloud-uploaded URLs continue to work.
- Image-only OpenRouter workflows preserve their current first-frame behavior.
- Other providers are not changed.
- No API key, data URL, or full local path is written to generation logs.

## Verification

Automated tests must run without contacting OpenRouter and cover:

1. A local MP4 becomes an `input_references` video item with a `data:video/...` URL.
2. A public video URL is passed through unchanged.
3. Image, video, and audio references coexist in multimodal mode.
4. Unroled image plus video uses `input_references`, not an auto-promoted frame.
5. Image-only non-multimodal input still becomes `first_frame`.
6. Explicit frame plus video/audio fails before the HTTP client is called.
7. Missing local media fails clearly instead of being omitted.
8. Existing OpenRouter image, provider-default, canvas, and export test suites remain green.

Browser acceptance uses a disposable canvas and does not click the generation button. It verifies that a connected local video is counted in the OpenRouter preflight line and that no paid request is made.
