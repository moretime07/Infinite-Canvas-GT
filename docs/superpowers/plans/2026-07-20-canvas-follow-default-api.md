# Canvas Follow Default API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit follow-default provider mode to Infinite Canvas image, LLM, and video nodes while preserving all existing node selections as fixed overrides.

**Architecture:** Put the provider-mode state transitions and capability-aware resolution in a small UMD/CommonJS helper so behavior can be tested without booting the large canvas UI. `canvas.js` remains responsible for node construction, select rendering, persistence scheduling, and request payloads, but delegates default/fixed resolution to the helper.

**Tech Stack:** Browser JavaScript, CommonJS-compatible UMD helper, native Node.js assertions, existing `ProviderDefaults` utility.

## Global Constraints

- New generic API image, LLM, and video nodes use `providerMode: "default"`.
- A missing `providerMode` always means `fixed`; never migrate existing nodes implicitly.
- `__default__` is UI-only and must never be stored as a provider ID or sent in a request.
- Fixed nodes are never changed by provider refreshes.
- Default nodes use the primary provider when capability-compatible and otherwise use existing `ProviderDefaults.pickProvider` fallback behavior.
- Do not change Smart Canvas, Online Image, GPT Chat, backend provider APIs, runtime provider data, or secret files.
- Do not click Generate, Send, Run, or invoke paid/model endpoints during verification.
- Execute in an isolated `.worktrees/` worktree because the main checkout contains runtime-generated HTML cache-version changes.

---

### Task 1: Pure provider-mode state resolver

**Files:**
- Create: `static/js/canvas-provider-mode.js`
- Create: `tests/canvas-follow-default-provider.test.js`
- Modify: `static/canvas.html`

**Interfaces:**
- Consumes: `ProviderDefaults.pickProvider(providers, options)` from `static/js/provider-defaults.js`.
- Produces: `CanvasProviderMode.DEFAULT_VALUE`, `mode(node)`, `select(node, selectedId)`, and `resolve(node, providers, options)`.

- [ ] **Step 1: Write failing resolver tests**

Create a Node assertion test that imports both helpers and covers explicit mode, legacy behavior, manual selection, default selection, capability fallback, and model preservation:

```js
const assert = require('node:assert/strict');
global.ProviderDefaults = require('../static/js/provider-defaults.js');
const mode = require('../static/js/canvas-provider-mode.js');

const providers = [
  {id:'chat-only', enabled:true, primary:false, chat_models:['chat-a'], image_models:[], video_models:[]},
  {id:'openrouter', enabled:true, primary:true, chat_models:['chat-b'], image_models:['image-b'], video_models:['video-b']},
  {id:'fallback', enabled:true, primary:false, chat_models:['chat-c'], image_models:['image-c'], video_models:['video-c']}
];

assert.equal(mode.mode({}), 'fixed');
assert.equal(mode.mode({providerMode:'default'}), 'default');

const following = {providerMode:'default', apiProvider:'fallback', model:'image-b'};
assert.deepEqual(
  mode.resolve(following, providers, {capability:'image_models', providerField:'apiProvider'}),
  {providerMode:'default', providerId:'openrouter', model:'image-b', changed:true}
);

const fixed = {apiProvider:'fallback', model:'image-c'};
assert.equal(mode.resolve(fixed, providers, {capability:'image_models', providerField:'apiProvider'}).providerId, 'fallback');

assert.deepEqual(mode.select(following, mode.DEFAULT_VALUE), {providerMode:'default', requestedId:''});
assert.deepEqual(mode.select(following, 'fallback'), {providerMode:'fixed', requestedId:'fallback'});
```

Also assert that an incompatible primary falls back to a compatible provider and that an incompatible model becomes the selected provider's first model.

- [ ] **Step 2: Run the test and observe RED**

Run:

```powershell
node tests/canvas-follow-default-provider.test.js
```

Expected: failure because `static/js/canvas-provider-mode.js` does not exist.

- [ ] **Step 3: Implement the minimal pure helper**

