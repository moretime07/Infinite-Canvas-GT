# OminiLink Model Discovery and Omni Flash Video Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make OminiLink / LittleOrange providers discoverable in API Settings and make `gemini-omni-flash-preview` execute real text-to-video, image-to-video, and video-edit jobs from Infinite Canvas.

**Architecture:** Keep the provider's OpenAI-compatible `base_url` for chat/model discovery and add a non-secret `video_base_url` for video tasks. Recognize only exact OminiLink API hosts, merge a documented video-model catalog when `/v1/models` is unavailable, and route OminiLink video jobs through a dedicated adapter before the existing generic video code.

**Tech Stack:** Python 3, FastAPI, Pydantic, httpx, unittest/AsyncMock, vanilla JavaScript, Node.js `assert`.

## Global Constraints

- Do not write API keys into provider JSON responses, logs, test snapshots, source files, or Git.
- Do not submit real paid generation jobs from automated tests; all upstream HTTP calls must use mocks.
- Recognize only `api.aig-ai.com`, `vg-api.aig-ai.com`, `api.ominilink.ai`, and `vg-api.ominilink.ai`; do not treat portal URLs or lookalike hosts as APIs.
- Preserve unknown providers' current failure behavior; OminiLink catalog fallback is host-scoped.
- `401` and `403` are authentication failures and must never be reported as successful catalog fallback.
- The complete multimodal adapter is only guaranteed for `gemini-omni-flash-preview`; other documented OminiLink video models use basic text-to-video only.
- Omni Flash accepts no audio, one image or one video, no mixed image/video input, requested duration `3..10` seconds, and known reference-video duration at most 3 seconds.
- Do not alter or commit unrelated dirty-worktree files.

---

### Task 1: Normalize OminiLink providers and persist separate video URLs

**Files:**
- Create: `tests/test_ominilink_provider.py`
- Modify: `main.py:1189-1275`
- Modify: `main.py:2514-2545`

**Interfaces:**
- Produces: `is_ominilink_api_url(value: str) -> bool`
- Produces: `normalize_ominilink_urls(base_url: str, video_base_url: str = "") -> tuple[str, str]`
- Produces: normalized provider fields `base_url: str` and `video_base_url: str`
- Consumes: existing `normalize_provider`, `ApiProviderPayload`, `public_provider`

- [ ] **Step 1: Write failing provider-normalization tests**

```python
import unittest
import main


class OminiLinkProviderTests(unittest.TestCase):
    def test_exact_api_hosts_are_recognized(self):
        for url in (
            "https://api.aig-ai.com/v1",
            "https://vg-api.aig-ai.com/v1",
            "https://api.ominilink.ai/v1",
            "https://vg-api.ominilink.ai/v1",
        ):
            with self.subTest(url=url):
                self.assertTrue(main.is_ominilink_api_url(url))

    def test_portal_and_lookalike_hosts_are_rejected(self):
        for url in (
            "https://portal.ominilink.ai/",
            "https://aig-ai.com.evil.test/v1",
            "https://notominilink.ai/v1",
        ):
            with self.subTest(url=url):
                self.assertFalse(main.is_ominilink_api_url(url))

    def test_video_host_migrates_to_dual_urls(self):
        provider = main.normalize_provider({
            "id": "orange",
            "name": "橙域",
            "base_url": "https://vg-api.aig-ai.com/v1",
        })
        self.assertEqual(provider["base_url"], "https://api.aig-ai.com/v1")
        self.assertEqual(provider["video_base_url"], "https://vg-api.aig-ai.com/v1")

    def test_explicit_video_url_wins(self):
        provider = main.normalize_provider({
            "id": "orange",
            "base_url": "https://api.aig-ai.com/v1",
            "video_base_url": "https://video-proxy.example.test/v1",
        })
        self.assertEqual(provider["video_base_url"], "https://video-proxy.example.test/v1")
```

- [ ] **Step 2: Run the new tests and verify the missing helpers/field fail**

Run: `python -m unittest tests.test_ominilink_provider -v`

Expected: FAIL because `is_ominilink_api_url` is undefined and `video_base_url` is not normalized.

- [ ] **Step 3: Add exact-host URL helpers and provider schema support**

Add constants and helpers near other provider URL helpers:

```python
OMINILINK_API_HOSTS = {
    "api.aig-ai.com",
    "vg-api.aig-ai.com",
    "api.ominilink.ai",
    "vg-api.ominilink.ai",
}


def is_ominilink_api_url(value: str) -> bool:
    parsed = urllib.parse.urlsplit(str(value or "").strip())
    return parsed.scheme in {"http", "https"} and (parsed.hostname or "").lower() in OMINILINK_API_HOSTS


def normalize_ominilink_urls(base_url: str, video_base_url: str = "") -> tuple[str, str]:
    base = str(base_url or "").strip().rstrip("/")
    video = str(video_base_url or "").strip().rstrip("/")
    parsed = urllib.parse.urlsplit(base)
    host = (parsed.hostname or "").lower()
    if host not in OMINILINK_API_HOSTS:
        return base, video
    suffix = parsed.path.rstrip("/") or "/v1"
    root_host = host.removeprefix("vg-")
    chat = urllib.parse.urlunsplit((parsed.scheme, root_host, suffix, "", ""))
    derived_video = urllib.parse.urlunsplit((parsed.scheme, f"vg-{root_host}", suffix, "", ""))
    return chat.rstrip("/"), (video or derived_video).rstrip("/")
```

Validate `video_base_url` with the same HTTP(S) rule as `base_url`, call
`normalize_ominilink_urls`, return it from `normalize_provider`, and add
`video_base_url: str = ""` to `ApiProviderPayload`. `public_provider` already copies normalized
non-secret fields and needs no key-related changes.

- [ ] **Step 4: Run provider and primary-provider regression tests**

Run: `python -m unittest tests.test_ominilink_provider tests.test_primary_provider -v`

Expected: PASS.

- [ ] **Step 5: Commit only Task 1 files**

```powershell
git add -- tests/test_ominilink_provider.py
git add -p -- main.py
git diff --cached --check
git commit -m "feat: normalize OminiLink provider URLs"
```

---

### Task 2: Discover OminiLink models without false Ark detection

**Files:**
- Modify: `tests/test_ominilink_provider.py`
- Modify: `main.py:10833-11420`

**Interfaces:**
- Produces: `OMINILINK_VIDEO_MODELS: tuple[str, ...]`
- Produces: `merge_ominilink_model_catalog(base_url, grouped, ids) -> tuple[dict, list[str]]`
- Produces: fallback response fields `catalog_fallback: bool`, `connection_verified: bool`
- Consumes: Task 1 `is_ominilink_api_url`

- [ ] **Step 1: Add failing async tests for false probes and catalog fallback**

```python
from unittest.mock import AsyncMock


class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = {}

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return next(self.responses)

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return next(self.responses)


class OminiLinkDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_ark_404_is_not_route_success(self):
        client = AsyncMock()
        client.get.return_value = FakeResponse(404, {"error": "not found"}, '{"error":"not found"}')
        ok, _ = await main.probe_volcengine_task_endpoint(
            client, "https://vg-api.aig-ai.com/v1", "secret"
        )
        self.assertFalse(ok)

    async def test_openai_chat_404_is_not_route_success(self):
        client = AsyncMock()
        client.post.return_value = FakeResponse(404, {"error": "not found"}, '{"error":"not found"}')
        ok, _ = await main.probe_openai_compat_bearer_endpoint(
            client, "https://api.aig-ai.com/v1", "secret"
        )
        self.assertFalse(ok)

    def test_catalog_marks_omni_as_chat_and_video(self):
        grouped, ids = main.merge_ominilink_model_catalog(
            "https://api.aig-ai.com/v1",
            {"image": [], "chat": ["upstream-chat"], "video": []},
            ["upstream-chat"],
        )
        self.assertIn("gemini-omni-flash-preview", grouped["chat"])
        self.assertIn("gemini-omni-flash-preview", grouped["video"])
        self.assertEqual(ids, sorted(set(ids)))

    async def test_fetch_models_404_uses_unverified_catalog(self):
        client = FakeClient([
            FakeResponse(404, {"error": "not found"}, '{"error":"not found"}'),
        ])
        with patch.object(main.httpx, "AsyncClient", return_value=client):
            result = await main.fetch_models_from_upstream(
                "https://api.aig-ai.com/v1", "secret", "openai"
            )
        self.assertIn("gemini-omni-flash-preview", result["video_models"])
        self.assertTrue(result["catalog_fallback"])
        self.assertFalse(result["connection_verified"])
        self.assertEqual(len(client.calls), 1)

    async def test_fetch_models_401_never_uses_catalog_fallback(self):
        client = FakeClient([
            FakeResponse(401, {"error": "unauthorized"}, '{"error":"unauthorized"}'),
        ])
        with patch.object(main.httpx, "AsyncClient", return_value=client):
            with self.assertRaises(main.HTTPException) as raised:
                await main.fetch_models_from_upstream(
                    "https://api.aig-ai.com/v1", "secret", "openai"
                )
        self.assertEqual(raised.exception.status_code, 401)
```

