const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

;(async () => {
const source = fs.readFileSync(path.resolve(__dirname, '..', 'static', 'js', 'canvas.js'), 'utf8');
const TASK_ONE = 'canvas_motion_11111111111111111111111111111111';
const TASK_OLD = 'canvas_motion_22222222222222222222222222222222';
const TASK_SAVED = 'canvas_motion_33333333333333333333333333333333';
const TASK_NEW = 'canvas_motion_44444444444444444444444444444444';

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
        syncConnectedOutputsFromMotion:() => 0,
        sleep:async ms => { context.delays.push(ms); },
        delays:[],
        tr:key => ({
            'canvas.motionFailed':'Motion task failed',
            'canvas.motionNeedVideo':'Connect one video input',
            'canvas.motionRuntimeUnavailable':'Install the local motion runtime, then restart the app.',
        }[key] || key),
        encodeURIComponent,
        JSON,
        Math,
        String,
        Number,
        Array,
        Object,
        RegExp,
        URL,
    };
    vm.createContext(context);
    const names = [
        'motionTaskIdIsSafe',
        'motionTaskIsTerminal',
        'motionTaskIsPolling',
        'motionTaskStateIsKnown',
        'motionTaskSafeState',
        'motionTaskSafeStage',
        'motionTaskSafeBranchState',
        'motionTaskCanTransition',
        'motionTaskSafeUrl',
        'motionTaskSafeMessage',
        'motionTaskResponseError',
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
        return response(true, {task_id:TASK_ONE, state:'queued', stage:'queued', progress:0, queue_position:2, depth_state:'pending', pose_state:'pending', warnings:[]});
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

// Break caught: a native prerequisite failure during preflight must use only the structured runtime-unavailable guidance.
{
    const saves = {count:0};
    const node = motionNode();
    const context = lifecycleContext({nodes:[node], saves, fetch:async () => response(false, {
        detail:{
            message:String.raw`ffprobe missing at C:\private\bin`,
            error_code:'runtime_unavailable',
        },
    }, 503)});
    await assert.rejects(() => context.createCanvasMotionTask(node, '/assets/input/original.mp4'));
    assert.equal(node.motionError, 'Install the local motion runtime, then restart the app.');
    assert.equal(JSON.stringify(node).includes('private'), false);
}

// Break caught: a lower-progress queued response or an omitted queue position would make the visible task status move backwards or lose its queue placement.
{
    const saves = {count:0};
    const node = motionNode({motionTaskId:TASK_ONE, motionRunToken:1, motionState:'queued', motionProgress:42, motionQueuePosition:3});
    const context = lifecycleContext({nodes:[node], saves, fetch:async () => response(true, {})});
    context.applyCanvasMotionTask(node, {task_id:TASK_ONE, state:'queued', stage:'queued', progress:12, queue_position:2, depth_state:'pending', pose_state:'pending', warnings:[]});
    assert.equal(node.motionProgress, 42);
    assert.equal(node.motionQueuePosition, 2);
    assert.equal(node.motionState, 'queued');
    assert.ok(saves.count > 0);
}

// Break caught: partial results must keep the successful branch usable while retaining the failed branch's error instead of treating both as one outcome.
{
    const saves = {count:0};
    const node = motionNode({motionTaskId:TASK_ONE, motionRunToken:1});
    const context = lifecycleContext({nodes:[node], saves, fetch:async () => response(true, {})});
    context.applyCanvasMotionTask(node, {
        task_id:TASK_ONE, state:'partial', stage:'publishing', progress:100,
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
    const node = motionNode({motionTaskId:TASK_ONE, motionRunToken:1});
    const context = lifecycleContext({nodes:[node], saves, fetch:async () => response(true, {})});
    context.applyCanvasMotionTask(node, {
        task_id:TASK_ONE, state:'completed', stage:'completed', progress:100,
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
    const node = motionNode({motionTaskId:TASK_OLD, motionRunToken:1, motionState:'running'});
    let resolvePoll;
    const requests = [];
    const context = lifecycleContext({nodes:[node], saves, fetch:(url, options={}) => {
        requests.push({url, options});
        if(url.endsWith('/cancel')) return Promise.resolve(response(true, {task_id:TASK_OLD, state:'cancelled', stage:'cancelled', progress:40, depth_state:'cancelled', pose_state:'cancelled', warnings:[]}));
        return new Promise(resolve => { resolvePoll = () => resolve(response(true, {task_id:TASK_OLD, state:'running', stage:'decoding', progress:90, depth_state:'running', pose_state:'pending', warnings:[]})); });
    }});
    const polling = context.pollCanvasMotionTask('motion-1', TASK_OLD);
    await context.cancelCanvasMotionTask('motion-1');
    resolvePoll();
    await polling;
    assert.equal(requests[1].url, `/api/canvas-motion-tasks/${TASK_OLD}/cancel`);
    assert.equal(node.motionState, 'cancelled');
    assert.notEqual(node.motionProgress, 90);
}

// Break caught: reopening a canvas must recover an existing task by GET polling, never submit a duplicate paid/local job.
{
    const saves = {count:0};
    const node = motionNode({motionTaskId:TASK_SAVED, motionRunToken:0, motionState:'running'});
    const requests = [];
    const context = lifecycleContext({nodes:[node], saves, fetch:async (url, options={}) => {
        requests.push({url, options});
        return response(true, {task_id:TASK_SAVED, state:'completed', stage:'completed', progress:100, depth_state:'completed', depth_url:'/assets/output/motion/depth.mp4', pose_state:'disabled', warnings:[]});
    }});
    context.resumePendingCanvasMotionTasks();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, `/api/canvas-motion-tasks/${TASK_SAVED}`);
    assert.equal(requests[0].options.method, undefined);
    assert.equal(node.motionState, 'completed');
}

// Break caught: a late response for a pre-retry task must not overwrite the newly created task's terminal state.
{
    const saves = {count:0};
    const node = motionNode({motionTaskId:TASK_OLD, motionRunToken:1, motionState:'running'});
    let resolveOld;
    const context = lifecycleContext({nodes:[node], saves, fetch:(url) => {
        if(url.endsWith(`/${TASK_OLD}`)) return new Promise(resolve => { resolveOld = () => resolve(response(true, {task_id:TASK_OLD, state:'failed', stage:'failed', progress:100, depth_state:'failed', depth_error:'old failure', pose_state:'failed', warnings:[]})); });
        return Promise.resolve(response(true, {task_id:TASK_NEW, state:'completed', stage:'completed', progress:100, depth_state:'completed', depth_url:'/assets/output/motion/new-depth.mp4', pose_state:'disabled', warnings:[]}));
    }});
    const oldPoll = context.pollCanvasMotionTask('motion-1', TASK_OLD);
    node.motionTaskId = TASK_NEW;
    node.motionRunToken = 2;
    await context.pollCanvasMotionTask('motion-1', TASK_NEW);
    resolveOld();
    await oldPoll;
    assert.equal(node.motionTaskId, TASK_NEW);
    assert.equal(node.motionState, 'completed');
    assert.equal(node.depthUrl, '/assets/output/motion/new-depth.mp4');
}

// Break caught: terminal responses must stop the polling loop and repeated identical responses must not keep saving the canvas.
{
    const saves = {count:0};
    const node = motionNode({motionTaskId:TASK_ONE, motionRunToken:1, motionState:'running'});
    let calls = 0;
    const completed = {task_id:TASK_ONE, state:'completed', stage:'completed', progress:100, depth_state:'completed', depth_url:'/assets/output/motion/depth.mp4', pose_state:'disabled', warnings:[]};
    const context = lifecycleContext({nodes:[node], saves, fetch:async () => { calls += 1; return response(true, completed); }});
    await context.pollCanvasMotionTask('motion-1', TASK_ONE);
    assert.equal(calls, 1);
    const afterTerminalSave = saves.count;
    context.applyCanvasMotionTask(node, completed);
    assert.equal(saves.count, afterTerminalSave);
    const persisted = JSON.stringify(node);
    assert.match(persisted, new RegExp(`"motionTaskId":"${TASK_ONE}"`));
    assert.match(persisted, /"depthUrl":"\/assets\/output\/motion\/depth.mp4"/);
    assert.equal(/source_path|C:\\|data:/.test(persisted), false);
}

// Break caught: a malicious create response cannot turn an arbitrary server string into a persisted task ID or raw diagnostic.
{
    const saves = {count:0};
    const node = motionNode({motionRunToken:1});
    const badTaskId = 'C:\\private\\canvas_motion_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
    const context = lifecycleContext({nodes:[node], saves, fetch:async () => response(true, {
        task_id:badTaskId, state:'running', stage:'C:\\private\\stage', progress:10,
        depth_state:'running', pose_state:'pending', warnings:['sk-live-unsafe'],
    })});
    await assert.rejects(() => context.createCanvasMotionTask(node, '/assets/input/clip.mp4'));
    assert.equal(node.motionTaskId, '');
    assert.equal(node.motionState, 'failed');
    assert.equal(node.motionError, 'Motion task failed');
    assert.equal(JSON.stringify(node).includes('private'), false);
    assert.equal(JSON.stringify(node).includes('sk-live'), false);
}

// Break caught: mismatched IDs and unknown or oversized task metadata must never overwrite a saved run or persist raw backend text.
{
    const saves = {count:0};
    const node = motionNode({motionTaskId:TASK_ONE, motionRunToken:1, motionState:'running', motionStage:'decoding'});
    const context = lifecycleContext({nodes:[node], saves, fetch:async () => response(true, {})});
    assert.equal(context.applyCanvasMotionTask(node, {
        task_id:TASK_NEW, state:'completed', stage:'completed', progress:100,
        depth_state:'completed', depth_url:'/assets/output/motion/wrong.mp4', pose_state:'disabled', warnings:[],
    }), false);
    assert.equal(node.motionTaskId, TASK_ONE);
    assert.equal(node.motionState, 'running');
    assert.equal(context.applyCanvasMotionTask(node, {
        task_id:TASK_ONE, state:'running', stage:'C:\\private\\stage', progress:20,
        depth_state:'running', pose_state:'pending', warnings:[],
    }), true);
    assert.equal(node.motionStage, 'decoding');
    const rawWarning = 'x'.repeat(1000);
    context.applyCanvasMotionTask(node, {
        task_id:TASK_ONE, state:'not-a-state', stage:'C:\\private\\stage', progress:99,
        depth_state:'not-a-branch', depth_error:'C:\\private\\depth.mp4',
        pose_state:'not-a-branch', pose_error:rawWarning,
        warnings:['sk-live-secret', rawWarning], error:'token=unsafe',
    });
    assert.equal(node.motionState, 'failed');
    assert.equal(node.motionStage, 'failed');
    assert.equal(node.depthState, 'failed');
    assert.equal(node.poseState, 'failed');
    assert.equal(node.depthError, 'Motion task failed');
    assert.equal(node.poseError, 'Motion task failed');
    assert.deepEqual(JSON.parse(JSON.stringify(node.motionWarnings)), ['Motion task failed', 'Motion task failed']);
    assert.equal(node.motionError, 'Motion task failed');
    const persisted = JSON.stringify(node);
    assert.equal(/private|secret|token=|x{300}/.test(persisted), false);
}

// Break caught: forward polling snapshots may skip stages, but delayed lower-rank snapshots cannot move a task backward or resurrect it after a terminal result.
{
    const saves = {count:0};
    const node = motionNode({motionTaskId:TASK_ONE, motionRunToken:1, motionState:'queued', motionStage:'queued'});
    let calls = 0;
    const context = lifecycleContext({nodes:[node], saves, fetch:async () => { calls += 1; return response(true, {}); }});
    assert.equal(context.applyCanvasMotionTask(node, {task_id:TASK_ONE, state:'running', stage:'decoding', progress:10, depth_state:'running', pose_state:'pending', warnings:[]}), true);
    assert.equal(node.motionState, 'running');
    assert.equal(context.applyCanvasMotionTask(node, {task_id:TASK_ONE, state:'queued', stage:'queued', progress:1, depth_state:'pending', pose_state:'pending', warnings:[]}), false);
    assert.equal(context.applyCanvasMotionTask(node, {task_id:TASK_ONE, state:'downloading', stage:'preparing', progress:10, depth_state:'pending', pose_state:'pending', warnings:[]}), false);
    assert.equal(context.applyCanvasMotionTask(node, {task_id:TASK_ONE, state:'completed', stage:'completed', progress:100, depth_state:'completed', depth_url:'/assets/output/motion/depth.mp4', pose_state:'disabled', warnings:[]}), true);
    assert.equal(context.applyCanvasMotionTask(node, {task_id:TASK_ONE, state:'running', stage:'decoding', progress:100, depth_state:'running', pose_state:'pending', warnings:[]}), false);
    assert.equal(node.motionState, 'completed');
    assert.equal(await context.pollCanvasMotionTask('motion-1', TASK_ONE), 'completed');
    assert.equal(calls, 0);
}

// Break caught: branch-specific asset preparation must remain visible instead of collapsing to the generic downloading fallback.
{
    const saves = {count:0};
    const context = lifecycleContext({nodes:[], saves, fetch:async () => response(true, {})});
    assert.equal(context.motionTaskSafeStage('preparing_depth', 'downloading'), 'preparing_depth');
    assert.equal(context.motionTaskSafeStage('preparing_pose', 'running'), 'preparing_pose');
}

// Break caught: terminal responses are valid from queued or downloading even if a poll missed intermediate snapshots.
{
    const saves = {count:0};
    const queued = motionNode({id:'queued', motionTaskId:TASK_ONE, motionRunToken:1, motionState:'queued'});
    const downloading = motionNode({id:'downloading', motionTaskId:TASK_NEW, motionRunToken:1, motionState:'downloading'});
    const context = lifecycleContext({nodes:[queued, downloading], saves, fetch:async () => response(true, {})});
    assert.equal(context.applyCanvasMotionTask(queued, {task_id:TASK_ONE, state:'cancelled', stage:'cancelled', progress:0, depth_state:'cancelled', pose_state:'cancelled', warnings:[]}), true);
    assert.equal(context.applyCanvasMotionTask(downloading, {task_id:TASK_NEW, state:'failed', stage:'failed', progress:100, depth_state:'failed', pose_state:'failed', warnings:[], error:'safe failure'}), true);
    assert.equal(queued.motionState, 'cancelled');
    assert.equal(downloading.motionState, 'failed');
}

// Break caught: a delayed lower-rank poll response is ignored but does not stop the current valid poll sequence.
{
    const saves = {count:0};
    const node = motionNode({motionTaskId:TASK_ONE, motionRunToken:1, motionState:'running', motionStage:'decoding'});
    const payloads = [
        {task_id:TASK_ONE, state:'queued', stage:'queued', progress:1, depth_state:'pending', pose_state:'pending', warnings:[]},
        {task_id:TASK_ONE, state:'completed', stage:'completed', progress:100, depth_state:'completed', depth_url:'/assets/output/motion/depth.mp4', pose_state:'disabled', warnings:[]},
    ];
    let calls = 0;
    const context = lifecycleContext({nodes:[node], saves, fetch:async () => response(true, payloads[calls++])});
    assert.equal(await context.pollCanvasMotionTask('motion-1', TASK_ONE), 'completed');
    assert.equal(calls, 2);
    assert.equal(node.motionState, 'completed');
}

// Break caught: transport, 5xx, and malformed snapshots are transient and must keep polling the authoritative task instead of failing it.
{
    const saves = {count:0};
    const node = motionNode({motionTaskId:TASK_ONE, motionRunToken:1, motionState:'running', motionStage:'decoding'});
    const replies = [
        response(false, {detail:'temporary'}, 503),
        response(true, {state:'running', stage:'decoding', progress:40}),
        response(true, {
            task_id:TASK_ONE, state:'completed', stage:'completed', progress:100,
            depth_state:'completed', depth_url:'/assets/output/motion/depth.mp4',
            pose_state:'disabled', warnings:[],
        }),
    ];
    const requests = [];
    const context = lifecycleContext({nodes:[node], saves, fetch:async (url, options={}) => {
        requests.push({url, options});
        return replies.shift();
    }});
    assert.equal(await context.pollCanvasMotionTask('motion-1', TASK_ONE), 'completed');
    assert.equal(node.motionTaskId, TASK_ONE);
    assert.equal(node.motionState, 'completed');
    assert.deepEqual(requests.map(request => request.options.method || 'GET'), ['GET', 'GET', 'GET']);
    assert.deepEqual(context.delays.slice(0, 2), [800, 1200]);
}

// Break caught: exhausting bounded transient retries must preserve a polling task ID, and a later retry may only GET that task.
{
    const saves = {count:0};
    const node = motionNode({motionTaskId:TASK_ONE, motionRunToken:1, motionState:'running', motionStage:'decoding'});
    let available = false;
    const requests = [];
    const context = lifecycleContext({nodes:[node], saves, fetch:async (url, options={}) => {
        requests.push({url, options});
        if(!available) return response(false, {detail:'temporary'}, 503);
        return response(true, {
            task_id:TASK_ONE, state:'completed', stage:'completed', progress:100,
            depth_state:'completed', depth_url:'/assets/output/motion/depth.mp4',
            pose_state:'disabled', warnings:[],
        });
    }});
    assert.equal(await context.pollCanvasMotionTask('motion-1', TASK_ONE), 'unresolved');
    assert.equal(node.motionTaskId, TASK_ONE);
    assert.equal(node.motionState, 'running');
    available = true;
    assert.equal(await context.pollCanvasMotionTask('motion-1', TASK_ONE), 'completed');
    assert.equal(requests.every(request => (request.options.method || 'GET') === 'GET'), true);
}

// Break caught: a 404 explicitly resolves a saved task as missing rather than treating it like an ambiguous transport failure.
{
    const saves = {count:0};
    const node = motionNode({motionTaskId:TASK_ONE, motionRunToken:1, motionState:'running', motionStage:'decoding'});
    const context = lifecycleContext({nodes:[node], saves, fetch:async () => response(false, {detail:'not found'}, 404)});
    assert.equal(await context.pollCanvasMotionTask('motion-1', TASK_ONE), 'missing');
    assert.equal(node.motionTaskId, TASK_ONE);
    assert.equal(node.motionState, 'failed');
    assert.equal(node.motionTaskMissing, true);
}

// Break caught: an asset-looking traversal URL is not a public output URL and must be discarded before persistence.
{
    const saves = {count:0};
    const node = motionNode({motionTaskId:TASK_ONE, motionRunToken:1, motionState:'running'});
    const context = lifecycleContext({nodes:[node], saves, fetch:async () => response(true, {})});
    context.applyCanvasMotionTask(node, {
        task_id:TASK_ONE, state:'partial', stage:'partial', progress:100,
        depth_state:'completed', depth_url:'/assets/../../private/depth.mp4',
        pose_state:'failed', pose_error:'safe branch failure', warnings:[],
    });
    assert.equal(node.depthUrl, '');
    assert.equal(JSON.stringify(node).includes('private'), false);
}

// Break caught: public result URLs are path-only canonical asset/output references, never credential-bearing or encoded traversal URLs.
{
    const saves = {count:0};
    const context = lifecycleContext({nodes:[], saves, fetch:async () => response(true, {})});
    assert.equal(context.motionTaskSafeUrl('/assets/output/motion/depth.mp4'), '/assets/output/motion/depth.mp4');
    [
        '/assets/output/motion/depth.mp4?token=secret',
        '/assets/output/motion/depth.mp4#fragment',
        '/assets/output/%2e%2e/private.mp4',
        '/assets/output/%5cprivate.mp4',
        '/assets/output/%00private.mp4',
        '/assets/output/\u0000private.mp4',
    ].forEach(value => assert.equal(context.motionTaskSafeUrl(value), ''));
}

// Break caught: backend errors cannot persist raw absolute paths or file URIs when they appear after common field separators or in encoded form.
{
    const saves = {count:0};
    const context = lifecycleContext({nodes:[], saves, fetch:async () => response(true, {})});
    [
        'source_path=/private/video.mp4',
        'failed:/private/video.mp4',
        '//private/video.mp4',
        '///private/video.mp4',
        'source_path=//private/video.mp4',
        'failed://private/video.mp4',
        'failed,///private/video.mp4',
        'source_path=%2F%2Fprivate%2Fvideo.mp4',
        'failed:%2f%2Fprivate%2fvideo.mp4',
        'source_path=/%5Cprivate/video.mp4',
        'network=\\\\/private/video.mp4',
        'file:///private/video.mp4',
        'file:%2F%2Fprivate%2Fvideo.mp4',
        'encoded=%2Fprivate%2Fvideo.mp4',
        '\\\\server\\share\\video.mp4',
        'C:\\private\\video.mp4',
        '\\private\\video.mp4',
        'source_path=\\private\\video.mp4',
        '\\Users\\PC\\video.mp4',
        'source_path=\\Users\\PC\\video.mp4',
        'failed\u0000with control text',
        'Authorization: Bearer secret-value',
    ].forEach(value => assert.equal(context.motionTaskSafeMessage(value), 'Motion task failed'));
    assert.equal(context.motionTaskSafeMessage('The source video is invalid or unsupported.'), 'The source video is invalid or unsupported.');
    assert.equal(context.motionTaskSafeMessage('Audio/video extraction failed.'), 'Audio/video extraction failed.');
    assert.equal(context.motionTaskSafeMessage('Retry in 1/2 second.'), 'Retry in 1/2 second.');
    assert.equal(context.motionTaskSafeMessage('音频/视频提取失败'), '音频/视频提取失败');
    assert.equal(context.motionTaskSafeMessage('视频无效或不支持'), '视频无效或不支持');
}

console.log('canvas-motion-task-lifecycle: passed');
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
