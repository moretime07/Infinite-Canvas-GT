# OpenRouter Global Defaults Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make OpenRouter the capability-aware default provider for every new generic image, chat, and video workflow without overwriting valid saved choices.

**Architecture:** Add one dependency-free browser helper that selects a valid requested provider first, then a compatible primary provider, then OpenRouter, then the first compatible provider. Wire Online Image, GPT Chat, Infinite Canvas, and Smart Canvas to that helper while leaving dedicated provider engines unchanged.

**Tech Stack:** Browser JavaScript, static HTML, Node.js built-in test modules, FastAPI configuration endpoints

## Global Constraints

- Apply defaults only to new state or invalid/missing saved providers.
- Preserve existing conversations, canvases, nodes, and valid saved provider selections.
- Select providers independently for `image_models`, `chat_models`, and `video_models`.
- Keep ModelScope, RunningHub, Volcengine, Jimeng, ComfyUI, and provider-specific asset/avatar workflows explicit and unchanged.
- Never print, copy, commit, or expose API keys.
- Do not make paid generation or chat requests during verification.

---

### Task 1: Shared capability-aware provider selector

**Files:**
- Create: `static/js/provider-defaults.js`
- Create: `tests/provider-defaults.test.js`

**Interfaces:**
- Consumes: provider objects containing `id`, `name`, `base_url`, `enabled`, `primary`, and model-list fields.
- Produces: `ProviderDefaults.pickProvider(providers, options)` and `ProviderDefaults.pickModel(provider, capability, requestedModel)` in browsers; exports the same object through `module.exports` in Node.js.

- [ ] **Step 1: Write the failing unit test**

```javascript
const assert = require('node:assert/strict');
const ProviderDefaults = require('../static/js/provider-defaults.js');

const providers = [
    {id:'modelscope', name:'ModelScope', enabled:true, primary:false, image_models:['ms-image'], chat_models:['ms-chat'], video_models:[]},
    {id:'custom-api', name:'openrouter', base_url:'https://openrouter.ai/api/v1', enabled:true, primary:true, image_models:['or-image'], chat_models:['or-chat'], video_models:['or-video']},
    {id:'lingjing', name:'Lingjing', enabled:true, primary:false, image_models:['lj-image'], chat_models:['lj-chat'], video_models:['lj-video']}
];

assert.equal(ProviderDefaults.pickProvider(providers, {capability:'image_models'}).id, 'custom-api');
assert.equal(ProviderDefaults.pickProvider(providers, {capability:'chat_models'}).id, 'custom-api');
assert.equal(ProviderDefaults.pickProvider(providers, {capability:'video_models'}).id, 'custom-api');
assert.equal(ProviderDefaults.pickProvider(providers, {capability:'chat_models', requestedId:'lingjing'}).id, 'lingjing');
assert.equal(ProviderDefaults.pickModel(providers[1], 'image_models', '').id, 'or-image');

const noOpenRouterVideo = providers.map(item => item.id === 'custom-api' ? {...item, video_models:[]} : item);
assert.equal(ProviderDefaults.pickProvider(noOpenRouterVideo, {capability:'video_models'}).id, 'lingjing');
assert.equal(ProviderDefaults.pickProvider(providers, {capability:'image_models', excludeIds:['custom-api','modelscope']}).id, 'lingjing');
console.log('provider-defaults: passed');
```

- [ ] **Step 2: Run the test and verify it fails because the helper is absent**

Run: `node tests/provider-defaults.test.js`

Expected: FAIL with `Cannot find module '../static/js/provider-defaults.js'`.

- [ ] **Step 3: Implement the shared helper**

```javascript
(function(root, factory){
    const api = factory();
    if(typeof module === 'object' && module.exports) module.exports = api;
    if(root) root.ProviderDefaults = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function(){
    const CAPABILITIES = new Set(['image_models', 'chat_models', 'video_models']);
    function compatibleProviders(providers, capability, excludeIds=[]){
        const excluded = new Set((excludeIds || []).map(String));
        if(!CAPABILITIES.has(capability)) return [];
        return (providers || []).filter(provider => provider && provider.enabled !== false
            && !excluded.has(String(provider.id || ''))
            && Array.isArray(provider[capability]) && provider[capability].length > 0);
    }
    function isOpenRouter(provider){
        const name = String(provider?.name || '').toLowerCase();
        const base = String(provider?.base_url || '').toLowerCase();
        return name.includes('openrouter') || base.includes('openrouter.ai');
    }
    function pickProvider(providers, options={}){
        const capability = options.capability || 'image_models';
        const compatible = compatibleProviders(providers, capability, options.excludeIds || []);
        const requestedId = String(options.requestedId || '');
        return compatible.find(provider => String(provider.id || '') === requestedId)
            || compatible.find(provider => provider.primary === true)
            || compatible.find(isOpenRouter)
            || compatible[0]
            || null;
    }
    function pickModel(provider, capability, requestedModel=''){
        const models = Array.isArray(provider?.[capability]) ? provider[capability].filter(Boolean) : [];
        const requested = String(requestedModel || '');
        const id = models.includes(requested) ? requested : (models[0] || '');
        return {id, models};
    }
    return {compatibleProviders, isOpenRouter, pickProvider, pickModel};
});
```