- [ ] **Step 2: Run discovery tests and verify they fail for current 404 behavior**

Run: `python -m unittest tests.test_ominilink_provider -v`

Expected: FAIL because both probe helpers currently treat `404 < 500` as reachable and the catalog helper is absent.

- [ ] **Step 3: Implement explicit route-status rules and the documented catalog**

Use this predicate in both probe helpers after handling redirects, authentication, and HTML:

```python
def route_probe_succeeded(status_code: int) -> bool:
    return 200 <= int(status_code or 0) < 500 and int(status_code) not in {404, 405}
```

Define the exact documented model tuple from the approved spec. Merge with:

```python
def merge_ominilink_model_catalog(base_url, grouped, ids):
    if not is_ominilink_api_url(base_url):
        return grouped, ids
    merged_ids = sorted(set([*ids, *OMINILINK_VIDEO_MODELS]))
    merged = {kind: sorted(set(grouped.get(kind) or [])) for kind in ("image", "chat", "video")}
    merged["video"] = sorted(set([*merged["video"], *OMINILINK_VIDEO_MODELS]))
    merged["chat"] = sorted(set([*merged["chat"], "gemini-omni-flash-preview"]))
    return merged, merged_ids
```

In `test_provider_connection` and `fetch_models_from_upstream`, merge the catalog after a successful
model-list response. When the exact OminiLink host returns `404`, HTML, `405`, or a network timeout,
return the catalog with `catalog_fallback=True`, `connection_verified=False`, and a Chinese message
that says the account's actual permission was not verified. Raise authentication failures unchanged.

- [ ] **Step 4: Run focused discovery tests**

Run: `python -m unittest tests.test_ominilink_provider -v`

Expected: PASS, including the unknown-host and `401/403` cases.

- [ ] **Step 5: Commit model discovery**

```powershell
git add -- tests/test_ominilink_provider.py
git add -p -- main.py
git diff --cached --check
git commit -m "feat: discover OminiLink video models"
```

---

### Task 3: Expose the dual URL and catalog status in API Settings

**Files:**
- Create: `tests/api-settings-ominilink.test.js`
- Modify: `static/api-settings.html:64-91`
- Modify: `static/js/api-settings.js:1-15`
- Modify: `static/js/api-settings.js:695-735`
- Modify: `static/js/api-settings.js:2360-2440`
- Modify: `static/js/api-settings.js:2680-2880`
- Modify: `static/js/api-settings.js:3303-3365`

**Interfaces:**
- Produces: DOM element `#videoBaseInput`
- Produces: `isOminiLinkApiUrl(value) -> boolean`
- Produces: `defaultOminiLinkVideoBaseUrl(value) -> string`
- Consumes: backend `video_base_url`, `catalog_fallback`, `connection_verified`

- [ ] **Step 1: Add a failing VM-based API Settings test**

Create the same fake-DOM/fake-fetch harness used by
`tests/api-settings-primary-provider.test.js`, expose `syncEditor`, `renderEditor`,
`saveProviders`, `isOminiLinkApiUrl`, and `defaultOminiLinkVideoBaseUrl`, then assert:

```javascript
assert.equal(api.isOminiLinkApiUrl('https://api.aig-ai.com/v1'), true);
assert.equal(api.isOminiLinkApiUrl('https://portal.ominilink.ai/'), false);
assert.equal(
  api.defaultOminiLinkVideoBaseUrl('https://api.aig-ai.com/v1'),
  'https://vg-api.aig-ai.com/v1'
);

elements.get('baseInput').value = 'https://api.aig-ai.com/v1';
elements.get('videoBaseInput').value = 'https://vg-api.aig-ai.com/v1';
assert.equal(await api.saveProviders(), true);
const body = JSON.parse(calls.find(call => call.options.method === 'PUT').options.body);
assert.equal(body[0].video_base_url, 'https://vg-api.aig-ai.com/v1');
assert.ok(!JSON.stringify(body).includes('secret-value'));
```

