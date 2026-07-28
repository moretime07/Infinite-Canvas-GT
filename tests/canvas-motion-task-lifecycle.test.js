const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

;(async () => {
const source = fs.readFileSync(path.resolve(__dirname, '..', 'static', 'js', 'canvas.js'), 'utf8');

function productionFunction(name){
    const match = new RegExp(`(?:async )?function ${name}\\(`).exec(source);
    assert.ok(match, `canvas.js should define ${name}`);
    const start = match.index;
    const next = /\n(?:async )?function \w+\(/g;
    next.lastIndex = start + match[0].length;
    const following = next.exec(source);
    return source.slice(start, following ? following.index : source.length);
}

function lifecycleContext({nodes, fetch, saves}={}){
    const context = {
        nodes:nodes || [],
        fetch,
        scheduleSave:() => { saves.count += 1; },
        render:() => {},
        sleep:async ms => { context.delays.push(ms); },
        delays:[],
        tr:key => ({
            'canvas.motionFailed':'Motion task failed',
            'canvas.motionNeedVideo':'Connect one video input',
        }[key] || key),
        encodeURIComponent,
        JSON,
        Math,
        String,
        Number,
        Array,
        Object,
        RegExp,
    };
    vm.createContext(context);
    const names = [
        'motionTaskIsTerminal',
        'motionTaskIsPolling',
        'motionTaskSafeUrl',
        'motionTaskSafeMessage',
        'motionTaskNode',
        'motionTaskPersist',
        'applyCanvasMotionTask',
        'createCanvasMotionTask',
        'pollCanvasMotionTask',
        'cancelCanvasMotionTask',
        'runMotionExtractNode',
        'resumePendingCanvasMotionTasks',
    ];
    vm.runInContext(names.map(productionFunction).concat(names.map(name => `this.${name} = ${name};`)).join('\n'), context);
    return context;
}

function response(ok, payload, status=ok ? 200 : 422){
    return {ok, status, json:async () => payload};
}

function motionNode(overrides={}){
    return {
        id:'motion-1', type:'motionExtract', depthEnabled:true, poseEnabled:true, preserveAudio:true,
        motionTaskId:'', motionState:'idle', motionStage:'', motionProgress:0,
        depthState:'pending', depthUrl:'', poseState:'pending', poseUrl:'', motionWarnings:[], motionError:'',
        ...overrides,
    };
}

// Break caught: adding provider credentials, raw paths, images, or data URLs to the creation request would expose data outside the local-task contract.
{
    const saves = {count:0};
    const node = motionNode({apiProvider:'secret-provider', apiKey:'sk-secret', image:'/private.png'});
    const requests = [];
    const context = lifecycleContext({nodes:[node], saves, fetch:async (url, options) => {
        requests.push({url, options});
        return response(true, {task_id:'task-1', state:'queued', stage:'queued', progress:0, queue_position:2, depth_state:'pending', pose_state:'pending', warnings:[]});
    }});
    await context.createCanvasMotionTask(node, '/assets/input/clip.mp4');
    const body = JSON.parse(requests[0].options.body);
    assert.deepEqual(JSON.parse(JSON.stringify(body)), {
        source_url:'/assets/input/clip.mp4', depth_enabled:true, pose_enabled:true, preserve_audio:true,
    });
    assert.deepEqual(Object.keys(body).sort(), ['depth_enabled', 'pose_enabled', 'preserve_audio', 'source_url']);
    assert.equal(requests[0].url, '/api/canvas-motion-tasks');
}

// Break caught: an unsafe backend validation detail must not be copied into a node, and a failed create must not mutate its upstream source.
{
    const saves = {count:0};
    const node = motionNode({motionSourceMeta:{url:'/assets/input/original.mp4'}});
    const context = lifecycleContext({nodes:[node], saves, fetch:async () => response(false, {detail:'C:\\private\\clip.mp4 sk-secret'}, 422)});
    await assert.rejects(() => context.createCanvasMotionTask(node, '/assets/input/original.mp4'));
    assert.equal(node.motionSourceMeta.url, '/assets/input/original.mp4');
    assert.equal(node.motionError, 'Motion task failed');
    assert.equal(node.motionState, 'failed');
    assert.equal(node.motionTaskId, '');
    assert.equal(node.motionError.includes('private'), false);
}

// Break caught: a lower-progress queued response or an omitted queue position would make the visible task status move backwards or lose its queue placement.
{
    const saves = {count:0};
    const node = motionNode({motionTaskId:'task-1', motionRunToken:1, motionState:'queued', motionProgress:42, motionQueuePosition:3});
    const context = lifecycleContext({nodes:[node], saves, fetch:async () => response(true, {})});
    context.applyCanvasMotionTask(node, {task_id:'task-1', state:'queued', stage:'queued', progress:12, queue_position:2, depth_state:'pending', pose_state:'pending', warnings:[]});
    assert.equal(node.motionProgress, 42);
    assert.equal(node.motionQueuePosition, 2);
    assert.equal(node.motionState, 'queued');
    assert.ok(saves.count > 0);
}

// Break caught: partial results must keep the successful branch usable while retaining the failed branch's error instead of treating both as one outcome.
{
    const saves = {count:0};
    const node = motionNode({motionTaskId:'task-1', motionRunToken:1});
    const context = lifecycleContext({nodes:[node], saves, fetch:async () => response(true, {})});
    context.applyCanvasMotionTask(node, {
        task_id:'task-1', state:'partial', stage:'publishing', progress:100,
        depth_state:'completed', depth_url:'/assets/output/motion/depth.mp4', depth_error:null,
        pose_state:'failed', pose_url:null, pose_error:'No person found', warnings:['Pose is black'],
    });
    assert.equal(node.depthState, 'completed');
    assert.equal(node.depthUrl, '/assets/output/motion/depth.mp4');
    assert.equal(node.poseState, 'failed');
    assert.equal(node.poseUrl, '');
    assert.equal(node.poseError, 'No person found');
    assert.deepEqual(JSON.parse(JSON.stringify(node.motionWarnings)), ['Pose is black']);
}

// Break caught: completed task branches are independently mapped; depth must never overwrite a separate pose output.
{
    const saves = {count:0};
    const node = motionNode({motionTaskId:'task-1', motionRunToken:1});
    const context = lifecycleContext({nodes:[node], saves, fetch:async () => response(true, {})});
    context.applyCanvasMotionTask(node, {
        task_id:'task-1', state:'completed', stage:'completed', progress:100,
        depth_state:'completed', depth_url:'/assets/output/motion/depth.mp4',
        pose_state:'completed', pose_url:'/assets/output/motion/pose.mp4', warnings:[],
    });
    assert.equal(node.depthUrl, '/assets/output/motion/depth.mp4');
    assert.equal(node.poseUrl, '/assets/output/motion/pose.mp4');
    assert.equal(node.motionState, 'completed');
}

// Break caught: cancelling must invalidate an in-flight poll and call the task-specific cancel route.
{
    const saves = {count:0};
    const node = motionNode({motionTaskId:'task-old', motionRunToken:1, motionState:'running'});
    let resolvePoll;
    const requests = [];
    const context = lifecycleContext({nodes:[node], saves, fetch:(url, options={}) => {
        requests.push({url, options});
        if(url.endsWith('/cancel')) return Promise.resolve(response(true, {task_id:'task-old', state:'cancelled', stage:'cancelled', progress:40, depth_state:'cancelled', pose_state:'cancelled', warnings:[]}));
        return new Promise(resolve => { resolvePoll = () => resolve(response(true, {task_id:'task-old', state:'running', stage:'decoding', progress:90, depth_state:'running', pose_state:'pending', warnings:[]})); });
    }});
    const polling = context.pollCanvasMotionTask('motion-1', 'task-old');
    await context.cancelCanvasMotionTask('motion-1');
    resolvePoll();
    await polling;
    assert.equal(requests[1].url, '/api/canvas-motion-tasks/task-old/cancel');
    assert.equal(node.motionState, 'cancelled');
    assert.notEqual(node.motionProgress, 90);
}

// Break caught: reopening a canvas must recover an existing task by GET polling, never submit a duplicate paid/local job.
{
    const saves = {count:0};
    const node = motionNode({motionTaskId:'saved-task', motionRunToken:0, motionState:'running'});
    const requests = [];
    const context = lifecycleContext({nodes:[node], saves, fetch:async (url, options={}) => {
        requests.push({url, options});
        return response(true, {task_id:'saved-task', state:'completed', stage:'completed', progress:100, depth_state:'completed', depth_url:'/assets/output/motion/depth.mp4', pose_state:'disabled', warnings:[]});
    }});
    context.resumePendingCanvasMotionTasks();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, '/api/canvas-motion-tasks/saved-task');
    assert.equal(requests[0].options.method, undefined);
    assert.equal(node.motionState, 'completed');
}

// Break caught: a late response for a pre-retry task must not overwrite the newly created task's terminal state.
{
    const saves = {count:0};
    const node = motionNode({motionTaskId:'old-task', motionRunToken:1, motionState:'running'});
    let resolveOld;
    const context = lifecycleContext({nodes:[node], saves, fetch:(url) => {
        if(url.endsWith('/old-task')) return new Promise(resolve => { resolveOld = () => resolve(response(true, {task_id:'old-task', state:'failed', stage:'failed', progress:100, depth_state:'failed', depth_error:'old failure', pose_state:'failed', warnings:[]})); });
        return Promise.resolve(response(true, {task_id:'new-task', state:'completed', stage:'completed', progress:100, depth_state:'completed', depth_url:'/assets/output/motion/new-depth.mp4', pose_state:'disabled', warnings:[]}));
    }});
    const oldPoll = context.pollCanvasMotionTask('motion-1', 'old-task');
    node.motionTaskId = 'new-task';
    node.motionRunToken = 2;
    await context.pollCanvasMotionTask('motion-1', 'new-task');
    resolveOld();
    await oldPoll;
    assert.equal(node.motionTaskId, 'new-task');
    assert.equal(node.motionState, 'completed');
    assert.equal(node.depthUrl, '/assets/output/motion/new-depth.mp4');
}

// Break caught: terminal responses must stop the polling loop and repeated identical responses must not keep saving the canvas.
{
    const saves = {count:0};
    const node = motionNode({motionTaskId:'task-1', motionRunToken:1, motionState:'running'});
    let calls = 0;
    const completed = {task_id:'task-1', state:'completed', stage:'completed', progress:100, depth_state:'completed', depth_url:'/assets/output/motion/depth.mp4', pose_state:'disabled', warnings:[]};
    const context = lifecycleContext({nodes:[node], saves, fetch:async () => { calls += 1; return response(true, completed); }});
    await context.pollCanvasMotionTask('motion-1', 'task-1');
    assert.equal(calls, 1);
    const afterTerminalSave = saves.count;
    context.applyCanvasMotionTask(node, completed);
    assert.equal(saves.count, afterTerminalSave);
    const persisted = JSON.stringify(node);
    assert.match(persisted, /"motionTaskId":"task-1"/);
    assert.match(persisted, /"depthUrl":"\/assets\/output\/motion\/depth.mp4"/);
    assert.equal(/source_path|C:\\|data:/.test(persisted), false);
}

console.log('canvas-motion-task-lifecycle: passed');
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
