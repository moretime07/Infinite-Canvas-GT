# Infinite Canvas Permissive Connections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let any two distinct canvas nodes connect through their rendered output/input ports, preserve those connections across reloads, and warn before a one-click run traverses a feedback cycle.

**Architecture:** Separate structural connection validation from workflow execution validation. A single pair of node-capability helpers drives both port rendering and link creation, while connection cleanup removes only invalid graph records. A dedicated cycle analyzer inspects the runnable dependency graph before a cascade; a confirmation modal allows one deterministic pass or cancels the run.

**Tech Stack:** Vanilla JavaScript, HTML, CSS, Node.js `assert`/`vm` tests, existing Infinite Canvas cascade runtime.

## Global Constraints

- Preserve all existing uncommitted user changes; stage and commit only files owned by the current task.
- Do not read, print, stage, or commit API keys, `.env` files, generated media, canvas data, or other private content.
- Allow feedback cycles between distinct nodes, including `LLM → 视频生成 → LLM`.
- Never allow a node to connect to itself.
- Do not automatically repeat a feedback cycle; a confirmed cyclic cascade executes each runnable node at most once.
- Do not change the execution order or user interaction of existing acyclic workflows.
- Keep named output identity (`fromPort`) when preserving and deduplicating saved connections.
- Use test-first development: each production change follows a test that was observed failing for the expected reason.

---

## File Structure

- Modify `static/js/canvas.js`: own node connection capabilities, structural cleanup, workflow cycle analysis, confirmation state, and cascade guard.
- Modify `static/canvas.html`: add one reusable feedback-cycle confirmation modal.
- Modify `static/css/canvas.css`: style the feedback-cycle modal using existing error-modal visual tokens.
- Replace/expand `tests/canvas-generator-llm-connection.test.js`: verify permissive structural links, saved-link cleanup, and LLM media collection.
- Create `tests/canvas-workflow-cycle-guard.test.js`: verify cycle paths, one-pass ordering, user confirmation, and acyclic bypass.

---

### Task 1: Centralize structural connection capabilities

**Files:**

- Modify: `static/js/canvas.js:6668-6704`
- Modify: `static/js/canvas.js:14881-14909`
- Modify: `tests/canvas-generator-llm-connection.test.js`
- Test: `tests/canvas-motion-ports.test.js`

**Interfaces:**

- Produces: `nodeCanReceive(node: object): boolean`
- Produces: `nodeCanEmit(node: object): boolean`
- Produces: `canConnect(fromId: string, toId: string): boolean`
- Produces: `sanitizeConnections(): void`
- Consumes: existing `nodeOutputPorts(node)` and `connectionKey(fromId, toId, fromPort)`

- [ ] **Step 1: Write failing permissive-link and cleanup tests**

Expand `tests/canvas-generator-llm-connection.test.js` with a graph containing all current canvas node types:

```js
const allNodes = [
    {id:'image', type:'image', url:'/assets/input.png'},
    {id:'prompt', type:'prompt', text:'prompt'},
    {id:'loop', type:'loop', imageInput:true, videoInput:true, showPrompt:true},
    {id:'group', type:'group', items:[]},
    {id:'prompt-group', type:'promptGroup', items:[]},
    {id:'generator', type:'generator', generatedOutputs:[{url:imageUrl, kind:'image'}]},
    {id:'msgen', type:'msgen', generatedOutputs:[{url:imageUrl, kind:'image'}]},
    {id:'comfy', type:'comfy', generatedOutputs:[{url:imageUrl, kind:'image'}]},
    {id:'ltx', type:'ltxDirector', generatedOutputs:[{url:videoUrl, kind:'video'}]},
    {id:'video', type:'video', generatedOutputs:[{url:videoUrl, kind:'video'}]},
    {id:'rh', type:'rh', generatedOutputs:[{url:videoUrl, kind:'video'}]},
    {id:'motion', type:'motionExtract', depthEnabled:true, poseEnabled:true},
    {id:'output', type:'output', images:[{url:videoUrl, kind:'video'}]},
    {id:'llm', type:'llm', outputText:'analysis'},
];
```

Assert these behaviors:

```js
assert.equal(controls.canConnect('llm', 'video'), true);
context.connections.push({id:'llm-video', from:'llm', to:'video'});
assert.equal(controls.canConnect('video', 'llm'), true);

for (const sourceId of ['generator','msgen','comfy','ltx','video','rh','motion','output']) {
    assert.equal(controls.canConnect(sourceId, 'llm'), true, `${sourceId} should connect to LLM`);
}

assert.equal(controls.canConnect('video', 'video'), false);
```

Seed cleanup with one feedback edge, one orphan, one self-link, one exact duplicate, and two named motion links:

```js
context.connections = [
    {id:'forward', from:'llm', to:'video'},
    {id:'feedback', from:'video', to:'llm'},
    {id:'feedback-copy', from:'video', to:'llm'},
    {id:'depth', from:'motion', to:'llm', fromPort:'depth'},
    {id:'pose', from:'motion', to:'llm', fromPort:'pose'},
    {id:'self', from:'llm', to:'llm'},
    {id:'orphan', from:'missing', to:'llm'},
];
controls.sanitizeConnections();
assert.deepEqual(context.connections.map(link => link.id), ['forward','feedback','depth','pose']);
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
node tests\canvas-generator-llm-connection.test.js
```

Expected: FAIL because `video → llm` is rejected when `llm → video` already exists, and/or cleanup retains duplicate records.

- [ ] **Step 3: Implement the minimal capability helpers**

In `static/js/canvas.js`, define the actual structural port contracts:

```js
function nodeCanReceive(node){
    if(!node) return false;
    if(node.type === 'loop') return Boolean(node.imageInput || node.videoInput || node.showPrompt);
    return ['generator','comfy','ltxDirector','output','llm','msgen','video','rh','motionExtract'].includes(node.type);
}

function nodeCanEmit(node){
    if(!node) return false;
    if(nodeOutputPorts(node).length) return true;
    return ['image','prompt','loop','group','promptGroup','generator','comfy','ltxDirector','llm','msgen','video','rh','output'].includes(node.type);
}
```

Use those helpers in `renderNode()`:

```js
const canInput = nodeCanReceive(node);
const canOutput = nodeCanEmit(node);
```

Replace the type/cycle matrix in `canConnect()`:

```js
function canConnect(fromId, toId){
    if(!fromId || !toId || fromId === toId) return false;
    const from = nodes.find(node => node.id === fromId);
    const to = nodes.find(node => node.id === toId);
    return Boolean(from && to && nodeCanEmit(from) && nodeCanReceive(to));
}
```

Make cleanup preserve feedback and named-port links while removing malformed records:

```js
function sanitizeConnections(){
    const seen = new Set();
    connections = (connections || []).filter(connection => {
        if(!canConnect(connection?.from, connection?.to)) return false;
        const key = connectionKey(connection.from, connection.to, normalizedFromPort(connection));
        if(seen.has(key)) return false;
        seen.add(key);
        return true;
    });
}
```

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```powershell
node tests\canvas-generator-llm-connection.test.js
node tests\canvas-motion-ports.test.js
```

Expected: both tests print their success messages and exit with code `0`.

- [ ] **Step 5: Commit the structural connection change**

```powershell
git add -- static/js/canvas.js tests/canvas-generator-llm-connection.test.js
git commit -m "fix(canvas): allow permissive structural connections"
```

Do not stage unrelated pre-existing modifications in either file; review `git diff --cached` before committing.

---

### Task 2: Detect feedback cycles without blocking links

**Files:**

- Modify: `static/js/canvas.js:12190-12320`
- Create: `tests/canvas-workflow-cycle-guard.test.js`

**Interfaces:**

- Consumes: `canvasWorkflowEdges(): Array<[string, string]>`
- Produces: `findWorkflowCycle(anchorId: string): string[]`
- Produces: `workflowCycleLabels(cycleIds: string[]): string[]`
- Preserves: `computeCascadeOrder(targetId: string): string[]` returns unique runnable node IDs

- [ ] **Step 1: Write failing cycle-analysis tests**

Create `tests/canvas-workflow-cycle-guard.test.js` with the existing `productionFunction()` extractor and a VM context containing:

```js
const nodes = [
    {id:'llm', type:'llm'},
    {id:'video', type:'video', model:'seedance-2.0'},
    {id:'output', type:'output'},
];
const connections = [
    {id:'forward', from:'llm', to:'video'},
    {id:'feedback', from:'video', to:'llm'},
    {id:'video-output', from:'video', to:'output'},
];
```

Assert:

```js
assert.deepEqual(findWorkflowCycle('video'), ['llm','video','llm']);
assert.deepEqual(workflowCycleLabels(['llm','video','llm']), ['LLM','视频生成','LLM']);
const order = computeCascadeOrder('video');
assert.equal(new Set(order).size, order.length);
assert.deepEqual(new Set(order), new Set(['llm','video']));
```

Add an acyclic fixture:

```js
connections.splice(1, 1);
assert.deepEqual(findWorkflowCycle('video'), []);
```

- [ ] **Step 2: Run the cycle test and verify RED**

Run:

```powershell
node tests\canvas-workflow-cycle-guard.test.js
```

Expected: FAIL because `findWorkflowCycle` and `workflowCycleLabels` do not exist.

- [ ] **Step 3: Implement deterministic cycle discovery**

Add a three-color depth-first search over the runnable edges relevant to the anchor:

```js
function findWorkflowCycle(anchorId){
    const runTypes = new Set(canvasRunTypes());
    const edges = canvasWorkflowEdges();
    const relevant = new Set([anchorId]);
    let changed = true;
    while(changed){
        changed = false;
        edges.forEach(([from, to]) => {
            if(relevant.has(to) && !relevant.has(from)){
                relevant.add(from);
                changed = true;
            }
        });
    }
    const adjacency = new Map();
    relevant.forEach(id => adjacency.set(id, []));
    edges.forEach(([from, to]) => {
        if(relevant.has(from) && relevant.has(to)) adjacency.get(from)?.push(to);
    });
    const state = new Map();
    const stack = [];
    let found = [];
    const visit = id => {
        state.set(id, 1);
        stack.push(id);
        for(const next of adjacency.get(id) || []){
            if(!runTypes.has(nodes.find(node => node.id === next)?.type)) continue;
            if(state.get(next) === 1){
                found = [...stack.slice(stack.indexOf(next)), next];
                return true;
            }
            if(!state.get(next) && visit(next)) return true;
        }
        stack.pop();
        state.set(id, 2);
        return false;
    };
    for(const node of nodes){
        if(relevant.has(node.id) && !state.get(node.id) && visit(node.id)) break;
    }
    return found;
}
```

Add stable display labels:

```js
function workflowCycleLabels(cycleIds){
    return (cycleIds || []).map(id => {
        const node = nodes.find(candidate => candidate.id === id);
        if(!node) return id;
        if(node.type === 'llm') return 'LLM';
        if(node.type === 'video') return tr('canvas.videoGenerateNode');
        if(node.type === 'generator') return tr('canvas.apiGenerate');
        if(node.type === 'msgen') return tr('canvas.modelscopeGenerate');
        if(node.type === 'rh') return 'RunningHub';
        if(node.type === 'comfy') return 'ComfyUI';
        if(node.type === 'motionExtract') return tr('canvas.motionExtract');
        return node.type;
    });
}
```

Keep `computeCascadeOrder()` using its visited set so a confirmed feedback graph emits every runnable node at most once.

- [ ] **Step 4: Run the cycle test and verify GREEN**

Run:

```powershell
node tests\canvas-workflow-cycle-guard.test.js
```

Expected: PASS and no recursion/stack overflow.

- [ ] **Step 5: Commit cycle analysis**

```powershell
git add -- static/js/canvas.js tests/canvas-workflow-cycle-guard.test.js
git commit -m "feat(canvas): detect workflow feedback cycles"
```

Review the staged diff first and exclude unrelated hunks.

---

### Task 3: Guard cyclic one-click runs with an explicit choice

**Files:**

- Modify: `static/canvas.html:312-326`
- Modify: `static/css/canvas.css:946-958`
- Modify: `static/js/canvas.js:250-260`
- Modify: `static/js/canvas.js:12180-12540`
- Modify: `tests/canvas-workflow-cycle-guard.test.js`