Also mock a catalog-fallback verification response and assert the UI contains “官方目录兜底” and
does not contain “API Key 验证通过”.

- [ ] **Step 2: Run the JS test and verify it fails**

Run: `node tests/api-settings-ominilink.test.js`

Expected: FAIL because `videoBaseInput` and OminiLink helpers do not exist.

- [ ] **Step 3: Add and wire the optional video URL field**

Add below `#baseInput`:

```html
<label class="field full api-video-base-url-field">
  <span class="label">视频 API 地址（可选）</span>
  <div class="field-frame">
    <input id="videoBaseInput" type="text" placeholder="https://vg-api.aig-ai.com/v1">
  </div>
  <span class="hint">OminiLink 的聊天与视频使用不同入口；留空时会按已知 API 主机自动补全。</span>
</label>
```

Add exact-host JS helpers using `new URL(value).hostname`, populate the field in `renderEditor`,
copy it to `item.video_base_url` in `syncEditor`, include it in the PUT payload, and send it in
test/fetch request bodies. When the base URL is an exact OminiLink chat/video host and the video
field is empty, fill the derived video URL without changing other providers.

Render verification status as:

```javascript
const sourceNote = data.catalog_fallback
  ? ' · 官方目录兜底（未验证当前账号权限）'
  : data.connection_verified === false
    ? ' · 未验证当前账号权限'
    : '';
```

- [ ] **Step 4: Run API Settings tests**

Run: `node tests/api-settings-ominilink.test.js`

Run: `node tests/api-settings-primary-provider.test.js`

Expected: PASS.

- [ ] **Step 5: Commit API Settings changes**

```powershell
git add -- tests/api-settings-ominilink.test.js
git add -p -- static/api-settings.html static/js/api-settings.js
git diff --cached --check
git commit -m "feat: configure OminiLink video endpoint"
```

---

### Task 4: Build and validate Omni Flash multimodal requests

**Files:**
- Create: `tests/test_ominilink_video.py`
- Modify: `main.py:6479-6555`
- Modify: `main.py:11745-12020`

**Interfaces:**
- Produces: `ominilink_video_api_root(provider: dict) -> str`
- Produces: `async build_ominilink_omni_request(payload: CanvasVideoRequest) -> dict`
- Produces: `probe_local_video_duration_seconds(value: str) -> Optional[float]`
- Consumes: Task 1 normalized `video_base_url`

- [ ] **Step 1: Add failing request-builder tests**

```python
class OminiLinkVideoRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_to_video_body(self):
        payload = main.CanvasVideoRequest(
            prompt="a flying ship", model="gemini-omni-flash-preview",
            duration=6, aspect_ratio="16:9",
        )
        body = await main.build_ominilink_omni_request(payload)
        self.assertEqual(body["generation_config"]["video_config"]["task"], "text_to_video")
        self.assertEqual(body["response_format"]["duration"], "6s")
        self.assertEqual(body["input"][0]["content"][0], {"type": "text", "text": "a flying ship"})

    async def test_image_to_video_contains_mime_and_base64(self):
        payload = main.CanvasVideoRequest(
            prompt="move", model="gemini-omni-flash-preview",
            images=[main.AIReference(url="data:image/png;base64,aGVsbG8=")],
        )
        body = await main.build_ominilink_omni_request(payload)
        content = body["input"][0]["content"]
        self.assertEqual(body["generation_config"]["video_config"]["task"], "image_to_video")
        self.assertIn({"type": "image", "data": "aGVsbG8=", "mime_type": "image/png"}, content)

    async def test_video_edit_rejects_known_reference_over_three_seconds(self):
        payload = main.CanvasVideoRequest(
            prompt="edit", model="gemini-omni-flash-preview", videos=["/assets/input/long.mp4"],
        )
        with patch.object(main, "probe_local_video_duration_seconds", return_value=3.1):
            with self.assertRaises(main.HTTPException) as raised:
                await main.build_ominilink_omni_request(payload)
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("3 秒", raised.exception.detail)

    async def test_unsupported_input_combinations_fail_locally(self):
        cases = [
            (
                main.CanvasVideoRequest(
                    prompt="x", model="gemini-omni-flash-preview",
                    images=[main.AIReference(url="data:image/png;base64,YQ==")],
                    videos=["data:video/mp4;base64,YQ=="],
                ),
                "不能同时",
            ),
            (
                main.CanvasVideoRequest(
                    prompt="x", model="gemini-omni-flash-preview",
                    audios=["/assets/input/a.mp3"],
                ),
                "不支持音频",
            ),
            (
                main.CanvasVideoRequest(
                    prompt="x", model="gemini-omni-flash-preview", duration=2,
                ),
                "3 到 10",
            ),
            (
                main.CanvasVideoRequest(
                    prompt="x", model="gemini-omni-flash-preview", duration=11,
                ),
                "3 到 10",
            ),
        ]
        for payload, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaises(main.HTTPException) as raised:
                    await main.build_ominilink_omni_request(payload)
                self.assertEqual(raised.exception.status_code, 400)
                self.assertIn(expected, raised.exception.detail)
```