- [ ] **Step 4: Run the focused test**

Run: `node tests/provider-defaults.test.js`

Expected: `provider-defaults: passed`.

- [ ] **Step 5: Commit the shared selector**

```bash
git add static/js/provider-defaults.js tests/provider-defaults.test.js
git commit -m "feat: add provider default selector"
```

---

### Task 2: Online Image and GPT Chat defaults

**Files:**
- Modify: `static/online.html`
- Modify: `static/gpt-chat.html`
- Create: `tests/openrouter-page-defaults.test.js`

**Interfaces:**
- Consumes: `window.ProviderDefaults.pickProvider()` and `pickModel()` from Task 1.
- Produces: OpenRouter defaults for a fresh Online Image or GPT Chat state; valid `gpt_chat_settings_v1` providers remain selected.

- [ ] **Step 1: Write the failing page wiring test**

```javascript
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const read = name => fs.readFileSync(path.resolve(__dirname, '..', 'static', name), 'utf8');
const online = read('online.html');
const chat = read('gpt-chat.html');

for(const [name, source] of [['online.html', online], ['gpt-chat.html', chat]]){
    assert.match(source, /\/static\/js\/provider-defaults\.js/, `${name} should load provider defaults`);
}
assert.match(online, /ProviderDefaults\.pickProvider\([^)]*capability:\s*['"]image_models['"]/, 'Online Image should select by image capability');
assert.match(chat, /hasSavedChatProvider/, 'GPT Chat should distinguish saved chat state');
assert.match(chat, /hasSavedImageProvider/, 'GPT Chat should distinguish saved image state');
assert.match(chat, /capability:\s*['"]chat_models['"]/, 'GPT Chat should select a chat-capable default');
assert.match(chat, /capability:\s*['"]image_models['"]/, 'GPT Chat should select an image-capable default');
console.log('openrouter-page-defaults: passed');
```

- [ ] **Step 2: Run the test and verify missing shared-helper wiring**

Run: `node tests/openrouter-page-defaults.test.js`

Expected: FAIL because the pages do not load or call `provider-defaults.js`.

- [ ] **Step 3: Wire Online Image to the shared selector**

Load `/static/js/provider-defaults.js` before the inline page script. Initialize `provider` to an empty string, then after `/api/config` loads assign:

```javascript
provider = ProviderDefaults.pickProvider(apiProviders, {
    capability:'image_models',
    requestedId:provider
})?.id || provider || 'comfly';
selectedModel = ProviderDefaults.pickModel(providerById(provider), 'image_models', selectedModel).id || models.gpt;
```

- [ ] **Step 4: Wire fresh GPT Chat state while preserving saved settings**

Load `/static/js/provider-defaults.js` before the inline chat script. Add explicit saved-state flags:

```javascript
const hasSavedChatProvider = typeof savedChatSettings.provider === 'string' && savedChatSettings.provider.trim() !== '';
const hasSavedImageProvider = typeof savedChatSettings.activeImageProvider === 'string' && savedChatSettings.activeImageProvider.trim() !== '';
```

After `/api/config` loads, choose providers with `requestedId` only when the corresponding flag is true:

```javascript
provider = ProviderDefaults.pickProvider(apiProviders, {
    capability:'chat_models',
    requestedId:hasSavedChatProvider ? provider : ''
})?.id || provider;
activeImageProvider = ProviderDefaults.pickProvider(apiProviders, {
    capability:'image_models',
    requestedId:hasSavedImageProvider ? activeImageProvider : ''
})?.id || activeImageProvider;
```

Then run the existing `validateSavedProviderState()` and model validation.

- [ ] **Step 5: Run focused and existing page tests**

Run these commands separately:

```bash
node tests/provider-defaults.test.js
node tests/openrouter-page-defaults.test.js
node tests/online-default-openrouter.test.js
```

