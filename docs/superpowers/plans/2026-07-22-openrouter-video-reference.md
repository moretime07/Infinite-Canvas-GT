# OpenRouter Video Reference Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure Infinite Canvas sends connected image, video, and audio references to OpenRouter Seedance through the normalized `input_references` schema, with clear preflight feedback and no silent reference loss.

**Architecture:** Extract OpenRouter request construction into a testable backend helper that normalizes remote, data, and local asset URLs. Keep exact first/last-frame generation separate from multimodal reference guidance, reject ambiguous combinations before the upstream call, and add a read-only frontend summary of the references the canvas will submit.

**Tech Stack:** Python 3, FastAPI, Pydantic, unittest, vanilla JavaScript, Node.js assertions.

## Global Constraints

- Do not contact OpenRouter or trigger paid video generation in automated or browser verification.
- Preserve all existing providers and saved canvas schemas.
- Never log API keys, full data URLs, or full local file paths.
- Existing public and cloud-uploaded video URLs must continue to pass through unchanged.
- Local `/assets/...` and `/output/...` media must use the existing safe path resolver.

---

### Task 1: Build and verify OpenRouter multimodal video requests

**Files:**
- Create: `tests/test_openrouter_video_references.py`
- Modify: `main.py:12123-12490`

**Interfaces:**
- Consumes: `CanvasVideoRequest`, `media_reference_to_url(value, max_image_size=None)`, `reference_to_data_url(ref, max_size=None)`, and `selected_model(model, fallback)`.
- Produces: `build_openrouter_video_request(payload: CanvasVideoRequest) -> tuple[dict, dict]`, returning the upstream JSON body and `{image, video, audio}` reference counts.

- [ ] **Step 1: Write failing request-builder tests**

Create tests that instantiate `CanvasVideoRequest` and assert:

```python
body, counts = main.build_openrouter_video_request(main.CanvasVideoRequest(
    prompt="Follow the reference motion",
    provider_id="custom-api",
    model="bytedance/seedance-2.0",
    duration=7,
    aspect_ratio="9:16",
    resolution="720p",
    images=[main.AIReference(url="data:image/png;base64,AAA")],
    videos=["data:video/mp4;base64,BBB"],
    audios=["https://cdn.example.com/music.mp3"],
    multimodal=True,
))
self.assertEqual(body["input_references"], [
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
    {"type": "video_url", "video_url": {"url": "data:video/mp4;base64,BBB"}},
    {"type": "audio_url", "audio_url": {"url": "https://cdn.example.com/music.mp3"}},
])
self.assertEqual(counts, {"image": 1, "video": 1, "audio": 1})
```

Also cover public URL pass-through, local MP4 conversion using a temporary file patched through `output_file_from_url`, image-only first-frame compatibility, unroled image plus video staying in `input_references`, missing local media, and explicit frame plus video conflict.

- [ ] **Step 2: Run the new tests and verify they fail**

Run: `python -m unittest tests.test_openrouter_video_references -v`

Expected: FAIL because `build_openrouter_video_request` does not exist.

- [ ] **Step 3: Implement media normalization and request construction**

Add focused helpers near `/api/canvas-video`:

```python
def openrouter_reference_url(value: str, kind: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith(("http://", "https://", "data:")):
        return text
    if text.startswith(("/assets/", "/output/")):
        path = output_file_from_url(text)
        if not path or not os.path.isfile(path):
            raise HTTPException(status_code=400, detail=f"参考{kind}文件不存在，请重新连接素材或使用上传云端：{os.path.basename(text)}")
        return reference_to_data_url({"url": text}, max_size=1536 if kind == "图片" else None)
    raise HTTPException(status_code=400, detail=f"参考{kind}地址格式不受支持，请使用本地素材、data URL 或 http/https 地址。")
```

Implement `build_openrouter_video_request` so explicit frames populate `frame_images`; otherwise images, videos, and audios populate `input_references`. When video/audio exists, unroled images must not be auto-promoted to first frame. Raise HTTP 400 when explicit frame images coexist with video/audio references.

- [ ] **Step 4: Route OpenRouter requests through the helper**

Replace the inline OpenRouter body builder with:

```python
elif is_openrouter:
    body, reference_counts = build_openrouter_video_request(payload)
```

Return the count summary with the successful local response under `request.references`, without returning input URLs or data.

- [ ] **Step 5: Run backend tests**

Run: `python -m unittest tests.test_openrouter_video_references tests.test_openrouter_image_generation tests.test_canvas_output_export tests.test_primary_provider -v`

Expected: all tests PASS and no network call occurs.

- [ ] **Step 6: Commit the backend change**

```powershell
git add -- main.py tests/test_openrouter_video_references.py
git commit -m "fix(video): send OpenRouter media references"
```

### Task 2: Show OpenRouter reference preflight counts

**Files:**
- Create: `tests/canvas-openrouter-video-reference.test.js`
- Modify: `static/js/canvas.js:8367-8510`
- Modify: `static/css/canvas.css:223-230`

**Interfaces:**
- Consumes: `mediaInputsByKind`, `videoProviderById(providerId)`, and `ProviderDefaults.isOpenRouter(provider)`.
- Produces: `videoReferenceSummaryHtml(node, mediaInputsByKind) -> string` and the `.video-reference-summary` node element.

- [ ] **Step 1: Write a failing frontend regression test**

Create a Node test that reads `static/js/canvas.js` and asserts the video renderer contains a summary helper, the three Chinese count labels, an OpenRouter provider check, and a conflict warning for `useFrameRoles` with video/audio input.

```js
assert.match(source, /function videoReferenceSummaryHtml\(/);
assert.match(source, /ProviderDefaults\.isOpenRouter/);
assert.match(source, /图片.*视频.*音频/s);
assert.match(source, /useFrameRoles.*video.*audio/s);
```

- [ ] **Step 2: Run the frontend test and verify it fails**

Run: `node tests/canvas-openrouter-video-reference.test.js`

Expected: FAIL because the summary helper is absent.

- [ ] **Step 3: Implement the preflight summary**

Add a helper that counts the classified references, resolves a default-following node to its current provider, and returns no markup for non-OpenRouter providers. For OpenRouter return:

```html
<div class="video-reference-summary" data-video-reference-summary>
  将提交：图片 1 · 视频 1 · 音频 0
</div>
```

When `useFrameRoles` is enabled while video or audio count is non-zero, render a warning explaining that the request will be blocked until the user disables `首尾帧` or removes the video/audio reference.

- [ ] **Step 4: Add compact styling**

Add `.video-reference-summary` and `.video-reference-summary.warning` styles using existing muted text, soft background, border, and warning color variables. Do not change node dimensions or existing reference cards.

- [ ] **Step 5: Run frontend and syntax tests**

Run:

```powershell
node tests/canvas-openrouter-video-reference.test.js
node tests/canvas-follow-default-provider.test.js
node tests/canvas-provider-defaults.test.js
node --check static/js/canvas.js
```

Expected: all commands PASS.

- [ ] **Step 6: Commit the frontend change**

```powershell
git add -- static/js/canvas.js static/css/canvas.css tests/canvas-openrouter-video-reference.test.js
git commit -m "feat(video): show OpenRouter reference preflight"
```

### Task 3: Full regression and no-cost acceptance

**Files:**
- Verify only; no planned file changes.

**Interfaces:**
- Consumes: committed backend helper and frontend summary.
- Produces: verification evidence that references are preserved without a paid generation call.

- [ ] **Step 1: Run the complete relevant test matrix**

Run all Python provider/video/export tests, all existing Node canvas/provider tests, `node --check` for modified JavaScript, `python -m py_compile main.py`, and `git diff --check`.

Expected: every command exits 0.

- [ ] **Step 2: Perform no-cost browser acceptance**

Open a disposable Infinite Canvas, connect one local image and one local MP4 to an OpenRouter Seedance node, enable `全能参考`, and verify the node shows `将提交：图片 1 · 视频 1 · 音频 0`. Do not click `视频生成`.

- [ ] **Step 3: Verify secrets and cleanup**

Confirm `API/.env` and `data/api_providers.json` remain ignored, remove the disposable canvas, and confirm only pre-existing runtime HTML changes remain uncommitted.