Extend the same table with two-image and two-video payloads and assert the messages contain
“只支持一张参考图” and “只支持一个参考视频”.

- [ ] **Step 2: Run request-builder tests and verify they fail**

Run: `python -m unittest tests.test_ominilink_video.OminiLinkVideoRequestTests -v`

Expected: FAIL because the builder and video-duration probe are absent.

- [ ] **Step 3: Implement media parsing and request construction**

Implement:

```python
def split_data_url(value: str) -> tuple[str, str]:
    match = re.fullmatch(r"data:([^;,]+);base64,(.+)", str(value or ""), re.S)
    if not match:
        raise HTTPException(status_code=400, detail="参考素材必须能转换为 base64 数据")
    return match.group(1).lower(), re.sub(r"\s+", "", match.group(2))
```

Use existing image conversion for `AIReference`, and a video helper that accepts an existing data URL
or resolves a local `/assets/` or `/output/` file with `local_media_path_for_cloud_upload`. Encode the
file bytes with `base64.b64encode`. Add a video-duration probe mirroring
`probe_local_audio_duration_seconds`, using `ffprobe -show_entries format=duration`.

Build the exact approved request shape:

```python
body = {
    "model": "gemini-omni-flash-preview",
    "background": True,
    "input": [{"type": "user_input", "content": content}],
    "generation_config": {
        "thinking_level": "low",
        "thinking_summaries": "auto",
        "video_config": {"task": task},
    },
    "response_format": {
        "type": "video",
        "delivery": "uri",
        "aspect_ratio": payload.aspect_ratio or "16:9",
        "duration": f"{payload.duration}s",
    },
}
```

The first content item is text. Add exactly one `image` item for image-to-video or one `video` item
for edit. Reject unsupported combinations with the Chinese messages asserted by the tests.

- [ ] **Step 4: Run the focused builder tests**

Run: `python -m unittest tests.test_ominilink_video.OminiLinkVideoRequestTests -v`

Expected: PASS.

- [ ] **Step 5: Commit the request builder**

```powershell
git add -- tests/test_ominilink_video.py
git add -p -- main.py
git diff --cached --check
git commit -m "feat: build Omni Flash video requests"
```

---

### Task 5: Submit, poll, parse, and save OminiLink video results

**Files:**
- Modify: `tests/test_ominilink_video.py`
- Modify: `main.py:11745-12020`
- Modify: `main.py:12363-12822`

**Interfaces:**
- Produces: `ominilink_video_output_urls(raw: dict) -> list[str]`
- Produces: `ominilink_video_size(aspect_ratio: str, resolution: str = "") -> str`
- Produces: `async wait_for_ominilink_video_task(client, provider, model, task_id) -> dict`
- Produces: `async generate_ominilink_video(payload, provider) -> dict`
- Consumes: Task 4 `build_ominilink_omni_request`, Task 1 `video_base_url`

- [ ] **Step 1: Add failing adapter tests with a recording fake client**

