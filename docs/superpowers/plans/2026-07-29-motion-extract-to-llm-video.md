# Motion Extract to LLM Video Input Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow DEPTH and POSE outputs from a motion extraction node to connect to an LLM and reach `/api/canvas-llm` as ordered, deduplicated video inputs.

**Architecture:** Keep the existing connection record as the source of truth, including its `fromPort`. Extend connection validation to accept `motionExtract -> llm`, then teach `llmInputVideos(node)` to resolve the selected motion branch through the existing `motionOutputVideoRefs(node, fromPort)` adapter. The existing LLM request and badge already consume `llmInputVideos`, so they inherit the corrected behavior without a second media path.

**Tech Stack:** Browser JavaScript, Node.js `assert`/`vm` regression tests, existing `/api/canvas-llm` JSON protocol.

## Global Constraints

- DEPTH and POSE may both connect to the same LLM.
- Send every valid connected branch in connection order.
- Send motion results through `videos`, never through `images`.
- Do not guess a branch for legacy connections without `fromPort`.
- Skip disabled, incomplete, failed, or URL-less branches.
- Deduplicate repeated URLs while retaining the first occurrence.
- Preserve all existing image, ordinary video, Output, group, and loop input behavior.
- Do not modify or stage the user's unrelated working-tree changes.

---

### Task 1: Accept named motion-output connections to LLM

**Files:**
- Modify: `static/js/canvas.js` (`canConnect`)
- Create: `tests/canvas-motion-llm-video.test.js`

**Interfaces:**
- Consumes: `canConnect(fromId: string, toId: string): boolean`, global `nodes`, `CANVAS_GENERATOR_TYPES`, and `CANVAS_MEDIA_OUTPUT_TYPES`.
- Produces: `motionExtract -> llm` returns `true`; all existing connection rules remain unchanged.

- [ ] **Step 1: Write the failing connection test**

Create the test harness and assert the new connection rule:

```js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '..', 'static', 'js', 'canvas.js'), 'utf8');

function productionFunction(name){
    const asyncStart = source.indexOf(`async function ${name}(`);
    const syncStart = source.indexOf(`function ${name}(`);
    const start = asyncStart === -1 ? syncStart : asyncStart;
    assert.notEqual(start, -1, `canvas.js should define ${name}`);
    const next = source.slice(start + 1).search(/\n(?:async )?function /);
    return source.slice(start, next === -1 ? source.length : start + 1 + next);
}

const motion = {id:'motion-1', type:'motionExtract'};
const llm = {id:'llm-1', type:'llm'};
const connectionContext = {
    nodes:[motion, llm],
    CANVAS_GENERATOR_TYPES:[],
    CANVAS_MEDIA_OUTPUT_TYPES:[],
    wouldCreateGeneratorCycle:() => false,
};
vm.createContext(connectionContext);
vm.runInContext(`${productionFunction('canConnect')}\nthis.canConnect = canConnect;`, connectionContext);

assert.equal(connectionContext.canConnect(motion.id, llm.id), true);
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
node tests/canvas-motion-llm-video.test.js
```

Expected: FAIL because `canConnect('motion-1', 'llm-1')` currently returns `false`.

- [ ] **Step 3: Implement the minimal connection rule**

Change the motion-source rule in `canConnect`:

```js
if(from.type === 'motionExtract'){
    return to.type === 'llm' || to.type === 'output' || CANVAS_GENERATOR_TYPES.includes(to.type);
}
```

- [ ] **Step 4: Run the focused test**

Run:

```powershell
node tests/canvas-motion-llm-video.test.js
```

Expected: PASS for the connection assertion.

---

### Task 2: Resolve DEPTH and POSE as ordered LLM video inputs

**Files:**
- Modify: `static/js/canvas.js` (`llmInputVideos`)
- Modify: `tests/canvas-motion-llm-video.test.js`

**Interfaces:**
- Consumes: `normalizedFromPort(connection): string` and `motionOutputVideoRefs(node, portName): Array<{url: string, name: string, kind: "video"}>`.
- Produces: `llmInputVideos(node): string[]`, ordered by inbound `connections`, with duplicate URLs removed.

- [ ] **Step 1: Add failing branch, order, deduplication, and invalid-state tests**

Append a media test context:

```js
const depthUrl = '/assets/motion/depth.mp4';
const poseUrl = '/assets/motion/pose.mp4';
const motionReady = {
    id:'motion-ready',
    type:'motionExtract',
    depthEnabled:true,
    depthState:'completed',
    depthUrl,
    poseEnabled:true,
    poseState:'completed',
    poseUrl,
};
const motionPending = {
    id:'motion-pending',
    type:'motionExtract',
    depthEnabled:true,
    depthState:'processing',
    depthUrl:'',
    poseEnabled:false,
    poseState:'disabled',
    poseUrl:'',
};
const targetLLM = {id:'llm-target', type:'llm'};
const mediaContext = {
    nodes:[motionReady, motionPending, targetLLM],
    connections:[
        {from:motionReady.id, to:targetLLM.id, fromPort:'pose'},
        {from:motionReady.id, to:targetLLM.id, fromPort:'depth'},
        {from:motionReady.id, to:targetLLM.id, fromPort:'depth'},
        {from:motionPending.id, to:targetLLM.id, fromPort:'depth'},
        {from:motionPending.id, to:targetLLM.id, fromPort:'pose'},
        {from:motionReady.id, to:targetLLM.id},
    ],
    tr:key => key,
    mediaKindForNode:() => 'video',
    isVideoUrl:url => String(url).endsWith('.mp4'),
    outputUrlValue:item => typeof item === 'string' ? item : item?.url || '',
};
vm.createContext(mediaContext);
vm.runInContext([
    productionFunction('motionVideoRefMetadata'),
    productionFunction('motionOutputVideoRefs'),
    productionFunction('normalizedFromPort'),
    productionFunction('llmInputVideos'),
    'this.llmInputVideos = llmInputVideos;',
].join('\n'), mediaContext);

assert.deepEqual(
    Array.from(mediaContext.llmInputVideos(targetLLM)),
    [poseUrl, depthUrl]
);
```

Also reset `connections` to verify each branch independently:

```js
mediaContext.connections = [{from:motionReady.id, to:targetLLM.id, fromPort:'depth'}];
assert.deepEqual(Array.from(mediaContext.llmInputVideos(targetLLM)), [depthUrl]);

mediaContext.connections = [{from:motionReady.id, to:targetLLM.id, fromPort:'pose'}];
assert.deepEqual(Array.from(mediaContext.llmInputVideos(targetLLM)), [poseUrl]);
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
node tests/canvas-motion-llm-video.test.js
```

Expected: FAIL because the existing `llmInputVideos` ignores `motionExtract` and does not preserve `fromPort`.

- [ ] **Step 3: Implement ordered motion-video collection**

Replace the body of `llmInputVideos` with a connection-aware collector:

```js
function llmInputVideos(node){
    const urls = [];
    const seen = new Set();
    const addUrl = url => {
        if(!url || seen.has(url)) return;
        seen.add(url);
        urls.push(url);
    };
    connections.filter(c => c.to === node.id).forEach(connection => {
        const n = nodes.find(candidate => candidate.id === connection.from);
        if(!n) return;
        if(n.type === 'motionExtract'){
            motionOutputVideoRefs(n, normalizedFromPort(connection)).forEach(ref => addUrl(ref?.url));
            return;
        }
        if(n.type === 'image' && n.url && mediaKindForNode(n) === 'video') addUrl(n.url);
        if(n.type === 'output' && (n.images || []).length){
            const last = [...n.images].reverse().map(outputUrlValue).find(url => url && isVideoUrl(url));
            addUrl(last);
        }
        if(n.type === 'group'){
            (n.items || [])
                .map(id => nodes.find(candidate => candidate.id === id))
                .filter(item => item?.type === 'image' && item?.url && mediaKindForNode(item) === 'video')
                .forEach(video => addUrl(video.url));
        }
    });
    return urls;
}
```

- [ ] **Step 4: Run focused motion-video tests**

Run:

```powershell
node tests/canvas-motion-llm-video.test.js
node tests/canvas-motion-ports.test.js
node tests/canvas-motion-node.test.js
```

Expected: all three commands PASS.

---

### Task 3: Verify the real LLM request payload and regression suite

**Files:**
- Modify: `tests/canvas-motion-llm-video.test.js`
- Verify: `static/js/canvas.js` (`renderLLMBody`, `callCanvasLLM`)

**Interfaces:**
- Consumes: `callCanvasLLM(node, message, messages, options): Promise<string>`.
- Produces: `/api/canvas-llm` JSON body containing motion results in `videos` and no motion URLs in `images`.

- [ ] **Step 1: Add a failing request-payload test**

Add the request dependencies to `mediaContext`, evaluate `callCanvasLLM`, and capture the JSON request:

```js
mediaContext.connections = [
    {from:motionReady.id, to:targetLLM.id, fromPort:'depth'},
    {from:motionReady.id, to:targetLLM.id, fromPort:'pose'},
];
mediaContext.llmInputImages = () => [];
mediaContext.syncDefaultCanvasNodeProvider = () => {};
mediaContext.unresolvedDefaultCanvasNodeError = () => '';
mediaContext.resolveCanvasNodeRequest = () => ({providerId:'openrouter', model:'vision-model'});
mediaContext.chatProviderModeOptions = () => ({});
mediaContext.resolveChatModel = value => value;
mediaContext.responseErrorMessage = async () => 'LLM failed';
mediaContext.capturedRequests = [];
mediaContext.cascadeFetch = async (url, options={}) => {
    mediaContext.capturedRequests.push({url, body:JSON.parse(options.body)});
    return {ok:true, json:async () => ({text:'motion recognized'})};
};
targetLLM.llmProvider = 'openrouter';
targetLLM.model = 'vision-model';
targetLLM.systemPrompt = 'Analyze the motion references.';

vm.runInContext(
    `${productionFunction('callCanvasLLM')}\nthis.callCanvasLLM = callCanvasLLM;`,
    mediaContext
);

(async () => {
    const text = await mediaContext.callCanvasLLM(targetLLM, 'Describe the movement');
    assert.equal(text, 'motion recognized');
    assert.equal(mediaContext.capturedRequests[0].url, '/api/canvas-llm');
    assert.deepEqual(
        Array.from(mediaContext.capturedRequests[0].body.videos),
        [depthUrl, poseUrl]
    );
    assert.deepEqual(Array.from(mediaContext.capturedRequests[0].body.images), []);
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
```

- [ ] **Step 2: Run the payload test**

Run:

```powershell
node tests/canvas-motion-llm-video.test.js
```

Expected: PASS after Task 2 because `callCanvasLLM` already reads `llmInputVideos`; if it fails, correct only the request wiring exposed by the assertion.

- [ ] **Step 3: Verify the LLM media badge uses the corrected collector**

Run:

```powershell
rg -n "const videos = llmInputVideos\\(node\\)" static/js/canvas.js
```

Expected: matches both `renderLLMBody` and `callCanvasLLM`, proving the UI badge and request share the same collector.

- [ ] **Step 4: Run all JavaScript regression tests**

Run:

```powershell
$tests = Get-ChildItem tests -Filter '*.test.js' | Sort-Object Name
foreach($test in $tests){
    node $test.FullName
    if($LASTEXITCODE -ne 0){ throw "Failed: $($test.Name)" }
}
```

Expected: every JavaScript test exits with code `0`.

- [ ] **Step 5: Inspect the scoped diff**

Run:

```powershell
git diff --check
git diff -- static/js/canvas.js tests/canvas-motion-llm-video.test.js
git status --short
```

Expected: no whitespace errors; only the intended `canvas.js` hunks and new test belong to this implementation. Existing unrelated working-tree changes remain untouched.

- [ ] **Step 6: Commit only the implementation files**

Because `static/js/canvas.js` contains an unrelated user change, stage only the intended hunks interactively:

```powershell
git add -p -- static/js/canvas.js
git add -- tests/canvas-motion-llm-video.test.js
git diff --cached --check
git diff --cached
git commit -m "feat: connect motion videos to LLM nodes"
```

Expected: the commit contains the connection rule, LLM video collector, and regression test only.
