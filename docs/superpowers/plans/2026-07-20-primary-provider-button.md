# Default Primary Provider Button Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an immediate, secure “set as default” control to each API Settings provider card so the user can choose the application's unique global primary provider.

**Architecture:** A dedicated bodyless backend endpoint validates provider eligibility and atomically persists exactly one primary provider. API Settings renders a sibling star control beside each existing provider-card button, calls only the dedicated endpoint, and preserves `primary` during ordinary full-form saves. Backend transition validation prevents direct callers from deleting or disabling the current primary without explicitly selecting an eligible replacement.

**Tech Stack:** FastAPI/Pydantic backend in `main.py`, vanilla JavaScript and CSS, Python `unittest`, Node.js `node:assert` + `vm` tests.

## Global Constraints

- The primary-switch request sends no API key, preview, or provider form payload.
- A primary candidate must exist, be enabled, have a usable provider credential, and configure at least one image, chat, or video model.
- At most one provider may have `primary === true` after any successful save.
- The current primary provider cannot be disabled or deleted without an explicitly selected eligible replacement in the same request.
- Existing saved page, conversation, canvas, and node provider choices remain authoritative; primary changes only the global fallback.
- Capability selection remains independent: a primary is preferred only for capabilities it supports.
- Do not call Generate, Send, Run, or any paid model endpoint during verification.
- Runtime files under `data/` and secrets under `API/.env` remain Git-ignored and must never be staged.

---

### Task 1: Backend eligibility helper and atomic primary endpoint

**Files:**
- Modify: `main.py:620-680`
- Modify: `main.py:1249-1285`
- Modify: `main.py:10594-10600`
- Create: `tests/test_primary_provider.py`

**Interfaces:**
- Produces: `provider_has_primary_credential(provider: dict, credential_overrides: dict | None = None) -> bool`.
- Produces: `provider_primary_ineligibility(provider: dict, credential_overrides: dict | None = None) -> str` returning an empty string when eligible.
- Produces: `set_primary_api_provider(provider_id: str)` for `PUT /api/providers/{provider_id}/primary`.
- Consumes: `load_api_providers`, `save_api_providers`, `public_provider`, `provider_env_key_value`, and `runninghub_wallet_key_value`.

- [ ] **Step 1: Write failing backend tests for eligibility and atomic switching**

Create `tests/test_primary_provider.py`:

```python
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


def provider(provider_id, **overrides):
    item = {
        "id": provider_id,
        "name": provider_id,
        "base_url": "https://example.test/v1",
        "enabled": True,
        "primary": False,
        "image_models": [],
        "chat_models": ["chat-model"],
        "video_models": [],
    }
    item.update(overrides)
    return item


class PrimaryProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_switch_sets_exactly_one_primary_without_key_payload(self):
        stored = [provider("old", primary=True), provider("next")]
        saved = []
        with patch.object(main, "load_api_providers", return_value=stored), patch.object(
            main, "provider_env_key_value", side_effect=lambda pid: "secret" if pid == "next" else "old-secret"
        ), patch.object(main, "save_api_providers", side_effect=lambda items: saved.extend(items)):
            result = await main.set_primary_api_provider("next")

        self.assertEqual([item["id"] for item in saved if item["primary"]], ["next"])
        self.assertEqual([item["id"] for item in stored if item["primary"]], ["old"])
        self.assertNotIn("api_key", str(result))

    async def test_rejects_unknown_disabled_unkeyed_and_modelless_candidates(self):
        cases = [
            ("missing", [provider("known")], "不存在"),
            ("disabled", [provider("disabled", enabled=False)], "停用"),
            ("unkeyed", [provider("unkeyed")], "密钥"),
            ("modelless", [provider("modelless", chat_models=[])], "模型"),
        ]
        for target, stored, message in cases:
            with self.subTest(target=target), patch.object(main, "load_api_providers", return_value=stored), patch.object(
                main, "provider_env_key_value", return_value="" if target == "unkeyed" else "secret"
            ), patch.object(main, "save_api_providers") as save:
                with self.assertRaises(HTTPException) as raised:
                    await main.set_primary_api_provider(target)
                self.assertIn(message, raised.exception.detail)
                save.assert_not_called()

    def test_runninghub_accepts_either_existing_runnable_key(self):
        item = provider("runninghub")
        with patch.object(main, "provider_env_key_value", return_value=""), patch.object(
            main, "runninghub_wallet_key_value", return_value="wallet-key"
        ):
            self.assertTrue(main.provider_has_primary_credential(item))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_primary_provider -v
```