**Interfaces:**

- Consumes: `findWorkflowCycle(anchorId): string[]`
- Consumes: `workflowCycleLabels(cycleIds): string[]`
- Produces: `confirmWorkflowCycleRun(cycleIds: string[]): Promise<boolean>`
- Produces: `confirmWorkflowCycleBeforeRun(anchorId: string): Promise<boolean>`
- Produces: `closeWorkflowCycleModal(allowRun: boolean): void`

- [ ] **Step 1: Write failing confirmation-gate tests**

Extend `tests/canvas-workflow-cycle-guard.test.js` with:

```js
(async () => {
    let confirmCalls = 0;
    context.confirmWorkflowCycleRun = async cycle => {
        confirmCalls += 1;
        assert.deepEqual(cycle, ['llm','video','llm']);
        return false;
    };
    assert.equal(await confirmWorkflowCycleBeforeRun('video'), false);
    assert.equal(confirmCalls, 1);

    connections.splice(1, 1);
    assert.equal(await confirmWorkflowCycleBeforeRun('video'), true);
    assert.equal(confirmCalls, 1);
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
```

Also assert the one-click run and button paths use the guard:

```js
assert.match(productionFunction('runNodeCascade'), /await confirmWorkflowCycleBeforeRun\(nodeId\)/);
assert.match(productionFunction('cascadeBtnHtml'), /findWorkflowCycle\(node\.id\)/);
```

- [ ] **Step 2: Run the cycle test and verify RED**

Run:

```powershell
node tests\canvas-workflow-cycle-guard.test.js
```

Expected: FAIL because the confirmation functions and modal do not exist.

- [ ] **Step 3: Add the reusable confirmation modal**

Add this structure after `#errorModal` in `static/canvas.html`:

```html
<div id="workflowCycleModal" class="error-modal" onclick="closeWorkflowCycleModal(false)">
    <div class="error-panel workflow-cycle-panel" onclick="event.stopPropagation()">
        <div class="error-head">
            <div class="error-title">检测到反馈回路</div>
            <button class="preview-icon-btn" type="button" onclick="closeWorkflowCycleModal(false)">
                <i data-lucide="x" class="w-4 h-4"></i>
            </button>
        </div>
        <div class="workflow-cycle-copy">本工作流包含循环依赖，不能自动无限循环。本次可以让每个节点仅运行一次。</div>
        <div id="workflowCyclePath" class="error-message workflow-cycle-path"></div>
        <div class="error-actions">
            <button class="error-btn" type="button" onclick="closeWorkflowCycleModal(false)">取消运行</button>
            <button class="error-btn primary" type="button" onclick="closeWorkflowCycleModal(true)">仅运行一轮</button>
        </div>
    </div>
</div>
```

Add focused CSS without introducing new global color tokens:

```css
.workflow-cycle-panel { width:min(620px, 100%); }
.workflow-cycle-copy { color:var(--muted); font-size:12px; line-height:1.55; }
.workflow-cycle-path { min-height:64px; color:#f59e0b; font-weight:850; }
```

- [ ] **Step 4: Implement modal state and the pre-run guard**

Cache modal elements near the existing error-modal constants:

```js
const workflowCycleModal = document.getElementById('workflowCycleModal');
const workflowCyclePath = document.getElementById('workflowCyclePath');
let workflowCycleResolver = null;
```

Implement:

```js
function closeWorkflowCycleModal(allowRun=false){
    workflowCycleModal?.classList.remove('open');
    const resolve = workflowCycleResolver;
    workflowCycleResolver = null;
    if(resolve) resolve(Boolean(allowRun));
}

function confirmWorkflowCycleRun(cycleIds){
    if(!workflowCycleModal || !workflowCyclePath){
        return Promise.resolve(window.confirm(`检测到反馈回路：\n${workflowCycleLabels(cycleIds).join(' → ')}\n\n仅运行一轮？`));
    }
    closeWorkflowCycleModal(false);
    workflowCyclePath.textContent = workflowCycleLabels(cycleIds).join(' → ');
    workflowCycleModal.classList.add('open');
    refreshIcons();
    return new Promise(resolve => { workflowCycleResolver = resolve; });
}

async function confirmWorkflowCycleBeforeRun(anchorId){
    const cycle = findWorkflowCycle(anchorId);
    if(!cycle.length) return true;
    return confirmWorkflowCycleRun(cycle);
}
```

