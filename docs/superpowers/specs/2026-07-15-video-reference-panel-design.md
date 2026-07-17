# Video Reference Panel Design

## Goal

Add a dedicated video reference section to the Infinite Canvas video generation node so users can clearly attach reference videos for video-to-video generation.

## Current State

The backend request already supports `videos` on `CanvasVideoRequest`, and the Infinite Canvas video runner already sends connected video inputs or a manually supplied video URL in the `videos` payload. The missing part is the node UI: image, video, and audio references are currently grouped under one generic `Media` section, which makes video references hard to discover and validate.

## Proposed UI

The video generation node should keep the current prompt, settings, and generate controls, but split the reference area into explicit sections:

- `图片参考`: connected image references, including existing frame labels such as first/last frame where applicable.
- `视频参考`: connected video references plus the existing manual video URL and temporary upload controls.
- `音频参考`: connected audio references when present.

Each section should reuse the existing input list rendering style so drag order, previews, and remove actions remain familiar. The video section should be visible even when empty, because it is the primary new affordance.

## Data Flow

No backend contract change is required.

- Image references continue to populate `images`.
- Video references continue to populate `videos`.
- Audio references continue to populate `audios`.
- Manual video URL and temporary upload URL continue to be treated as video references.

## Implementation Scope

Update `static/js/canvas.js` only unless verification exposes a missing CSS hook.

Expected changes:

- Split mixed `mediaInputs` in `renderVideoBody` by media kind.
- Render separate reference sections for image, video, and audio inputs.
- Keep the existing video URL/upload controls inside the video reference section.
- Preserve existing generation payload behavior in `runVideoNode`.

## Verification

Run a source-level regression check that confirms the new video reference section exists before and after implementation. Then verify the app still serves successfully from the local FastAPI server and, if feasible, inspect the video node UI in-browser.