Expected: FAIL because `set_primary_api_provider` and `provider_has_primary_credential` do not exist.

- [ ] **Step 3: Implement eligibility helpers and the dedicated endpoint**

Add near existing provider key helpers in `main.py`:

```python
def provider_has_primary_credential(provider, credential_overrides=None):
    provider_id = str((provider or {}).get("id") or "").strip().lower()
    overrides = credential_overrides or {}
    standard_key = str(overrides.get(provider_id) or provider_env_key_value(provider_id) or "").strip()
    if provider_id == "runninghub":
        wallet_key = str(overrides.get("runninghub_wallet") or runninghub_wallet_key_value() or "").strip()
        return bool(standard_key or wallet_key)
    return bool(standard_key)


def provider_primary_ineligibility(provider, credential_overrides=None):
    if not provider:
        return "API 平台不存在"
    if provider.get("enabled", True) is False:
        return "平台已停用"
    if not provider_has_primary_credential(provider, credential_overrides):
        return "未配置可用密钥"
    if not any(provider.get(key) for key in ("image_models", "chat_models", "video_models")):
        return "未配置可用模型"
    return ""
```

Add beside the existing provider routes:

```python
@app.put("/api/providers/{provider_id}/primary")
async def set_primary_api_provider(provider_id: str):
    stored = load_api_providers()
    target = next((item for item in stored if item.get("id") == provider_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="API 平台不存在")
    reason = provider_primary_ineligibility(target)
    if reason:
        raise HTTPException(status_code=400, detail=reason)
    updated = [{**item, "primary": item.get("id") == provider_id} for item in stored]
    save_api_providers(updated)
    return {"providers": [public_provider(item) for item in updated]}
```