```python
class RecordingClient:
    def __init__(self, post_responses, get_responses=()):
        self.post_responses = iter(post_responses)
        self.get_responses = iter(get_responses)
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return next(self.post_responses)

    async def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return next(self.get_responses)


class OminiLinkVideoAdapterTests(unittest.IsolatedAsyncioTestCase):
    def test_extracts_only_nested_video_uri(self):
        raw = {"steps": [{"content": [
            {"type": "text", "text": "done"},
            {"type": "video", "uri": "https://cdn.example.test/result.mp4"},
        ]}]}
        self.assertEqual(
            main.ominilink_video_output_urls(raw),
            ["https://cdn.example.test/result.mp4"],
        )

    async def test_omni_submit_and_get_poll(self):
        submitted = FakeResponse(200, {"id": "job-1", "status": "queued"}, '{"id":"job-1"}')
        completed = FakeResponse(200, {
            "id": "job-1", "status": "completed",
            "steps": [{"content": [{"type": "video", "uri": "https://cdn.example.test/v.mp4"}]}],
        }, '{"status":"completed"}')
        client = RecordingClient([submitted], [completed])
        provider = {
            "id": "orange", "name": "橙域",
            "base_url": "https://api.aig-ai.com/v1",
            "video_base_url": "https://vg-api.aig-ai.com/v1",
        }
        with patch.object(main.asyncio, "sleep", new=AsyncMock()), \
             patch.object(main, "save_remote_video_to_output", new=AsyncMock(return_value="/assets/output/v.mp4")):
            result = await main.generate_ominilink_video(
                main.CanvasVideoRequest(prompt="go", provider_id="orange",
                                        model="gemini-omni-flash-preview", duration=6),
                provider, client=client,
            )
        self.assertEqual(client.calls[0][0:2], (
            "POST", "https://vg-api.aig-ai.com/v1/gemini-omni-flash-preview"
        ))
        self.assertEqual(client.calls[1][0:2], (
            "GET", "https://vg-api.aig-ai.com/v1/query/gemini-omni-flash-preview/job-1"
        ))
        self.assertEqual(result["videos"], ["/assets/output/v.mp4"])

    async def test_completed_without_video_uri_is_terminal_error(self):
        completed = FakeResponse(
            200, {"id": "job-empty", "status": "completed", "steps": []},
            '{"id":"job-empty","status":"completed"}',
        )
        client = RecordingClient([completed])
        provider = {
            "id": "orange",
            "base_url": "https://api.aig-ai.com/v1",
            "video_base_url": "https://vg-api.aig-ai.com/v1",
        }
        with self.assertRaises(main.HTTPException) as raised:
            await main.generate_ominilink_video(
                main.CanvasVideoRequest(
                    prompt="go", model="gemini-omni-flash-preview", duration=6
                ),
                provider, client=client,
            )
        self.assertEqual(raised.exception.status_code, 502)
        self.assertIn("job-empty", raised.exception.detail)

    async def test_non_omni_model_uses_basic_body_and_post_query(self):
        submitted = FakeResponse(200, {"id": "job-2", "status": "queued"}, "")
        completed = FakeResponse(
            200, {"id": "job-2", "status": "completed",
                  "output_url": "https://cdn.example.test/basic.mp4"}, ""
        )
        client = RecordingClient([submitted, completed])
        provider = {
            "id": "orange",
            "base_url": "https://api.aig-ai.com/v1",
            "video_base_url": "https://vg-api.aig-ai.com/v1",
        }
        with patch.object(main.asyncio, "sleep", new=AsyncMock()), \
             patch.object(main, "save_remote_video_to_output",
                          new=AsyncMock(return_value="/assets/output/basic.mp4")):
            await main.generate_ominilink_video(
                main.CanvasVideoRequest(prompt="go", model="seedance-2.0", duration=6),
                provider, client=client,
            )
        self.assertEqual(client.calls[0][0], "POST")
        self.assertEqual(client.calls[0][2]["json"], {
            "prompt": "go", "size": "1280x720", "seconds": "6"
        })
        self.assertEqual(client.calls[1][0], "POST")
```

Add named methods `test_immediate_completed_response_skips_poll`,
`test_safety_failure_uses_humanized_message`, `test_poll_timeout_raises_504`, and
`test_html_submit_response_reports_non_json`. Each uses `RecordingClient`, patches sleep/time where
needed, and asserts respectively: one POST call; the Chinese safety summary; status `504`; and the
phrase “返回网页而不是 JSON”.

- [ ] **Step 2: Run adapter tests and verify they fail**

Run: `python -m unittest tests.test_ominilink_video.OminiLinkVideoAdapterTests -v`

Expected: FAIL because the dedicated adapter functions do not exist.

- [ ] **Step 3: Implement the dedicated adapter and inject it into `canvas_video`**

`ominilink_video_output_urls` must walk `steps -> content` and only accept entries whose
`type == "video"` and whose `uri` begins with `http://` or `https://`.