Create a UMD/CommonJS module with no DOM dependency:

```js
(function(root, factory){
    const api = factory(root?.ProviderDefaults || (typeof require === 'function' ? require('./provider-defaults.js') : null));
    if(typeof module === 'object' && module.exports) module.exports = api;
    if(root) root.CanvasProviderMode = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function(ProviderDefaults){
    const DEFAULT_VALUE = '__default__';
    const mode = node => node?.providerMode === 'default' ? 'default' : 'fixed';
    function select(node, selectedId){
        return selectedId === DEFAULT_VALUE
            ? {providerMode:'default', requestedId:''}
            : {providerMode:'fixed', requestedId:String(selectedId || '')};
    }
    function resolve(node, providers, options){
        const capability = options.capability;
        const providerField = options.providerField;
        const currentMode = mode(node);
        const requestedId = currentMode === 'default' ? '' : String(node?.[providerField] || '');
        const provider = ProviderDefaults.pickProvider(providers, {
            capability,
            requestedId,
            excludeIds:options.excludeIds || []
        });
        const models = Array.isArray(provider?.[capability]) ? provider[capability].filter(Boolean) : [];
        const model = models.includes(node?.model) ? node.model : (models[0] || '');
        const providerId = provider?.id || '';
        return {
            providerMode:currentMode,
            providerId,
            model,
            changed:providerId !== String(node?.[providerField] || '') || model !== String(node?.model || '')
        };
    }
    return {DEFAULT_VALUE, mode, select, resolve};
});
```

Load it in `static/canvas.html` after `provider-defaults.js` and before `canvas.js`.

- [ ] **Step 4: Run focused GREEN tests and syntax checks**

Run:

```powershell
node tests/canvas-follow-default-provider.test.js
node --check static/js/canvas-provider-mode.js
```

Expected: both exit 0.

- [ ] **Step 5: Commit Task 1**

```powershell
git add static/js/canvas-provider-mode.js static/canvas.html tests/canvas-follow-default-provider.test.js
git commit -m "feat: add canvas provider mode resolver"
```

---

### Task 2: New-node defaults and three provider controls

**Files:**
- Modify: `static/js/canvas.js`
- Modify: `static/js/i18n/canvas.js`
- Modify: `tests/canvas-follow-default-provider.test.js`
- Test: `tests/canvas-provider-defaults.test.js`

**Interfaces:**
- Consumes: Task 1's `CanvasProviderMode` API.
- Produces: `providerMode: "default"` on new generic API nodes and native select options that switch cleanly between default and fixed modes.

- [ ] **Step 1: Add failing constructor and UI contract tests**

Extend the focused test to read `canvas.js` and assert:

```js
assert.match(source, /addGeneratorNode[\s\S]*providerMode\s*:\s*['"]default['"]/);
assert.match(source, /addLLMNode[\s\S]*providerMode\s*:\s*['"]default['"]/);
assert.match(source, /addVideoNode[\s\S]*providerMode\s*:\s*['"]default['"]/);
assert.match(source, /跟随默认 API/);
assert.match(source, /CanvasProviderMode\.DEFAULT_VALUE/);
assert.match(source, /CanvasProviderMode\.select/);
```

Add pure transition assertions proving that selecting `__default__` never puts the sentinel into `apiProvider` or `llmProvider`, while selecting a real ID produces fixed mode.

- [ ] **Step 2: Run the focused test and observe RED**

Run:

```powershell
node tests/canvas-follow-default-provider.test.js
```

Expected: failure because constructors and selects do not yet expose follow-default mode.

- [ ] **Step 3: Implement shared option and node-resolution adapters**

In `canvas.js`, add small adapters around the pure helper:

```js
function canvasProviderMode(node){
    return CanvasProviderMode.mode(node);
}
function followDefaultOption(node, capability, excludeIds=[]){
    const providerId = preferredProviderId(capability, '', excludeIds);
    const provider = apiProviders.find(item => item.id === providerId);
    const selected = canvasProviderMode(node) === 'default' ? 'selected' : '';
    const label = tr('canvas.followDefaultApi') || '跟随默认 API';
    return `<option value="${CanvasProviderMode.DEFAULT_VALUE}" ${selected}>${escapeHtml(`${label}（${provider?.name || providerId || '暂无'}）`)}</option>`;
}
function applyCanvasProviderSelection(node, selectedValue, options){
    const transition = CanvasProviderMode.select(node, selectedValue);
    node.providerMode = transition.providerMode;
    if(transition.providerMode === 'fixed') node[options.providerField] = transition.requestedId;
    return syncCanvasNodeProvider(node, options);
}
```

Use capability-specific `syncCanvasNodeProvider()` calls for image, chat, and video so the stored provider ID and model remain real values.

Add the i18n entry:

```js
"canvas.followDefaultApi": {zh:"跟随默认 API", en:"Follow default API"}
```

- [ ] **Step 4: Mark new nodes as following default**

Add `providerMode:'default'` to `addGeneratorNode`, `addLLMNode`, and `addVideoNode`. Keep the existing resolved `apiProvider`/`llmProvider` and model assignments.

- [ ] **Step 5: Integrate the three native selects**

- Prepend the follow-default option to `providerOptions`, `chatProviderOptions`, and `videoProviderOptions` only when a node is supplied.
- Render each select with `__default__` selected when `providerMode === 'default'`.
- On change, call `applyCanvasProviderSelection()`; a real provider sets `fixed`, while `__default__` sets `default`.
- Rebuild the model options and preserve the existing save/render behavior.
- Keep helper calls used by unrelated code backward-compatible by making the node argument optional.

- [ ] **Step 6: Run focused and existing canvas tests**

Run:

```powershell
node tests/canvas-follow-default-provider.test.js
node tests/canvas-provider-defaults.test.js
node --check static/js/canvas.js
```

Expected: all exit 0.

- [ ] **Step 7: Commit Task 2**

```powershell
git add static/js/canvas.js static/js/i18n/canvas.js tests/canvas-follow-default-provider.test.js tests/canvas-provider-defaults.test.js
git commit -m "feat: add follow default controls to canvas nodes"
```

---

### Task 3: Refresh synchronization, legacy safety, and request invariants

**Files:**
- Modify: `static/js/canvas.js`
- Modify: `tests/canvas-follow-default-provider.test.js`

**Interfaces:**
- Consumes: `syncCanvasNodeProvider(node, options)` from Task 2.
- Produces: `syncFollowingDefaultCanvasNodes()` returning whether any persisted node state changed.

- [ ] **Step 1: Add failing synchronization tests**

Use the pure resolver against a mixed node set and assert:

```js
const mixed = [
  {type:'generator', providerMode:'default', apiProvider:'fallback', model:'image-c'},
  {type:'generator', providerMode:'fixed', apiProvider:'fallback', model:'image-c'},
  {type:'generator', apiProvider:'fallback', model:'image-c'}
];
```

After changing the primary provider, only the first node may change. Add source-level assertions that refresh synchronization filters on explicit `providerMode === 'default'`, schedules save only when state changed, and never writes `CanvasProviderMode.DEFAULT_VALUE` into request payload construction.

- [ ] **Step 2: Run the focused test and observe RED**

Run:

```powershell
node tests/canvas-follow-default-provider.test.js
```

Expected: failure because refresh synchronization is absent.

- [ ] **Step 3: Implement capability-aware refresh synchronization**

Add:

```js
function syncFollowingDefaultCanvasNodes(){
    let changed = false;
    (nodes || []).forEach(node => {
        if(node.providerMode !== 'default') return;
        if(node.type === 'generator') changed = syncCanvasNodeProvider(node, imageProviderModeOptions()) || changed;
        if(node.type === 'llm') changed = syncCanvasNodeProvider(node, chatProviderModeOptions()) || changed;
        if(node.type === 'video') changed = syncCanvasNodeProvider(node, videoProviderModeOptions()) || changed;
    });
    return changed;
}
```

