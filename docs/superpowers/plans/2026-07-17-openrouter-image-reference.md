# OpenRouter Image Reference Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route OpenRouter image generation through `/api/v1/images` and deliver canvas reference images via `input_references` without changing other providers.

**Architecture:** Add one OpenRouter-specific generator beside the existing provider-specific generators and dispatch to it before the generic OpenAI Images path. Reuse `openrouter_api_root`, `reference_to_data_url`, `api_headers`, and `extract_image` so URL normalization, authentication, image preprocessing, and response parsing remain centralized.

**Tech Stack:** Python 3.11, FastAPI application code, httpx async client, unittest with controlled HTTP fakes.

## Global Constraints

- Do not make a real paid OpenRouter generation request during automated verification.
- Preserve all non-OpenRouter provider behavior.
- Preserve reference-image order and cap it with `ONLINE_IMAGE_REFERENCE_MAX`.
- Do not fall back from OpenRouter to `/images/edits` or `/images/generations`.
- The supplied project has no `.git` directory, so commit steps cannot be executed.

---

### Task 1: OpenRouter dedicated image request

**Files:**
- Create: `tests/test_openrouter_image_generation.py`
- Modify: `main.py:8859-8870`

**Interfaces:**
- Consumes: `is_openrouter_provider(provider)`, `openrouter_api_root(base_url)`, `reference_to_data_url(ref, max_size=1536)`, `api_headers(provider=provider, model=model)`, `extract_image(raw)`.
- Produces: `generate_openrouter_provider_image(prompt, size, quality, model, reference_images=None, provider=None) -> tuple[dict, dict]` and an OpenRouter dispatch branch in `generate_ai_image`.

- [ ] **Step 1: Write the failing regression tests**

Create `tests/test_openrouter_image_generation.py`:

```python
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main


class FakeImageResponse:
    status_code = 200
    text = ""

    def raise_for_status(self):
        return None

    def json(self):
        return {"data": [{"b64_json": "generated-image"}]}


class RecordingAsyncClient:
    def __init__(self):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return FakeImageResponse()


class OpenRouterImageGenerationTests(unittest.IsolatedAsyncioTestCase):
    provider = {
        "id": "custom-api",
        "name": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "protocol": "openai",
        "image_request_mode": "openai",
    }

    async def generate(self, references):
        client = RecordingAsyncClient()
        with patch.object(main, "get_api_provider", return_value=self.provider), patch.object(
            main.httpx, "AsyncClient", return_value=client
        ), patch.object(main, "api_headers", return_value={"Authorization": "Bearer test"}):
            result = await main.generate_ai_image(
                "keep image one structure and use image two style",
                "1280x720",
                "high",
                "google/gemini-3-pro-image",
                references,
                "custom-api",
            )
        return client, result

    async def test_reference_images_use_openrouter_images_endpoint_and_input_references(self):
        references = [
            {"url": "data:image/png;base64,AAA", "name": "structure.png"},
            {"url": "data:image/png;base64,BBB", "name": "style.png"},
        ]

        client, (image, raw) = await self.generate(references)

        self.assertEqual([call["url"] for call in client.calls], ["https://openrouter.ai/api/v1/images"])
        body = client.calls[0]["json"]
        self.assertEqual(body["model"], "google/gemini-3-pro-image")
        self.assertEqual(body["prompt"], "keep image one structure and use image two style")
        self.assertEqual(body["size"], "1280x720")
        self.assertEqual(body["quality"], "high")
        self.assertEqual(body["n"], 1)
        self.assertEqual(body["input_references"], [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,BBB"}},
        ])
        self.assertNotIn("image", body)
        self.assertEqual(image["type"], "b64")
        self.assertEqual(image["value"], "generated-image")
        self.assertEqual(raw["data"][0]["b64_json"], "generated-image")

    async def test_text_to_image_omits_empty_input_references(self):
        client, _ = await self.generate([])

        self.assertEqual([call["url"] for call in client.calls], ["https://openrouter.ai/api/v1/images"])
        self.assertNotIn("input_references", client.calls[0]["json"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_openrouter_image_generation.py" -v
```

Expected: both tests fail because current code calls `/images/edits` for references and `/images/generations` for text-only requests instead of `/images`.

- [ ] **Step 3: Implement the minimal OpenRouter generator**

Add immediately before `generate_ai_image` in `main.py`:

```python
async def generate_openrouter_provider_image(prompt, size, quality, model, reference_images=None, provider=None):
    provider = provider or {}
    api_root = openrouter_api_root(provider.get("base_url") or "")
    if not api_root:
        raise HTTPException(status_code=400, detail="OpenRouter 未配置 Base URL")
    body = {
        "model": model,
        "prompt": prompt,
        "n": 1,
    }
    if size:
        body["size"] = size
    normalized_quality = str(quality or "").strip().lower()
    if normalized_quality in {"low", "medium", "high"}:
        body["quality"] = normalized_quality
    input_references = []
    for ref in (reference_images or [])[:ONLINE_IMAGE_REFERENCE_MAX]:
        image_url = reference_to_data_url(ref, max_size=1536)
        if image_url:
            input_references.append({
                "type": "image_url",
                "image_url": {"url": image_url},
            })
    if input_references:
        body["input_references"] = input_references
    timeout = httpx.Timeout(connect=20.0, read=1800.0, write=120.0, pool=20.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{api_root}/images",
            headers=api_headers(provider=provider, model=model),
            json=body,
        )
        response.raise_for_status()
        raw = response.json()
        return extract_image(raw), raw
```

Add this dispatch after RunningHub and before the Gemini protocol check in `generate_ai_image`:

```python
    if is_openrouter_provider(provider):
        return await generate_openrouter_provider_image(prompt, size, quality, model, reference_images, provider)
```

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_openrouter_image_generation.py" -v
```

Expected: `Ran 2 tests` and `OK`.

- [ ] **Step 5: Run the complete local regression suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
node .\tests\api-settings-default-openrouter.test.js
node .\tests\canvas-output-auto-export.test.js
```

Expected: Python unittest reports `OK`; both Node scripts print their `passed` message and exit with code 0.

- [ ] **Step 6: Restart and health-check the local service**

Stop only the process currently listening on port 3000, start `main.py` with the project virtual environment, then run:

```powershell
Invoke-WebRequest -Uri 'http://127.0.0.1:3000/' -UseBasicParsing -TimeoutSec 5
```

Expected: HTTP status 200 and page title `AI Studio`.

- [ ] **Step 7: Record the no-commit constraint**

Run:

```powershell
Test-Path .\.git
```

Expected: `False`; do not stage or commit files because this directory is not a Git repository.