`wait_for_ominilink_video_task` must:

```python
query_url = (
    f"{ominilink_video_api_root(provider)}/query/"
    f"{urllib.parse.quote(model, safe='')}/{urllib.parse.quote(task_id, safe='')}"
)
response = (
    await client.get(query_url, headers=api_headers(provider=provider, model=model))
    if model == "gemini-omni-flash-preview"
    else await client.post(query_url, headers=api_headers(provider=provider, model=model), json={})
)
```

Use the existing success/failure status sets, `VIDEO_POLL_TIMEOUT`, exponential delay, and
`humanize_video_task_failure`. If status is completed but no video URI exists, raise a 502 error
containing the non-secret task ID.

`generate_ominilink_video` accepts an optional injected client for tests; when absent, create and
close an `httpx.AsyncClient`. Build the Omni body for Omni Flash and the documented basic body for
other catalog models:

```python
{"prompt": payload.prompt, "size": ominilink_video_size(payload.aspect_ratio, payload.resolution),
 "seconds": str(payload.duration)}
```

`ominilink_video_size` returns `1280x720`, `720x1280`, `720x720`, `960x720`,
`720x960`, `1680x720`, or `720x1680` for `16:9`, `9:16`, `1:1`, `4:3`, `3:4`,
`21:9`, or `9:21`; unknown ratios fall back to `1280x720`.

Reject references for non-Omni models. Submit to
`{video_base_url}/{quoted_model}`, parse immediate or polled output, save each URI with
`save_remote_video_to_output`, and return `{"videos": local_urls, "task_id": task_id, "raw": raw}`.

At the top of `canvas_video`, after RunningHub/Jimeng branches and before generic URL candidates:

```python
if is_ominilink_api_url(provider.get("base_url")) or is_ominilink_api_url(provider.get("video_base_url")):
    return await generate_ominilink_video(payload, provider)
```

- [ ] **Step 4: Run backend video suites**

Run: `python -m unittest tests.test_ominilink_video tests.test_canvas_video_tasks tests.test_openrouter_video_references -v`

Expected: PASS.

- [ ] **Step 5: Commit the execution adapter**

```powershell
git add -- tests/test_ominilink_video.py
git add -p -- main.py
git diff --cached --check
git commit -m "feat: execute OminiLink video tasks"
```

---

### Task 6: Add Infinite Canvas preflight and model-selection regressions

**Files:**
- Create: `tests/canvas-ominilink-video.test.js`
- Modify: `static/js/canvas.js:600-760`
- Modify: `static/js/canvas.js:8370-8580`
- Modify: `static/js/canvas.js:10360-10440`

**Interfaces:**
- Produces: `isOminiLinkProvider(provider) -> boolean`
- Produces: `omniFlashVideoValidationError(node, mediaRefs) -> string`
- Consumes: provider `video_models`, `base_url`, `video_base_url`

- [ ] **Step 1: Add a failing canvas preflight test**

Extract production functions with the pattern in `tests/canvas-openrouter-video-reference.test.js`
and assert:

```javascript
const provider = {
  id:'orange',
  base_url:'https://api.aig-ai.com/v1',
  video_base_url:'https://vg-api.aig-ai.com/v1',
  video_models:['gemini-omni-flash-preview']
};
assert.equal(context.isOminiLinkProvider(provider), true);
assert.equal(context.isOminiLinkProvider({
  base_url:'https://portal.ominilink.ai/'
}), false);
assert.match(
  context.omniFlashVideoValidationError(
    {model:'gemini-omni-flash-preview', duration:6},
    [{kind:'image'}, {kind:'video'}]
  ),
  /不能同时/
);
assert.match(
  context.omniFlashVideoValidationError(
    {model:'gemini-omni-flash-preview', duration:2},
    []
  ),
  /3.*10/
);
const runVideoNode = productionFunction('runVideoNode');
assert.ok(
  runVideoNode.indexOf('omniFlashVideoValidationError(')
    < runVideoNode.indexOf('createCanvasVideoTask('),
  'Omni validation must run before the paid task is created'
);
assert.match(source, /video_models/);
assert.match(source, /gemini-omni-flash-preview/);
```