Call it after `loadConfig()` in `refreshCanvasConfigFromSettings()`. Retain existing sanitization for fixed/legacy image and video nodes. Render after refresh, and call `scheduleSave()` only if the sync function returns true and a canvas is open.

- [ ] **Step 4: Harden render and run paths**

Before rendering or running a default-mode node, resolve it through the capability adapter. Ensure all request builders continue to use `node.apiProvider` or `node.llmProvider`, never the UI sentinel. Legacy nodes with no mode remain fixed even if their saved provider equals the current primary.

- [ ] **Step 5: Run focused GREEN and full canvas regression**

Run:

```powershell
node tests/canvas-follow-default-provider.test.js
node tests/canvas-provider-defaults.test.js
node tests/provider-defaults.test.js
node tests/smart-canvas-provider-defaults.test.js
node tests/canvas-output-auto-export.test.js
node --check static/js/canvas.js
node --check static/js/canvas-provider-mode.js
```

Expected: all exit 0.

- [ ] **Step 6: Commit Task 3**

```powershell
git add static/js/canvas.js tests/canvas-follow-default-provider.test.js
git commit -m "fix: sync canvas nodes that follow default api"
```

---

### Task 4: Full regression and no-cost browser acceptance

**Files:**
- Test: all existing provider-default and primary-provider suites.
- Runtime-only verification: local Infinite Canvas UI; never stage canvas data or secret files.

**Interfaces:**
- Consumes: Tasks 1–3 behavior.
- Produces: evidence that default/fixed state works end to end without model calls.

- [ ] **Step 1: Run the complete automated matrix**

Run each command separately:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_primary_provider -v
.\.venv\Scripts\python.exe -m unittest tests.test_openrouter_image_generation -v
node tests/canvas-follow-default-provider.test.js
node tests/api-settings-primary-provider.test.js
node tests/api-settings-default-openrouter.test.js
node tests/provider-defaults.test.js
node tests/openrouter-page-defaults.test.js
node tests/online-default-openrouter.test.js
node tests/canvas-provider-defaults.test.js
node tests/smart-canvas-provider-defaults.test.js
node tests/canvas-output-auto-export.test.js
node --check static/js/canvas.js
node --check static/js/canvas-provider-mode.js
node --check static/js/provider-defaults.js
```

Expected: every command exits 0.

- [ ] **Step 2: Verify repository and secret scope**

Run:

```powershell
git diff --check
git status --short
git check-ignore -v -- data/api_providers.json API/.env
git diff --cached --name-only
```

Expected: no runtime data or secrets staged; worktree clean after commits.

- [ ] **Step 3: Perform no-cost UI acceptance after integration**

Reload the main application, open a disposable classic canvas, and verify without clicking Generate, Send, or Run:

1. A new image node displays `跟随默认 API（OpenRouter）` and an OpenRouter image model.
2. A new LLM node displays the same follow-default label and an OpenRouter chat model.
3. A new video node displays the same follow-default label and an OpenRouter video model.
4. Choose a concrete provider on one node; confirm it becomes fixed.
5. Switch the global primary only if another already-keyed eligible provider exists; confirm default nodes update and the fixed node does not. Restore the original primary.
6. If no eligible alternative exists, do not add or change keys; rely on automated mixed-provider tests for the switching branch and record the limitation.
7. Delete only the disposable verification canvas created for this step.

- [ ] **Step 4: Final whole-branch review**

Review the feature range against `docs/superpowers/specs/2026-07-20-canvas-follow-default-api-design.md`. Fix every Critical or Important issue, rerun Steps 1–2, and re-review before integration.

---

## Completion Criteria

- New generic image, LLM, and video nodes visibly follow the global default API.
- Manual provider selection makes only that node fixed.
- Selecting the follow-default option restores automatic behavior immediately.
- Global provider refreshes update only explicit default-mode nodes.
- Existing nodes remain unchanged unless the user opts them into default mode.
- Request payloads always contain a real provider ID and never `__default__`.
- Full regression, secret-scope checks, and no-cost acceptance pass.