Expected: all three tests print `passed` and exit with code 0.

- [ ] **Step 6: Commit page defaults**

```bash
git add static/online.html static/gpt-chat.html tests/openrouter-page-defaults.test.js
git commit -m "feat: default new pages to OpenRouter"
```

---

### Task 3: Infinite Canvas new-node defaults

**Files:**
- Modify: `static/canvas.html`
- Modify: `static/js/canvas.js`
- Create: `tests/canvas-provider-defaults.test.js`

**Interfaces:**
- Consumes: the Task 1 provider selector and loaded `apiProviders`.
- Produces: `preferredProviderId(capability, requestedId, excludeIds)` for canvas code; new generator, LLM, and video nodes select OpenRouter when compatible.

- [ ] **Step 1: Write the failing canvas contract test**

```javascript
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(path.resolve(__dirname, '..', 'static', 'canvas.html'), 'utf8');
const source = fs.readFileSync(path.resolve(__dirname, '..', 'static', 'js', 'canvas.js'), 'utf8');
assert.match(html, /provider-defaults\.js[^]*canvas\.js/, 'Canvas should load provider defaults before canvas.js');
assert.match(source, /function\s+preferredProviderId\s*\(/, 'Canvas should expose one preferred-provider adapter');
assert.match(source, /addGeneratorNode[^]*preferredProviderId\(['"]image_models['"]/, 'New image nodes should use the image default');
assert.match(source, /addLLMNode[^]*preferredProviderId\(['"]chat_models['"]/, 'New LLM nodes should use the chat default');
assert.match(source, /addVideoNode[^]*preferredProviderId\(['"]video_models['"]/, 'New video nodes should use the video default');
console.log('canvas-provider-defaults: passed');
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `node tests/canvas-provider-defaults.test.js`

Expected: FAIL because `preferredProviderId` and helper wiring are absent.

- [ ] **Step 3: Add the canvas adapter and preserve existing valid IDs**

Load `provider-defaults.js` before `canvas.js`, then add:

```javascript
function preferredProviderId(capability, requestedId='', excludeIds=[]){
    return ProviderDefaults.pickProvider(
        apiProviders.length ? apiProviders : defaultApiProviders(),
        {capability, requestedId, excludeIds}
    )?.id || '';
}
```

Update `resolveImageProviderId`, `resolveChatProviderId`, and `resolveVideoProviderId` so a valid requested ID wins and a missing/invalid ID uses `preferredProviderId` with the matching capability.

- [ ] **Step 4: Use the adapter for new generic nodes only**

Use these exact defaults:

```javascript
const providerId = preferredProviderId('image_models', '', ['modelscope']);
const providerId = preferredProviderId('chat_models');
const providerId = preferredProviderId('video_models', '', ['modelscope']);
```

Apply them in `addGeneratorNode`, `addLLMNode`, and `addVideoNode`, respectively. Do not change `addMsGenNode`, `addRhNode`, ComfyUI nodes, or loaded nodes with valid provider IDs.

- [ ] **Step 5: Run canvas and shared regression tests**

Run these commands separately:

```bash
node tests/provider-defaults.test.js
node tests/canvas-provider-defaults.test.js
node tests/canvas-output-auto-export.test.js
```

Expected: all tests print `passed` and exit with code 0.

- [ ] **Step 6: Commit Infinite Canvas defaults**

```bash
git add static/canvas.html static/js/canvas.js tests/canvas-provider-defaults.test.js
git commit -m "feat: default new canvas nodes to OpenRouter"
```

---

### Task 4: Smart Canvas generic defaults

**Files:**
- Modify: `static/smart-canvas.html`
- Modify: `static/js/smart-canvas.js`
- Create: `tests/smart-canvas-provider-defaults.test.js`

**Interfaces:**
- Consumes: the Task 1 selector, Smart Canvas `apiProviders`, and saved `settings`.
- Produces: capability-aware defaults for missing `settings.provider_id`, chat provider, and video provider while retaining valid saved values.

- [ ] **Step 1: Write the failing Smart Canvas contract test**

```javascript
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(path.resolve(__dirname, '..', 'static', 'smart-canvas.html'), 'utf8');
const source = fs.readFileSync(path.resolve(__dirname, '..', 'static', 'js', 'smart-canvas.js'), 'utf8');
assert.match(html, /provider-defaults\.js[^]*smart-canvas\.js/, 'Smart Canvas should load provider defaults first');
assert.match(source, /function\s+preferredSmartProviderId\s*\(/, 'Smart Canvas should use one provider adapter');
assert.match(source, /preferredSmartProviderId\(['"]image_models['"]/, 'Smart Canvas should default generic image API');
assert.match(source, /preferredSmartProviderId\(['"]chat_models['"]/, 'Smart Canvas should default chat API');
assert.match(source, /preferredSmartProviderId\(['"]video_models['"]/, 'Smart Canvas should default video API');
console.log('smart-canvas-provider-defaults: passed');
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `node tests/smart-canvas-provider-defaults.test.js`

Expected: FAIL because Smart Canvas does not load or call the shared selector.

- [ ] **Step 3: Add the Smart Canvas adapter**

Load `provider-defaults.js` before `smart-canvas.js`, then add:

```javascript
function preferredSmartProviderId(capability, requestedId='', excludeIds=[]){
    return ProviderDefaults.pickProvider(apiProviders || [], {capability, requestedId, excludeIds})?.id || '';
}
```

- [ ] **Step 4: Apply capability-aware defaults without overriding saved settings**

Use `preferredSmartProviderId('image_models', settings.provider_id, ['modelscope','volcengine'])` when normalizing generic API image settings. Use the chat capability in `resolveChatProviderId`. Use `preferredSmartProviderId('video_models', settings.videoProvider, ['modelscope'])` when a video provider is missing or invalid. Do not change `settings.engine` when it is `modelscope`, `runninghub`, `volcengine`, or `comfy`.

- [ ] **Step 5: Run Smart Canvas and shared tests**

Run these commands separately:

```bash
node tests/provider-defaults.test.js
node tests/smart-canvas-provider-defaults.test.js
```

Expected: both tests print `passed` and exit with code 0.

- [ ] **Step 6: Commit Smart Canvas defaults**

```bash
git add static/smart-canvas.html static/js/smart-canvas.js tests/smart-canvas-provider-defaults.test.js
git commit -m "feat: default Smart Canvas APIs to OpenRouter"
```

---

### Task 5: Mark OpenRouter primary and verify the integrated behavior

**Files:**
- Runtime-only update: `data/api_providers.json` through `PUT /api/providers` (Git-ignored; never stage)
- Test: all tests created in Tasks 1-4 plus existing regressions

**Interfaces:**
- Consumes: `GET /api/providers` public provider records and the existing key-preserving `PUT /api/providers` endpoint.
- Produces: exactly one enabled provider with `primary === true`, the configured OpenRouter provider; its secret remains in `API/.env` and is omitted from the request body.

- [ ] **Step 1: Save OpenRouter as the sole primary provider without transmitting a key**

Read `GET /api/providers`, set `primary = true` only on the provider whose base URL contains `openrouter.ai`, set all other `primary` fields to false, remove response-only fields (`has_key`, previews, and env names), and send the public provider records to `PUT /api/providers`. Do not add an `api_key` field.

- [ ] **Step 2: Run the complete JavaScript regression set**

Run each command separately and stop on the first failure:

```bash
node tests/provider-defaults.test.js
node tests/openrouter-page-defaults.test.js
node tests/online-default-openrouter.test.js
node tests/canvas-provider-defaults.test.js
node tests/smart-canvas-provider-defaults.test.js
node tests/api-settings-default-openrouter.test.js
node tests/canvas-output-auto-export.test.js
```

Expected: seven `passed` messages and zero failures.

- [ ] **Step 3: Verify live configuration without paid requests**

Read `/api/config` and assert:

```text
OpenRouter exists, enabled=true, has_key=true, primary=true,
image_models count > 0, chat_models count > 0, video_models count > 0.
```

Confirm `git check-ignore -v -- data/api_providers.json` succeeds.

- [ ] **Step 4: Verify fresh UI state and new nodes**

Reload the local app in a clean browser storage context or clear only the page-specific new-state keys used for the test. Verify:

```text
Online Image provider = openrouter
GPT Chat fresh chat provider = openrouter
GPT Chat fresh image provider = openrouter
New Infinite Canvas generator provider = openrouter
New Infinite Canvas LLM provider = openrouter
New Infinite Canvas video provider = openrouter
New Smart Canvas generic image/chat/video providers = openrouter
```

Do not click Generate or Send.

- [ ] **Step 5: Verify repository scope and commit any remaining tracked wiring changes**

Run: `git diff --check; git status --short`

Expected: runtime `data/` files are absent from status; only planned tracked files are present before their corresponding commit, and the final tracked working tree is clean.
