# Output Auto Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let every Infinite Canvas Output node automatically export images and videos to separate configurable local folders with configurable formats and filename templates.

**Architecture:** The browser stores export settings on each Output node and calls a local FastAPI export endpoint only for newly appended media. The endpoint resolves local canvas files or downloads remote media to a temporary file, then uses Pillow for image conversion and ffmpeg for video conversion before writing a unique, sanitized filename to the configured folder.

**Tech Stack:** FastAPI, Pydantic, Pillow, ffmpeg, static JavaScript, CSS, Python `unittest`, Node.js source regression checks.

## Global Constraints

- Image default folder: `D:\桌面\1\全能画布图片输出`.
- Video default folder: `D:\桌面\1\全能画布视频输出`.
- Image default format: `jpg`; video default format: `mp4`.
- Default name template: `{canvas}_{node}_{date}_{index}`.
- Supported name variables: `{canvas}`, `{node}`, `{date}`, `{index}`, `{type}`.
- Each Output node persists its own export settings, success history, and status.
- Missing folders are created by the backend. Invalid or inaccessible folders return an export error without affecting generated output.
- The backend must stream remote media to disk and never load whole video files into memory.
- Do not create commits because `E:\claude\Infinite-Canvas-GT-main` is not a Git repository.

---

### Task 1: Add a Tested Local Output Export API

**Files:**
- Create: `E:/claude/Infinite-Canvas-GT-main/tests/test_canvas_output_export.py`
- Modify: `E:/claude/Infinite-Canvas-GT-main/main.py:2581-2605`
- Modify: `E:/claude/Infinite-Canvas-GT-main/main.py:9009-9107`

**Interfaces:**
- Consumes: `CanvasOutputExportRequest` with `canvas_title`, `node_id`, `name_template`, `image_folder`, `video_folder`, `image_format`, `video_format`, and `items`.
- Produces: `POST /api/canvas-output-export -> {ok: bool, exported: [{source_url, path, name, kind}], skipped: []}`.
- Adds: `export_canvas_output_items(payload)`, `canvas_output_export_kind(item)`, `materialize_canvas_output_export_source(url)`, `format_canvas_output_export_name(...)`, and `unique_canvas_output_export_path(...)`.

- [ ] **Step 1: Write the failing backend test**

```python
import os
import shutil
import subprocess
import tempfile
import unittest
from PIL import Image

import main


class CanvasOutputExportTests(unittest.TestCase):
    def test_export_local_image_converts_to_jpeg_with_template(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_name = "output-auto-export-source.png"
            source_path = main.output_path_for(source_name, "output")
            Image.new("RGBA", (4, 4), (255, 0, 0, 128)).save(source_path)
            try:
                result = main.export_canvas_output_items(main.CanvasOutputExportRequest(
                    canvas_title="测试画布",
                    node_id="out-demo",
                    name_template="{canvas}_{node}_{type}_{index}",
                    image_folder=temp_dir,
                    video_folder=temp_dir,
                    image_format="jpg",
                    video_format="mp4",
                    items=[main.CanvasOutputExportItem(url=main.output_url_for(source_name, "output"), kind="image")],
                ))
            finally:
                if os.path.exists(source_path):
                    os.remove(source_path)

            self.assertEqual(len(result["exported"]), 1)
            saved = result["exported"][0]
            self.assertTrue(saved["name"].endswith(".jpg"))
            self.assertTrue(os.path.isfile(saved["path"]))
            with Image.open(saved["path"]) as image:
                self.assertEqual(image.format, "JPEG")

    def test_export_uses_video_folder_and_unique_names(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_dir = os.path.join(temp_dir, "images")
            video_dir = os.path.join(temp_dir, "videos")
            source_name = "output-auto-export-video.mp4"
            source_path = main.output_path_for(source_name, "output")
            subprocess.run([
                shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "color=c=black:s=4x4:d=0.1",
                "-c:v", "libx264", source_path,
            ], check=True)
            try:
                payload = main.CanvasOutputExportRequest(
                    canvas_title="测试画布", node_id="out-video", name_template="same-name",
                    image_folder=image_dir, video_folder=video_dir,
                    image_format="jpg", video_format="mp4",
                    items=[main.CanvasOutputExportItem(url=main.output_url_for(source_name, "output"), kind="video")],
                )
                first = main.export_canvas_output_items(payload)["exported"][0]
                second = main.export_canvas_output_items(payload)["exported"][0]
            finally:
                if os.path.exists(source_path):
                    os.remove(source_path)

            self.assertEqual(os.path.dirname(first["path"]), os.path.abspath(video_dir))
            self.assertNotEqual(first["name"], second["name"])
            self.assertTrue(os.path.isfile(second["path"]))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the backend test to verify it fails**

Run: `.venv\Scripts\python.exe -m unittest tests.test_canvas_output_export.CanvasOutputExportTests.test_export_local_image_converts_to_jpeg_with_template -v`

Expected: FAIL because `CanvasOutputExportRequest` and `export_canvas_output_items` do not exist.

- [ ] **Step 3: Add validated request models and naming helpers**

```python
class CanvasOutputExportItem(BaseModel):
    url: str = ""
    kind: str = ""
    name: str = ""

