const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const source = fs.readFileSync(path.resolve(__dirname, '..', 'static', 'js', 'canvas.js'), 'utf8');

function productionFunction(name){
    const asyncStart = source.indexOf(`async function ${name}(`);
    const start = asyncStart === -1 ? source.indexOf(`function ${name}(`) : asyncStart;
    assert.notEqual(start, -1, `canvas.js should define ${name}`);
    const nextSync = source.indexOf('\nfunction ', start + 1);
    const nextAsync = source.indexOf('\nasync function ', start + 1);
    const next = [nextSync, nextAsync].filter(index => index !== -1).sort((a, b) => a - b)[0] ?? -1;
    return source.slice(start, next === -1 ? source.length : next);
}

const runVideoNode = productionFunction('runVideoNode');
assert.match(runVideoNode, /createCanvasVideoTask\(/, 'video generation should start a recoverable backend task');
assert.match(runVideoNode, /waitCanvasVideoTaskResult\(/, 'video generation should poll the recoverable backend task');
assert.match(runVideoNode, /pending\.paused\s*=\s*true/, 'stopping the cascade should preserve and pause the pending video');
assert.match(runVideoNode, /scheduleSave\(\)/, 'video pending state changes should be persisted');

const resumeTasks = productionFunction('resumeCanvasVideoTasks');
assert.match(resumeTasks, /canvasTaskType\s*===\s*['"]video['"]/, 'saved video tasks should be recognized after reopening a canvas');
assert.match(resumeTasks, /!p\.paused/, 'explicitly paused video tasks should not resume without the user');

const resumeRunningHubTasks = productionFunction('resumeRunningHubTasks');
assert.match(resumeRunningHubTasks, /run\?\.nodeType\s*===\s*['"]rh['"]/, 'saved RunningHub tasks should be recognized after reopening a canvas');
assert.match(resumeRunningHubTasks, /pollRunningHubPending\(/, 'saved RunningHub tasks should resume polling after reopening a canvas');

const pollRunningHubPending = productionFunction('pollRunningHubPending');
assert.match(pollRunningHubPending, /\/api\/runninghub\/query\?taskId=/, 'RunningHub recovery should query the existing task instead of submitting a new paid task');
assert.match(pollRunningHubPending, /data\.status\s*===\s*['"]FAILED['"]/, 'RunningHub recovery should stop when the upstream task has failed');
assert.match(pollRunningHubPending, /_pending\s*=\s*\(out\._pending\s*\|\|\s*\[\]\)\.filter/, 'terminal RunningHub tasks should be removed from the pending output list');

const openCanvas = productionFunction('openCanvas');
assert.match(openCanvas, /resumeRunningHubTasks\(\)/, 'opening a canvas should resume saved RunningHub task polling');

const renderPending = productionFunction('renderPendingOutput');
assert.match(renderPending, /pending\?\.paused/, 'paused video tasks should have a distinct output state');
assert.match(renderPending, /继续等待|Resume waiting/, 'paused video tasks should offer a resume action');

console.log('canvas-video-task-lifecycle: passed');