Assert the `runVideoNode` source invokes the validation before `createCanvasVideoTask`.
Extract `videoProviderOptions` and `videoModelOptions` and execute them with the OminiLink provider;
assert the returned markup contains `value="orange"` and
`value="gemini-omni-flash-preview"`.

- [ ] **Step 2: Run the canvas test and verify it fails**

Run: `node tests/canvas-ominilink-video.test.js`

Expected: FAIL because the provider/validation helpers are absent.

- [ ] **Step 3: Implement exact-host frontend recognition and preflight**

Implement exact `new URL(...).hostname` checks against the four approved hosts. The validation helper
returns an empty string for non-Omni models, otherwise returns Chinese errors for:

```javascript
if(duration < 3 || duration > 10) return 'Omni Flash 视频时长必须在 3 到 10 秒之间。';
if(audioCount) return 'Omni Flash 不支持音频参考，请移除音频后重试。';
if(imageCount && videoCount) return 'Omni Flash 不能同时提交图片和视频参考。';
if(imageCount > 1) return 'Omni Flash 图片生视频只支持一张参考图。';
if(videoCount > 1) return 'Omni Flash 视频编辑只支持一个参考视频。';
```

Call it in `runVideoNode` after collecting refs and resolving the effective provider/model, but before
creating output pending state or calling the backend. Throw during cascade runs and show `alert`
during manual runs. Do not remove the backend validation.

- [ ] **Step 4: Run canvas regressions**

Run: `node tests/canvas-ominilink-video.test.js`

Run: `node tests/canvas-provider-defaults.test.js`

Run: `node tests/canvas-follow-default-provider.test.js`

Run: `node tests/canvas-video-task-lifecycle.test.js`

Expected: PASS.

- [ ] **Step 5: Commit canvas preflight**

```powershell
git add -- tests/canvas-ominilink-video.test.js
git add -p -- static/js/canvas.js
git diff --cached --check
git commit -m "feat: validate Omni Flash canvas inputs"
```

---

### Task 7: Run full verification and security checks

**Files:**
- Modify only if a failing test reveals a scoped defect: `main.py`, `static/api-settings.html`, `static/js/api-settings.js`, `static/js/canvas.js`, or the new OminiLink tests

**Interfaces:**
- Consumes all previous tasks.
- Produces a verified branch ready for the existing local-merge workflow.

- [ ] **Step 1: Run Python syntax and focused backend suites**

Run:

```powershell
python -m py_compile main.py
python -m unittest tests.test_ominilink_provider tests.test_ominilink_video tests.test_primary_provider tests.test_canvas_video_tasks tests.test_openrouter_video_references -v
```

Expected: all tests PASS.

- [ ] **Step 2: Run all JavaScript regression scripts**

Run:

```powershell
Get-ChildItem tests -Filter '*.test.js' | ForEach-Object {
    node $_.FullName
    if ($LASTEXITCODE -ne 0) { throw "JS test failed: $($_.Name)" }
}
```

Expected: every script exits `0`.

- [ ] **Step 3: Run all Python unit tests**

Run: `python -m unittest discover -s tests -p 'test_*.py' -v`

Expected: all tests PASS.

- [ ] **Step 4: Verify no secrets and no unrelated files are staged**

Run:

```powershell
git diff --check
git diff --cached --name-only
git diff --cached | Select-String -Pattern 'sk-[A-Za-z0-9]|Bearer\s+[A-Za-z0-9]|api_key"\s*:\s*"[^"]+'
```

Expected: no whitespace errors, only scoped files are staged, and the secret scan returns no match.

- [ ] **Step 5: Perform a read-only local UI smoke test**

Start the existing local service without changing provider keys, open API Settings and Infinite
Canvas, and verify:

1. An OminiLink provider shows both chat and video addresses.
2. Model fallback shows `gemini-omni-flash-preview` in chat and video selectors.
3. The canvas video node preserves the selected OminiLink provider/model.
4. Unsupported mixed inputs fail before a task is submitted.

Do not press the real video-generation button during this smoke test.

- [ ] **Step 6: Commit any verification-only scoped fixes**

```powershell
git add -- tests/test_ominilink_provider.py tests/test_ominilink_video.py tests/api-settings-ominilink.test.js tests/canvas-ominilink-video.test.js
git add -p -- main.py static/api-settings.html static/js/api-settings.js static/js/canvas.js
git diff --cached --check
git commit -m "test: verify OminiLink Omni Flash integration"
```

Skip this commit when verification required no source changes.
