# Middle-button Canvas Pan and RunningHub Input Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make canvas panning respond only to a held middle mouse button, while ensuring unused RunningHub image inputs are explicitly disabled when the app schema supports an empty sentinel.

**Architecture:** Keep both fixes inside the existing canvas controller. Add small, testable mouse helpers for button-state decisions. Reuse RunningHub field-option parsing to discover the provider-declared `None` sentinel; required media remains validated locally, connected media is uploaded normally, optional unused media is sent as `None`, and unsupported empty fields remain omitted.

**Tech Stack:** Vanilla JavaScript, Node.js `node:test`/`assert`/`vm`, existing Python `unittest` regression suite.

## Global Constraints

- Preserve unrelated dirty-worktree changes.
- Do not use template filenames as fallback media values.
- Do not run a paid RunningHub generation task during verification.
- Do not log, commit, or expose API keys.
- Keep wheel zoom, node dragging, minimap interaction, touch handling, and selection-box shortcuts unchanged.

---

## Task 1: Lock canvas panning to the held middle mouse button

**Files:**

- Create: `tests/canvas-middle-button-pan.test.js`
- Modify: `static/js/canvas.js` near `startBoardPan` and `board.onmousedown`

- [ ] **Step 1: Add a failing regression test**

Create a Node test that loads the relevant production helpers or handler source and verifies:

```js
assert.equal(isMiddleMouseButton({button: 1}), true);
assert.equal(isMiddleMouseButton({button: 0}), false);
assert.equal(isMiddleMouseHeld({buttons: 4}), true);
assert.equal(isMiddleMouseHeld({buttons: 1}), false);
assert.doesNotMatch(boardMouseDownSource, /startBoardPan\(e,\s*\{clearSelectionOnClick:true\}\)/);
assert.match(boardMouseDownSource, /if\s*\(isMiddleMouseButton\(e\)\)/);
```

Also assert that the pan `mousemove` path terminates the drag if the middle-button bit is absent.

- [ ] **Step 2: Run the test and confirm the intended failure**

Run:

```powershell
node --test tests/canvas-middle-button-pan.test.js
```

Expected: FAIL because the helper functions do not yet exist and left blank-space drag still invokes `startBoardPan`.

- [ ] **Step 3: Implement the minimal interaction change**

Add pure helpers:

```js
function isMiddleMouseButton(event){
    return Number(event?.button) === 1;
}

function isMiddleMouseHeld(event){
    return (Number(event?.buttons || 0) & 4) === 4;
}
```

Update `startBoardPan`:

```js
window.onmousemove = e2 => {
    if(!isMiddleMouseHeld(e2)){
        endDrag(e2);
        return;
    }
    // existing movement calculation
};
```

Update `board.onmousedown` so:

- middle button starts board pan;
- left button continues to support knife/selection shortcuts;
- a plain left click on blank canvas clears selection;
- a plain left drag on blank canvas never changes the viewport.

- [ ] **Step 4: Run the focused test**

Run:

```powershell
node --test tests/canvas-middle-button-pan.test.js
```

Expected: PASS.

- [ ] **Step 5: Commit the focused change**

```powershell
git add static/js/canvas.js tests/canvas-middle-button-pan.test.js
git commit -m "fix: require middle button for canvas pan"
```

---

## Task 2: Isolate unused RunningHub image slots from saved template assets

**Files:**

- Modify: `tests/runninghub-optional-media.test.js`
- Modify: `static/js/canvas.js` near `rhExtractFieldOptions` and `rhBuildNodeInfoList`

- [ ] **Step 1: Change the regression expectation first**

Update the existing optional-media regression so the provider schema:

```js
fieldData: JSON.stringify([['example.png', 'None'], {image_upload: true}])
```

produces:

```js
[
  {nodeId: '1', fieldName: 'image', fieldValue: 'canvas-image.png'},
  {nodeId: '2', fieldName: 'image', fieldValue: 'None'},
  {nodeId: '3', fieldName: 'image', fieldValue: 'None'},
  {nodeId: '5', fieldName: 'prompt', fieldValue: expectedPrompt},
]
```

Add a second case proving an optional media field without a provider-declared empty sentinel stays omitted.

- [ ] **Step 2: Run the test and confirm the intended failure**

Run:

```powershell
node --test tests/runninghub-optional-media.test.js
```

Expected: FAIL because the current implementation omits optional image fields and therefore allows RunningHub to reuse saved web-app assets.

- [ ] **Step 3: Implement provider-supported empty-media mapping**

Add a helper after `rhExtractFieldOptions`:

```js
function rhEmptyMediaValue(field){
    const options = rhExtractFieldOptions(field) || [];
    const emptyOption = options.find(value => String(value).trim().toLowerCase() === 'none');
    return emptyOption == null ? '' : String(emptyOption);
}
```

In the app-mode media branch of `rhBuildNodeInfoList`:

```js
if(!upstream.length && !hasManualValue){
    if(rhFieldIsRequired(field)){
        throw new Error(/* existing required-input message */);
    }
    const emptyValue = rhEmptyMediaValue(field);
    if(emptyValue){
        result.push({
            nodeId: field.nodeId,
            fieldName: field.fieldName,
            fieldValue: emptyValue,
        });
    }
    continue;
}
```

This must occur before upload handling so the `None` sentinel is never treated as a local file.

- [ ] **Step 4: Run focused RunningHub tests**

Run:

```powershell
node --test tests/runninghub-optional-media.test.js tests/runninghub-nodeinfo-validation.test.js tests/runninghub-workflow-input-pruning.test.js
```

Expected: PASS with required media still rejected locally, connected media still uploaded, and workflow input pruning unchanged.

- [ ] **Step 5: Commit the focused change**

```powershell
git add static/js/canvas.js tests/runninghub-optional-media.test.js
git commit -m "fix: isolate unused RunningHub media slots"
```

---

## Task 3: Full regression verification and application restart

**Files:**

- Verify all modified files only; no paid API calls.

- [ ] **Step 1: Run the complete JavaScript suite**

```powershell
node --test tests/*.test.js
```

Expected: all tests pass.

- [ ] **Step 2: Run the complete Python suite**

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
```

Expected: all tests pass.

- [ ] **Step 3: Check patch hygiene and secret scope**

```powershell
git diff --check
git status --short
git diff --name-only HEAD
```

Expected: no whitespace errors; only intended source, tests, and documentation are staged or committed by this work. Do not stage provider data or environment files.

- [ ] **Step 4: Restart the local application**

Stop only the process listening on the project’s local port, then launch:

```powershell
.\.venv\Scripts\python.exe main.py
```

Expected: `http://127.0.0.1:3000/` loads successfully.

- [ ] **Step 5: Perform read-only browser smoke checks**

Verify:

- left drag on blank canvas does not move the viewport;
- middle drag moves the viewport only while held;
- left blank click clears selection;
- a RunningHub node with one connected image serializes unused supported image slots as `None`;
- no paid generation is submitted.