class CanvasOutputExportRequest(BaseModel):
    canvas_title: str = "canvas"
    node_id: str = "output"
    name_template: str = "{canvas}_{node}_{date}_{index}"
    image_folder: str = r"D:\桌面\1\全能画布图片输出"
    video_folder: str = r"D:\桌面\1\全能画布视频输出"
    image_format: str = "jpg"
    video_format: str = "mp4"
    items: List[CanvasOutputExportItem] = Field(default_factory=list)

def format_canvas_output_export_name(template, canvas_title, node_id, kind, index, extension):
    values = {
        "canvas": sanitize_export_filename(canvas_title, "canvas"),
        "node": sanitize_export_filename(node_id, "output"),
        "date": time.strftime("%Y%m%d-%H%M%S"),
        "index": str(index),
        "type": kind,
    }
    stem = str(template or "{canvas}_{node}_{date}_{index}")
    for key, value in values.items():
        stem = stem.replace("{" + key + "}", value)
    stem = sanitize_export_filename(stem, "canvas-output")
    stem = os.path.splitext(stem)[0]
    return f"{stem}.{extension.lstrip('.')}"

def canvas_output_export_kind(item):
    kind = str(getattr(item, "kind", "") or "").strip().lower()
    if kind in {"image", "video"}:
        return kind
    path = urllib.parse.urlparse(str(getattr(item, "url", "") or "")).path.lower()
    return "video" if os.path.splitext(path)[1] in {".mp4", ".webm", ".mov", ".m4v", ".avi", ".mkv"} else "image"

def normalized_canvas_output_export_format(kind, value):
    format_value = str(value or "").strip().lower().lstrip(".")
    allowed = {"image": {"jpg", "png", "webp"}, "video": {"mp4", "webm", "mov"}}
    default = "jpg" if kind == "image" else "mp4"
    return format_value if format_value in allowed[kind] else default
```

- [ ] **Step 4: Implement streaming, conversion, and unique export writes**

```python
def unique_canvas_output_export_path(folder, filename):
    base = os.path.abspath(os.path.expanduser(str(folder or "").strip()))
    if not base:
        raise HTTPException(status_code=400, detail="请选择输出文件夹")
    os.makedirs(base, exist_ok=True)
    stem, ext = os.path.splitext(sanitize_export_filename(filename, "canvas-output.bin"))
    candidate = os.path.join(base, f"{stem}{ext}")
    suffix = 2
    while os.path.exists(candidate):
        candidate = os.path.join(base, f"{stem}-{suffix}{ext}")
        suffix += 1
    return candidate

def materialize_canvas_output_export_source(url):
    local = output_file_from_url(url) or local_media_file_by_basename(filename_from_media_url(url, ""))
    if local and os.path.isfile(local):
        return local, None
    parsed = urllib.parse.urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="无效的输出文件地址")
    suffix = os.path.splitext(parsed.path)[1] or ".bin"
    handle = tempfile.NamedTemporaryFile(prefix="canvas_output_export_", suffix=suffix, delete=False)
    try:
        with requests.get(url, stream=True, timeout=(10, 120), headers={"User-Agent": "InfiniteCanvas/1.0"}) as response:
            response.raise_for_status()
            for chunk in response.iter_content(chunk_size=256 * 1024):
                if chunk:
                    handle.write(chunk)
    except requests.RequestException as exc:
        handle.close()
        os.unlink(handle.name)
        raise HTTPException(status_code=502, detail=f"远程输出下载失败：{exc}") from exc
    handle.close()
    return handle.name, handle.name