At the beginning of `runNodeCascade(nodeId)` after validating the target:

```js
if(!await confirmWorkflowCycleBeforeRun(nodeId)) return;
```

Make the one-click button available when a node's upstream workflow contains a cycle even if the graph has no terminal generator:

```js
function cascadeBtnHtml(node){
    const cycle = findWorkflowCycle(node.id);
    if(!isTerminalGenerator(node.id) && !cycle.length) return '';
    // retain the existing order, loop, active, and button rendering logic
}
```

- [ ] **Step 5: Run focused tests and verify GREEN**

Run:

```powershell
node tests\canvas-workflow-cycle-guard.test.js
node tests\canvas-generator-llm-connection.test.js
node tests\canvas-motion-ports.test.js
```

Expected: all tests pass.

- [ ] **Step 6: Commit the guarded-run UI**

```powershell
git add -- static/canvas.html static/css/canvas.css static/js/canvas.js tests/canvas-workflow-cycle-guard.test.js
git commit -m "feat(canvas): guard cyclic one-click runs"
```

Review `git diff --cached` and unstage unrelated pre-existing hunks before committing.

---

### Task 4: Verify data flow, persistence, and browser behavior

**Files:**

- Modify only if a regression test exposes a missing behavior:
  - `static/js/canvas.js`
  - `tests/canvas-generator-llm-connection.test.js`
  - `tests/canvas-workflow-cycle-guard.test.js`

**Interfaces:**

- Verifies: `llmInputText(node)`, `llmInputImages(node)`, and `llmInputVideos(node)` consume all supported upstream results.
- Verifies: opening, saving, and reloading a canvas preserves feedback links.
- Verifies: confirmed cyclic cascades run each runnable node at most once.

- [ ] **Step 1: Run all canvas Node.js tests**

Run:

```powershell
Get-ChildItem tests -Filter 'canvas-*.test.js' | Sort-Object Name | ForEach-Object {
    Write-Host "== $($_.Name) =="
    node $_.FullName
    if($LASTEXITCODE -ne 0){ throw "Failed: $($_.Name)" }
}
```

Expected: every test exits with code `0`.

- [ ] **Step 2: Run repository Python regression tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: all collected tests pass. If unrelated environment-dependent tests are skipped, record the exact skip output; do not mask failures.

- [ ] **Step 3: Start or reuse the local server and verify browser behavior**

Open a test canvas at `http://127.0.0.1:3000/` and perform this exact sequence:

1. Create one LLM node and one video-generation node.
2. Connect `LLM → 视频生成`.
3. Connect `视频生成 → LLM`.
4. Save and reload the page; verify both lines remain.
5. Click one-click run; verify the feedback warning displays `LLM → 视频生成 → LLM`.
6. Click “取消运行”; verify no node enters queued/running state.
7. Click one-click run again and choose “仅运行一轮”; verify each node enters running at most once and the run terminates.
8. Open an acyclic canvas and verify one-click run starts without the warning.

- [ ] **Step 4: Inspect persistence without exposing private data**

Use the canvas API response only to count matching connection records; do not print prompts, media URLs, provider configuration, or API keys. Confirm:

```text
LLM → video: 1
video → LLM: 1
self links: 0
exact duplicates: 0
```

- [ ] **Step 5: Run final diff and secret checks**

Run:

```powershell
git diff --check
git status --short
git diff --stat
```

Inspect only the task-owned staged diff and confirm it contains no `.env`, key files, canvas data, media, logs, or unrelated user work.

- [ ] **Step 6: Commit any test-driven compatibility correction**

Only if Steps 1-4 required an additional production correction:

```powershell
git add -- static/js/canvas.js tests/canvas-generator-llm-connection.test.js tests/canvas-workflow-cycle-guard.test.js
git commit -m "fix(canvas): preserve permissive workflow data flow"
```

If no correction was needed, do not create an empty commit.