- [ ] **Step 4: Run focused backend tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_primary_provider -v
```

Expected: all Task 1 tests pass with no persistence call for invalid candidates.

- [ ] **Step 5: Commit the backend endpoint**

```powershell
git add main.py tests/test_primary_provider.py
git commit -m "feat: add primary provider endpoint"
```

---

### Task 2: Protect primary state in the existing provider save endpoint

**Files:**
- Modify: `main.py:10598-10655`
- Modify: `tests/test_primary_provider.py`

**Interfaces:**
- Produces: `validate_primary_transition(current: list[dict], proposed: list[dict], credential_overrides: dict | None = None) -> None`.
- Consumes: Task 1 `provider_primary_ineligibility`.
- Preserves: the existing `save_providers(payload: List[ApiProviderPayload])` request and response shape.

- [ ] **Step 1: Add failing tests for preserving and replacing the current primary**

Append to `PrimaryProviderTests`:

```python
    def test_transition_rejects_removing_or_disabling_current_primary(self):
        current = [provider("current", primary=True), provider("other")]
        for proposed in (
            [provider("other")],
            [provider("current", enabled=False), provider("other")],
            [provider("current", primary=False), provider("other")],
        ):
            with self.subTest(proposed=proposed), self.assertRaises(HTTPException) as raised:
                main.validate_primary_transition(current, proposed)
            self.assertIn("先设置另一个默认供应商", raised.exception.detail)

    def test_transition_accepts_explicit_eligible_replacement(self):
        current = [provider("current", primary=True), provider("other")]
        proposed = [provider("current", enabled=False), provider("other", primary=True)]
        with patch.object(main, "provider_env_key_value", return_value="secret"):
            main.validate_primary_transition(current, proposed)

    async def test_full_save_keeps_submitted_primary_flag(self):
        payload = [
            main.ApiProviderPayload(id="one", name="One", enabled=True, primary=True, chat_models=["chat"]),
            main.ApiProviderPayload(id="two", name="Two", enabled=True, primary=False, chat_models=["chat"]),
        ]
        saved = []
        with patch.object(main, "load_api_providers", return_value=[provider("one", primary=True), provider("two")]), patch.object(
            main, "provider_env_key_value", return_value="secret"
        ), patch.object(main, "save_api_providers", side_effect=lambda items: saved.extend(items)), patch.object(
            main, "update_env_values"
        ), patch.object(main, "reload_env_globals"):
            await main.save_providers(payload)
        self.assertEqual([item["id"] for item in saved if item["primary"]], ["one"])
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_primary_provider -v
```

Expected: FAIL because `validate_primary_transition` is absent and current-primary removal is not rejected.

- [ ] **Step 3: Implement transition validation before persistence**

Add in `main.py`:

```python
def validate_primary_transition(current, proposed, credential_overrides=None):
    current_primary = next((item for item in current if item.get("primary")), None)
    if current_primary is None:
        return
    same = next((item for item in proposed if item.get("id") == current_primary.get("id")), None)
    if same is not None and same.get("enabled", True) is not False and same.get("primary"):
        return
    replacement = next((item for item in proposed if item.get("primary") and item.get("id") != current_primary.get("id")), None)
    reason = provider_primary_ineligibility(replacement, credential_overrides)
    if replacement is None or reason:
        raise HTTPException(status_code=400, detail="请先设置另一个默认供应商")
```

In `save_providers`, load `current_providers` before building the replacement list. Build a `credential_overrides` dictionary from non-empty `api_key` and `wallet_api_key` payload fields without logging them, enforce the existing last-primary-wins normalization, then call:

```python
validate_primary_transition(current_providers, providers, credential_overrides)
```

Call it before `save_api_providers(providers)` and before applying environment updates. Keep the endpoint's existing duplicate-ID and “at least one provider” checks.

- [ ] **Step 4: Run backend regression tests**

Run separately:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_primary_provider -v
.\.venv\Scripts\python.exe -m unittest tests.test_openrouter_image_generation -v
```

Expected: both test modules pass.

- [ ] **Step 5: Commit transition protection**

```powershell
git add main.py tests/test_primary_provider.py
git commit -m "fix: preserve primary provider on settings saves"
```

---

### Task 3: API Settings card control and immediate switch behavior

**Files:**
- Modify: `static/js/api-settings.js:260-280`
- Modify: `static/js/api-settings.js:2168-2255`
- Modify: `static/js/api-settings.js:3079-3090`
- Modify: `static/js/api-settings.js:3208-3290`
- Modify: `static/css/api-settings.css`
- Create: `tests/api-settings-primary-provider.test.js`

**Interfaces:**
- Produces: `providerPrimaryIssue(item) -> string`.
- Produces: `providerCapabilityBadges(item) -> string`.
- Produces: `providerPrimaryControl(item) -> string`.
- Produces: `setPrimaryProvider(event, providerId) -> Promise<boolean>`.
- Consumes: `PUT /api/providers/{provider_id}/primary` from Task 1.

- [ ] **Step 1: Write a failing executable frontend behavior test**

Create `tests/api-settings-primary-provider.test.js`. Reuse the `fakeElement`, document, window, and VM pattern from `tests/api-settings-default-openrouter.test.js`, but expose the new functions and inject a recording fetch:

```javascript
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function fakeElement(){
    return {style:{},classList:{add(){},remove(){},toggle(){},contains(){return false;}},addEventListener(){},querySelector(){return null;},querySelectorAll(){return [];},closest(){return null;},setAttribute(){},removeAttribute(){},disabled:false,hidden:false,value:'',textContent:'',innerHTML:'',placeholder:''};
}

async function createState(providerRows){
    const calls = [];
    const messages = [];
    const elements = new Map();
    const document = {body:fakeElement(),getElementById(id){if(!elements.has(id)) elements.set(id,fakeElement());return elements.get(id);},querySelector(){return null;},querySelectorAll(){return [];},addEventListener(){}};
    const window = {addEventListener(){},parent:{postMessage(message){messages.push(message);}},top:{postMessage(message){messages.push(message);}},location:{href:''}};
    const fetch = async (url, options={}) => {
        calls.push({url, options});
        if(url === '/api/providers') return {ok:true,json:async()=>({providers:providerRows})};
        if(url.endsWith('/primary')) return {ok:true,json:async()=>({providers:providerRows.map(item=>({...item,primary:url.includes(item.id)}))})};
        throw new Error(`unexpected fetch ${url}`);
    };
    const context = {document,window,fetch,URL,console,setTimeout,clearTimeout,alert(){},confirm(){return true;}};
    context.globalThis = context;
    const sourcePath = path.resolve(__dirname,'..','static','js','api-settings.js');
    const source = fs.readFileSync(sourcePath,'utf8') + `\n globalThis.__primaryTest={loadProviders,setPrimaryProvider,providerPrimaryIssue,providerPrimaryControl,providerCapabilityBadges,providers:()=>providers,selected:()=>selectedId};`;
    vm.runInNewContext(source,context,{filename:sourcePath});
    await context.__primaryTest.loadProviders();
    return {api:context.__primaryTest,calls,messages};
}

(async()=>{
    const rows = [
        {id:'one',name:'One',enabled:true,primary:true,has_key:true,image_models:['img'],chat_models:[],video_models:[]},
        {id:'two',name:'Two',enabled:true,primary:false,has_key:true,image_models:[],chat_models:['chat'],video_models:['video']},
        {id:'off',name:'Off',enabled:false,primary:false,has_key:true,chat_models:['chat']},
        {id:'empty',name:'Empty',enabled:true,primary:false,has_key:false,chat_models:[]}
    ];
    const state = await createState(rows);
    assert.match(state.api.providerPrimaryControl(rows[0]), /默认/);
    assert.match(state.api.providerPrimaryControl(rows[1]), /设为默认/);
    assert.equal(state.api.providerPrimaryIssue(rows[2]), '平台已停用');
    assert.equal(state.api.providerPrimaryIssue(rows[3]), '未配置密钥');
    assert.match(state.api.providerCapabilityBadges(rows[1]), /对话/);
    assert.match(state.api.providerCapabilityBadges(rows[1]), /视频/);

    let stopped = 0;
    const ok = await state.api.setPrimaryProvider({preventDefault(){},stopPropagation(){stopped += 1;}}, 'two');
    assert.equal(ok, true);
    assert.equal(stopped, 1);
    assert.equal(state.calls.filter(call=>call.url === '/api/providers/two/primary').length, 1);
    assert.equal(state.calls.find(call=>call.url.endsWith('/primary')).options.method, 'PUT');
    assert.equal(state.calls.find(call=>call.url.endsWith('/primary')).options.body, undefined);
    assert.equal(state.api.providers().filter(item=>item.primary).map(item=>item.id).join(','), 'two');
    assert.equal(state.api.selected(), 'one', 'switching primary must not select another editor card');
    assert.ok(state.messages.some(message=>message?.type === 'studio-api'));
    console.log('api-settings-primary-provider: passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
```

- [ ] **Step 2: Run the frontend test and verify RED**

Run:

```powershell
node tests/api-settings-primary-provider.test.js
```

Expected: FAIL because the primary UI helpers and action do not exist.

- [ ] **Step 3: Implement provider eligibility, capability badges, and star markup**

Add a module-level pending ID and helpers in `static/js/api-settings.js`:

```javascript
let primaryProviderPendingId = '';

function providerHasPrimaryCredential(item){
    if(item?.id === 'runninghub') return item.has_key === true || item.has_wallet_key === true;
    return item?.has_key === true;
}
function providerPrimaryIssue(item){
    if(!item || item.enabled === false) return '平台已停用';
    if(!providerHasPrimaryCredential(item)) return '未配置密钥';
    if(!['image_models','chat_models','video_models'].some(key => Array.isArray(item[key]) && item[key].length)) return '未配置模型';
    return '';
}
function providerCapabilityBadges(item){
    const values = [['image_models','图片'],['chat_models','对话'],['video_models','视频']];
    return `<span class="provider-capabilities">${values.filter(([key]) => item?.[key]?.length).map(([,label]) => `<span>${label}</span>`).join('')}</span>`;
}
function providerPrimaryControl(item){
    const current = item?.primary === true;
    const reason = current ? '当前默认供应商' : providerPrimaryIssue(item);
    const pending = primaryProviderPendingId === item?.id;
    const disabled = current || Boolean(reason) || Boolean(primaryProviderPendingId);
    return `<button class="provider-primary-btn ${current ? 'is-primary' : ''} ${pending ? 'is-pending' : ''}" type="button" ${disabled ? 'disabled' : ''} title="${escapeAttr(reason || '设为默认')}" aria-label="${escapeAttr(reason || `将 ${item.name || item.id} 设为默认供应商`)}" onclick="setPrimaryProvider(event,'${escapeAttr(item.id)}')"><i data-lucide="${pending ? 'loader-circle' : 'star'}"></i><span>${current ? '默认' : '设为默认'}</span></button>`;
}
```

Do not nest this button inside the existing `.provider-card` button. Wrap each built-in and custom card as:

```html
<div class="provider-card-shell">
    <button class="provider-card ...">...</button>
    ${providerCapabilityBadges(item)}
    ${providerPrimaryControl(item)}
</div>
```

Keep existing card click, drag, active, key-state, logo, and protocol behavior unchanged.

- [ ] **Step 4: Implement the immediate switch action and preserve primary on full saves**

Add:

```javascript
async function setPrimaryProvider(event, providerId){
    event?.preventDefault?.();
    event?.stopPropagation?.();
    const item = providers.find(provider => provider.id === providerId);
    const reason = providerPrimaryIssue(item);
    if(!item || item.primary || reason || primaryProviderPendingId) return false;
    primaryProviderPendingId = providerId;
    renderProviderList();
    try {
        const response = await fetch(`/api/providers/${encodeURIComponent(providerId)}/primary`, {method:'PUT'});
        const data = await response.json();
        if(!response.ok) throw new Error(data.detail || '设置默认供应商失败');
        providers = data.providers || providers;
        renderEditor();
        setStatus(`已将 ${item.name || item.id} 设为默认供应商`);
        broadcastStudioApiChange('providers-changed');
        return true;
    } catch(error) {
        setStatus(error.message || '设置默认供应商失败');
        return false;
    } finally {
        primaryProviderPendingId = '';
        renderProviderList();
    }
}
```

In `saveProviders()` replace:

```javascript
primary:false,
```

with:

```javascript
primary:item.primary === true,
```

At the start of `deleteProvider()`, after obtaining `item`, add:

```javascript
if(item.primary){ alert('请先设置另一个默认供应商'); return; }
```

- [ ] **Step 5: Add focused card styling**

Add styles in `static/css/api-settings.css` for `.provider-card-shell`, `.provider-primary-btn`, `.provider-capabilities`, `.is-primary`, `.is-pending`, disabled state, focus-visible state, and dark/light theme variables. Keep the star control at the top-right without covering provider name, logo, protocol, or drag handle. Use `pointer-events` only on decorative badges; the star remains keyboard-focusable.

- [ ] **Step 6: Run frontend tests and syntax checks**

Run separately:

```powershell
node tests/api-settings-primary-provider.test.js
node tests/api-settings-default-openrouter.test.js
node tests/provider-defaults.test.js
node --check static/js/api-settings.js
```

Expected: all three tests print `passed`; syntax check exits 0.

- [ ] **Step 7: Commit the API Settings control**

```powershell
git add static/js/api-settings.js static/css/api-settings.css tests/api-settings-primary-provider.test.js
git commit -m "feat: add default provider control"
```

---

### Task 4: Integrated regression and live no-cost verification

**Files:**
- Test: `tests/test_primary_provider.py`
- Test: `tests/api-settings-primary-provider.test.js`
- Runtime-only verification: `data/api_providers.json` through the dedicated endpoint; never stage it.

**Interfaces:**
- Consumes: all Task 1–3 backend and frontend behavior.
- Produces: final evidence that the selected primary controls capability-aware defaults across pages without sending secrets or triggering model requests.

- [ ] **Step 1: Run complete automated regression suites**

Run each command separately and stop on the first failure:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_primary_provider -v
.\.venv\Scripts\python.exe -m unittest tests.test_openrouter_image_generation -v
node tests/api-settings-primary-provider.test.js
node tests/api-settings-default-openrouter.test.js
node tests/provider-defaults.test.js
node tests/openrouter-page-defaults.test.js
node tests/online-default-openrouter.test.js
node tests/canvas-provider-defaults.test.js
node tests/smart-canvas-provider-defaults.test.js
node tests/canvas-output-auto-export.test.js
node --check static/js/api-settings.js
node --check static/js/provider-defaults.js
```

Expected: every test exits 0 and every Node test prints `passed`.

- [ ] **Step 2: Verify the dedicated endpoint without transmitting a key**

Choose an already configured eligible non-primary provider from `GET /api/providers`, call the bodyless dedicated endpoint, and verify the response has exactly one primary. Switch back to the user's original primary before continuing. Do not use PowerShell JSON round-tripping; use Node `fetch()` so UTF-8 provider metadata remains intact.

Required assertions:

```text
request method = PUT
request body = absent
response contains no api_key field
exactly one provider has primary=true
the requested provider is that primary
```

- [ ] **Step 3: Verify rejection paths without modifying saved state**

Use test fixtures or temporarily ineligible records only inside automated tests. Do not alter the user's runtime keys. Confirm 404 for an unknown provider and confirm the stored primary ID is unchanged after the request.

- [ ] **Step 4: Verify API Settings UI and downstream defaults**

Reload the local API Settings page and verify:

```text
Every card shows capability badges and a star control.
The current primary shows a filled star and “默认”.
Eligible alternatives show “设为默认”.
Ineligible alternatives are disabled with a reason.
Clicking an eligible star does not change the editor's selected card.
Success updates exactly one card and shows a status message.
Online Image, fresh GPT Chat, new generic Infinite Canvas nodes,
and Smart Canvas use the selected primary when it supports their capability.
```

Do not click Generate, Send, Run, or any other paid action.

- [ ] **Step 5: Verify repository and secret scope**

Run:

```powershell
git diff --check
git status --short
git check-ignore -v -- data/api_providers.json API/.env
git diff --cached --name-only
```

Expected: runtime provider data and secrets are ignored; no key or runtime file is staged; tracked working tree is clean after the planned commits.

- [ ] **Step 6: Final whole-branch review**

Review the complete feature range against `docs/superpowers/specs/2026-07-20-primary-provider-button-design.md`. Fix every Critical or Important finding, rerun Task 4 Steps 1 and 5, and re-review before integration.

---

## Completion Criteria

- The user can immediately choose an eligible primary provider from any provider card.
- The switch request has no body and never transmits a key.
- The backend persists exactly one primary provider atomically.
- Normal API Settings saves preserve the primary flag.
- The current primary cannot be deleted or disabled without an explicit eligible replacement.
- Capability-aware fallback behavior remains unchanged.
- Automated and live no-cost verification pass.
