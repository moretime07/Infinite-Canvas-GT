# OpenRouter Online Image Default Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Online Image page default to the configured OpenRouter provider and its first image model.

**Architecture:** Change only the page-level initial provider ID. Reuse the existing `/api/config` loading, provider validation, first-model selection, fallback behavior, and request payload construction.

**Tech Stack:** Static HTML, browser JavaScript, Node.js built-in test modules

## Global Constraints

- The OpenRouter provider ID is exactly `custom-api`.
- If OpenRouter is unavailable, retain the existing fallback to the first usable image provider.
- Do not change manual provider or model switching.
- Do not commit runtime provider configuration or API keys.

---

### Task 1: Default Online Image generation to OpenRouter

**Files:**
- Create: `tests/online-default-openrouter.test.js`
- Modify: `static/online.html:256`

**Interfaces:**
- Consumes: `/api/config` provider objects with `id`, `enabled`, and `image_models` fields.
- Produces: initial page state with `provider === 'custom-api'`; existing `renderProviderControls()` selects the first configured OpenRouter image model or falls back to the first available provider.

- [ ] **Step 1: Write the failing test**

```javascript
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const source = fs.readFileSync(path.resolve(__dirname, '..', 'static', 'online.html'), 'utf8');
assert.match(source, /let\s+provider\s*=\s*['"]custom-api['"]\s*;/, 'Online Image should initialize with OpenRouter');
assert.match(source, /if\(!providers\.some\(p\s*=>\s*p\.id\s*===\s*provider\)\)\s*provider\s*=\s*providers\[0\]\?\.id/, 'Unavailable OpenRouter should retain the existing provider fallback');
console.log('online-default-openrouter: passed');
```

- [ ] **Step 2: Run the test and verify the current implementation fails**

Run: `node tests/online-default-openrouter.test.js`

Expected: FAIL because `static/online.html` initializes `provider` to `comfly`.

- [ ] **Step 3: Make the minimal implementation change**

```javascript
let provider = 'custom-api';
```

- [ ] **Step 4: Run focused and existing JavaScript regression tests**

Run: `node tests/online-default-openrouter.test.js; node tests/api-settings-default-openrouter.test.js; node tests/canvas-output-auto-export.test.js`

Expected: all three commands exit with code 0 and print their `passed` messages.

- [ ] **Step 5: Verify the live page**

Open `http://127.0.0.1:3000/static/online.html`, reload it, and inspect `#providerSelect` and `#modelSelect`.

Expected: provider value is `custom-api`, visible label is `openrouter`, and the model value equals the first OpenRouter `image_models` entry returned by `/api/config`.

- [ ] **Step 6: Verify repository safety and commit**

Run: `git diff --check; git status --short; git check-ignore -v -- data/api_providers.json`

Expected: no whitespace errors; only the planned source, test, and plan files are tracked; `data/api_providers.json` remains ignored.

```bash
git add static/online.html tests/online-default-openrouter.test.js docs/superpowers/plans/2026-07-19-openrouter-online-default.md
git commit -m "fix: default online image provider to OpenRouter"
```