def export_canvas_output_items(payload):
    exported, skipped = [], []
    for index, item in enumerate(payload.items[:200], start=1):
        kind = canvas_output_export_kind(item)
        if kind not in {"image", "video"}:
            skipped.append({"source_url": item.url, "reason": "不支持的输出类型"})
            continue
        source, temporary = materialize_canvas_output_export_source(item.url)
        try:
            extension = normalized_canvas_output_export_format(kind, payload.image_format if kind == "image" else payload.video_format)
            folder = payload.image_folder if kind == "image" else payload.video_folder
            filename = format_canvas_output_export_name(payload.name_template, payload.canvas_title, payload.node_id, kind, index, extension)
            destination = unique_canvas_output_export_path(folder, filename)
            if kind == "image":
                with Image.open(source) as image:
                    image = ImageOps.exif_transpose(image)
                    if extension == "jpg":
                        background = Image.new("RGB", image.size, "white")
                        if image.mode in {"RGBA", "LA"}:
                            background.paste(image.convert("RGBA"), mask=image.convert("RGBA").getchannel("A"))
                        else:
                            background.paste(image.convert("RGB"))
                        background.save(destination, format="JPEG", quality=95)
                    else:
                        image.save(destination, format={"png": "PNG", "webp": "WEBP"}[extension])
            else:
                ffmpeg = shutil.which("ffmpeg")
                if not ffmpeg:
                    raise HTTPException(status_code=500, detail="未找到 ffmpeg，无法转换视频格式")
                codec = {"mp4": ["-c:v", "libx264", "-c:a", "aac"], "mov": ["-c:v", "libx264", "-c:a", "aac"], "webm": ["-c:v", "libvpx-vp9", "-c:a", "libopus"]}[extension]
                proc = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", source, *codec, destination], capture_output=True, text=True, timeout=600)
                if proc.returncode != 0 or not os.path.isfile(destination):
                    raise HTTPException(status_code=422, detail=(proc.stderr or "视频格式转换失败").strip()[:500])
            exported.append({"source_url": item.url, "path": destination, "name": os.path.basename(destination), "kind": kind})
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
    if not exported:
        raise HTTPException(status_code=404, detail="没有可导出的图片或视频")
    return {"ok": True, "exported": exported, "skipped": skipped}
```

- [ ] **Step 5: Expose the same-origin FastAPI endpoint**

```python
@app.post("/api/canvas-output-export")
async def export_canvas_output(payload: CanvasOutputExportRequest, request: Request):
    ensure_same_origin_request(request)
    return await asyncio.to_thread(export_canvas_output_items, payload)
```

- [ ] **Step 6: Run the backend test to verify it passes**

Run: `.venv\Scripts\python.exe -m unittest tests.test_canvas_output_export.CanvasOutputExportTests.test_export_local_image_converts_to_jpeg_with_template -v`

Expected: PASS with one exported JPEG in the temporary directory.

---

### Task 2: Persist Output Node Export Settings and Render Controls

**Files:**
- Create: `E:/claude/Infinite-Canvas-GT-main/tests/canvas-output-auto-export.test.js`
- Modify: `E:/claude/Infinite-Canvas-GT-main/static/js/canvas.js:9536-9551`
- Modify: `E:/claude/Infinite-Canvas-GT-main/static/js/canvas.js:11888-11918`
- Modify: `E:/claude/Infinite-Canvas-GT-main/static/js/canvas.js:5612-5840`
- Modify: `E:/claude/Infinite-Canvas-GT-main/static/css/canvas.css:592-602`

**Interfaces:**
- Consumes: persisted Output node fields `autoExport`, `imageExportFolder`, `videoExportFolder`, `imageExportFormat`, `videoExportFormat`, `exportNameTemplate`, `exportedOutputUrls`, and `exportStatus`.
- Produces: `ensureOutputExportSettings(node)`, `renderOutputExportControls(node)`, and `bindOutputExportControls(body, node)`.
- Depends on: Task 1 endpoint at `/api/canvas-output-export`.

- [ ] **Step 1: Write the failing frontend regression check**

```javascript
const assert = require('node:assert/strict');
const fs = require('node:fs');

const source = fs.readFileSync('static/js/canvas.js', 'utf8');
assert.match(source, /function ensureOutputExportSettings\(node\)/);
assert.match(source, /D:\\\\桌面\\\\1\\\\全能画布图片输出/);
assert.match(source, /D:\\\\桌面\\\\1\\\\全能画布视频输出/);
assert.match(source, /function renderOutputExportControls\(node\)/);
assert.match(source, /data-output-export-settings/);
console.log('canvas-output-auto-export: passed');
```

- [ ] **Step 2: Run the frontend check to verify it fails**

Run: `node tests\canvas-output-auto-export.test.js`

Expected: FAIL because the Output node has no export setting defaults or controls.

- [ ] **Step 3: Initialize settings for new and existing Output nodes**

```javascript
const OUTPUT_EXPORT_DEFAULTS = {
    autoExport:true,
    imageExportFolder:'D:\\桌面\\1\\全能画布图片输出',
    videoExportFolder:'D:\\桌面\\1\\全能画布视频输出',
    imageExportFormat:'jpg',
    videoExportFormat:'mp4',
    exportNameTemplate:'{canvas}_{node}_{date}_{index}'
};

