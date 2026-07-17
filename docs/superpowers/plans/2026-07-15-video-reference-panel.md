# Video Reference Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dedicated `视频参考` section to the Infinite Canvas video generation node.

**Architecture:** Keep the backend contract unchanged because `CanvasVideoRequest.videos` and `runVideoNode` already pass video references. Update the existing `renderVideoBody` UI to split mixed media inputs into image, video, and audio sections while preserving the current input list renderer and button handlers.

**Tech Stack:** Static JavaScript in `static/js/canvas.js`, FastAPI server in `main.py`, PowerShell verification commands.

## Global Constraints

- Preserve existing generation payload behavior in `runVideoNode`.
- Update `static/js/canvas.js` only unless verification exposes a missing CSS hook.
- Keep manual video URL and temporary upload controls inside the new video reference section.
- This project path is not a git repository, so no commit step is runnable in this workspace.

---

### Task 1: Source-Level Regression Check

**Files:**
- Test: inline PowerShell source check
- Read: `E:/claude/Infinite-Canvas-GT-main/static/js/canvas.js`

**Interfaces:**
- Consumes: current `static/js/canvas.js` source text.
- Produces: a red check proving the dedicated `视频参考` section is missing before implementation.

- [ ] **Step 1: Write the failing test**

```powershell
$source = Get-Content -Raw -Encoding UTF8 static\js\canvas.js
if($source -notmatch '视频参考'){ throw 'missing 视频参考 section' }
if($source -notmatch 'data-video-ref-section="video"'){ throw 'missing video reference section hook' }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -c "from pathlib import Path; s=Path('static/js/canvas.js').read_text(encoding='utf-8'); assert '视频参考' in s and 'data-video-ref-section=\"video\"' in s"`

Expected: FAIL with an assertion error because the current UI only contains the generic `Media` header.

---

### Task 2: Split Video Node Reference Sections

**Files:**
- Modify: `E:/claude/Infinite-Canvas-GT-main/static/js/canvas.js`

**Interfaces:**
- Consumes: `mediaKindForRef(ref) -> 'image' | 'video' | 'audio' | ...`, `renderVideoImageInputs(list, node, imageInputs)`.
- Produces: `mediaInputsByKind`, `.video-image-list`, `.video-reference-list`, `.video-audio-list`, and `data-video-ref-section="video"` in `renderVideoBody`.

- [ ] **Step 1: Add media grouping inside `renderVideoBody`**

```javascript
const mediaInputsByKind = {
    image: mediaInputs.filter(src => src.refs?.some(ref => mediaKindForRef(ref) === 'image')),
    video: mediaInputs.filter(src => src.refs?.some(ref => mediaKindForRef(ref) === 'video')),
    audio: mediaInputs.filter(src => src.refs?.some(ref => mediaKindForRef(ref) === 'audio'))
};
```

- [ ] **Step 2: Replace the single generic `Media` list**

```html
<div class="video-ref-section" data-video-ref-section="image">
    <div class="video-input-head">
        <div class="text-[10px] font-bold text-gray-400 uppercase tracking-widest">图片参考</div>
    </div>
    <div class="input-list video-img-list video-image-list"></div>
</div>
<div class="video-ref-section" data-video-ref-section="video">
    <div class="video-input-head">
        <div class="text-[10px] font-bold text-gray-400 uppercase tracking-widest">视频参考</div>
        <div class="video-input-actions">
            <button type="button" class="tool-btn" data-video-manual-url title="手动输入视频 URL"><i data-lucide="link" class="w-4 h-4"></i><span>输入网址</span></button>
            <button type="button" class="tool-btn" data-video-temp-sh title="上传当前输入视频到云端直链"><i data-lucide="upload-cloud" class="w-4 h-4"></i><span>上传云端</span></button>
        </div>
    </div>
    <div class="input-list video-img-list video-reference-list"></div>
</div>
<div class="video-ref-section" data-video-ref-section="audio">
    <div class="video-input-head">
        <div class="text-[10px] font-bold text-gray-400 uppercase tracking-widest">音频参考</div>
    </div>
    <div class="input-list video-img-list video-audio-list"></div>
</div>
```

- [ ] **Step 3: Render the split lists**

```javascript
renderVideoImageInputs(wrap.querySelector('.video-image-list'), node, mediaInputsByKind.image, tr('canvas.groupEmpty'));
renderVideoImageInputs(wrap.querySelector('.video-reference-list'), node, mediaInputsByKind.video, '可连接视频节点，或输入/上传一个视频 URL 作为参考');
renderVideoImageInputs(wrap.querySelector('.video-audio-list'), node, mediaInputsByKind.audio, tr('canvas.groupEmpty'));
```

---

### Task 3: Green Verification

**Files:**
- Read: `E:/claude/Infinite-Canvas-GT-main/static/js/canvas.js`
- Read: `E:/claude/Infinite-Canvas-GT-main/main.py`

**Interfaces:**
- Consumes: modified frontend source.
- Produces: confirmation that the UI hook exists and the app still serves.

- [ ] **Step 1: Run the source regression check**

Run: `.venv\Scripts\python.exe -c "from pathlib import Path; s=Path('static/js/canvas.js').read_text(encoding='utf-8'); assert '视频参考' in s and 'data-video-ref-section=\"video\"' in s; assert 'renderVideoImageInputs(wrap.querySelector(\\'.video-reference-list\\')' in s"`

Expected: PASS with exit code 0.

- [ ] **Step 2: Compile backend**

Run: `.venv\Scripts\python.exe -m py_compile main.py`

Expected: PASS with exit code 0.

- [ ] **Step 3: Verify local server responds**

Run: `.venv\Scripts\python.exe -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:3000/', timeout=10).status)"`

Expected: `200`.