function ensureOutputExportSettings(node){
    if(!node || node.type !== 'output') return node;
    Object.entries(OUTPUT_EXPORT_DEFAULTS).forEach(([key, value]) => {
        if(node[key] === undefined || node[key] === null || node[key] === '') node[key] = value;
    });
    node.exportedOutputUrls = Array.isArray(node.exportedOutputUrls) ? node.exportedOutputUrls : [];
    return node;
}
```

Call `ensureOutputExportSettings(out)` whenever `outputForNode` creates or returns an Output node and before rendering an existing Output node.

- [ ] **Step 4: Render and bind a compact settings panel inside Output nodes**

```javascript
function renderOutputExportControls(node){
    ensureOutputExportSettings(node);
    return `<div class="output-export" data-output-export>
        <div class="output-export-head">
            <button type="button" class="setting-check ${node.autoExport ? 'active' : ''}" data-output-export-toggle><span class="check-dot"></span>自动导出</button>
            <button type="button" class="icon-btn" data-output-export-settings title="导出设置"><i data-lucide="folder-cog"></i></button>
            <button type="button" class="icon-btn" data-output-export-now title="导出当前文件"><i data-lucide="download"></i></button>
        </div>
        <div class="output-export-fields" ${node.exportSettingsOpen ? '' : 'hidden'}>
            <label>图片目录<input data-output-image-folder value="${escapeAttr(node.imageExportFolder)}"></label>
            <label>图片格式<select data-output-image-format><option value="jpg" ${node.imageExportFormat === 'jpg' ? 'selected' : ''}>JPG</option><option value="png" ${node.imageExportFormat === 'png' ? 'selected' : ''}>PNG</option><option value="webp" ${node.imageExportFormat === 'webp' ? 'selected' : ''}>WebP</option></select></label>
            <label>视频目录<input data-output-video-folder value="${escapeAttr(node.videoExportFolder)}"></label>
            <label>视频格式<select data-output-video-format><option value="mp4" ${node.videoExportFormat === 'mp4' ? 'selected' : ''}>MP4</option><option value="webm" ${node.videoExportFormat === 'webm' ? 'selected' : ''}>WebM</option><option value="mov" ${node.videoExportFormat === 'mov' ? 'selected' : ''}>MOV</option></select></label>
            <label>命名格式<input data-output-name-template value="${escapeAttr(node.exportNameTemplate)}"></label>
        </div>
        <div class="output-export-status" data-output-export-status>${escapeHtml(node.exportStatus || '')}</div>
    </div>`;
}
```

Bind each input to its matching node property, set `exportSettingsOpen` through the settings button, call `scheduleSave()`, and use `render()` only when the expanded state changes. Add CSS that keeps the bar compact, gives path fields a stable single-line layout, and lets the fields wrap on narrow node widths.

```javascript
function refreshOutputExportStatus(node){
    const element = nodesEl.querySelector(`.node[data-id="${CSS.escape(node.id)}"] [data-output-export-status]`);
    if(element) element.textContent = node.exportStatus || '';
}
```

- [ ] **Step 5: Run the frontend check to verify it passes**

Run: `node tests\canvas-output-auto-export.test.js`

Expected: PASS with the defaults, control renderer, and settings data hook present.

---

### Task 3: Trigger Automatic and Manual Output Exports

**Files:**
- Modify: `E:/claude/Infinite-Canvas-GT-main/static/js/canvas.js:11911-11939`
- Modify: `E:/claude/Infinite-Canvas-GT-main/static/js/canvas.js:3115-3156`
- Modify: `E:/claude/Infinite-Canvas-GT-main/tests/canvas-output-auto-export.test.js`

**Interfaces:**
- Consumes: Task 1 endpoint and Task 2 Output node fields.
- Produces: `exportOutputNodeMedia(nodeId, {manual})` and `scheduleOutputNodeAutoExport(node, addedItems)`.
- Calls: `appendOutputImages(out, images, ...)` after new media arrives and the existing Output-node context menu.

- [ ] **Step 1: Extend the failing frontend check for automatic export**

```javascript
assert.match(source, /async function exportOutputNodeMedia\(nodeId, opts=\{\}\)/);
assert.match(source, /fetch\('\/api\/canvas-output-export'/);
assert.match(source, /function scheduleOutputNodeAutoExport\(node, addedItems\)/);
assert.match(source, /scheduleOutputNodeAutoExport\(out, list\)/);
```

- [ ] **Step 2: Run the extended frontend check to verify it fails**

Run: `node tests\canvas-output-auto-export.test.js`

Expected: FAIL because automatic export has not been wired to appended Output media.

- [ ] **Step 3: Implement de-duplicated export requests**

```javascript
async function exportOutputNodeMedia(nodeId, opts={}){
    const node = nodes.find(item => item.id === nodeId && item.type === 'output');
    if(!node) return;
    ensureOutputExportSettings(node);
    const all = (node.images || []).filter(item => ['image', 'video'].includes(mediaKindForOutputItem(item)));
    const exported = new Set(node.exportedOutputUrls || []);
    const items = opts.manual ? all : all.filter(item => !exported.has(outputUrlValue(item)));
    if(!items.length) return;
    node.exportStatus = '正在导出...';
    refreshOutputExportStatus(node);
    const response = await fetch('/api/canvas-output-export', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({
            canvas_title:canvas?.title || 'canvas', node_id:node.id,
            name_template:node.exportNameTemplate,
            image_folder:node.imageExportFolder, video_folder:node.videoExportFolder,
            image_format:node.imageExportFormat, video_format:node.videoExportFormat,
            items:items.map(item => ({url:outputUrlValue(item), kind:mediaKindForOutputItem(item), name:item.name || outputImageName(outputUrlValue(item))}))
        })
    });
    const data = await response.json();
    if(!response.ok) throw new Error(data.detail || '自动导出失败');
    node.exportedOutputUrls = [...new Set([...exported, ...data.exported.map(item => item.source_url)])];
    node.exportStatus = `已导出 ${data.exported.length} 个文件`;
    scheduleSave();
}

function scheduleOutputNodeAutoExport(node, addedItems){
    ensureOutputExportSettings(node);
    if(!node.autoExport || !addedItems?.some(item => ['image', 'video'].includes(mediaKindForOutputItem(item)))) return;
    Promise.resolve().then(() => exportOutputNodeMedia(node.id)).catch(error => {
        node.exportStatus = error.message || '自动导出失败';
        refreshOutputExportStatus(node);
        scheduleSave();
    });
}
```

Update `appendOutputImages` to call `scheduleOutputNodeAutoExport(out, list)` after `out.images` is updated. Update the Output-node context menu with a second manual export button that calls `exportOutputNodeMedia(nodeId, {manual:true})` so exporting all current files remains available even when auto export is off.

- [ ] **Step 4: Run the frontend check to verify it passes**

Run: `node tests\canvas-output-auto-export.test.js`

Expected: PASS with endpoint wiring and automatic trigger present.

---

### Task 4: Verify the Full Feature

**Files:**
- Read: `E:/claude/Infinite-Canvas-GT-main/main.py`
- Read: `E:/claude/Infinite-Canvas-GT-main/static/js/canvas.js`
- Read: `E:/claude/Infinite-Canvas-GT-main/static/css/canvas.css`

**Interfaces:**
- Consumes: completed Tasks 1-3.
- Produces: proof that image conversion, JavaScript syntax, the API route, and Output-node controls work together.

- [ ] **Step 1: Run every regression check**

Run: `.venv\Scripts\python.exe -m unittest tests.test_canvas_output_export -v`

Expected: PASS with no failures.

- [ ] **Step 2: Compile and parse changed production files**

Run: `.venv\Scripts\python.exe -m py_compile main.py`

Expected: exit code 0.

Run: `node --check static\js\canvas.js`

Expected: exit code 0.

- [ ] **Step 3: Exercise the endpoint with the local test image**

Run: `.venv\Scripts\python.exe -m unittest tests.test_canvas_output_export.CanvasOutputExportTests.test_export_local_image_converts_to_jpeg_with_template -v`

Expected: PASS and the test confirms a real JPEG was written to a temporary output directory.

- [ ] **Step 4: Inspect the output node in the running application**

Run: `npx.cmd --yes playwright screenshot --wait-for-timeout=1200 http://127.0.0.1:3000/static/canvas.html output\playwright\output-auto-export.png`

Expected: exit code 0; the Output node shows its automatic-export toggle, settings button, path/format fields when expanded, and manual export button.
