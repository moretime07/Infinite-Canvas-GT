import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {fileURLToPath} from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
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

function contextFor(nodes, connections){
    const context = {
        nodes, connections, loopContext:null, comfyBackendCount:4,
        Math, Number, String, Array, Object, Set, Promise, URL,
        normalizedFromPort:connection => connection.fromPort || '',
        tr:key => ({
            'canvas.motionDepthUnavailable':'Depth result is unavailable',
            'canvas.motionPoseUnavailable':'Pose result is unavailable',
            'canvas.motionResultPortRequired':'A motion result port is required',
        }[key] || key),
        motionVideoRefMetadata:() => ({}),
        videoRefsFromNode:node => node?.refs || [],
        runGenerator:async id => { context.runs.push(`generator:${id}`); },
        runMsGenNode:async id => { context.runs.push(`msgen:${id}`); },
        runComfyNode:async id => { context.runs.push(`comfy:${id}`); },
        runLTXDirectorNode:async id => { context.runs.push(`ltx:${id}`); },
        runLLMNode:async id => { context.runs.push(`llm:${id}`); },
        runVideoNode:async id => { context.runs.push(`video:${id}`); },
        runRhNode:async id => { context.runs.push(`rh:${id}`); },
        runMotionExtractNode:async (id, options) => { context.runs.push({id, options}); return 'completed'; },
        runs:[],
    };
    vm.createContext(context);
    vm.runInContext([
        productionFunction('loopInputVideoRefs'),
        productionFunction('motionOutputVideoRefs'),
        productionFunction('motionTaskSafeUrl'),
        productionFunction('motionLoopItemState'),
        productionFunction('persistMotionLoopItem'),
        productionFunction('motionCascadeSelectedBranches'),
        productionFunction('assertMotionCascadeBranches'),
        productionFunction('canvasRunTypes'),
        productionFunction('cascadeParallelLimit'),
        productionFunction('computeCascadeOrder'),
        productionFunction('loopCascadeContextForRound'),
        productionFunction('runCascadeNodeByType'),
        'this.loopInputVideoRefs = loopInputVideoRefs;',
        'this.motionOutputVideoRefs = motionOutputVideoRefs;',
        'this.motionLoopItemState = motionLoopItemState;',
        'this.persistMotionLoopItem = persistMotionLoopItem;',
        'this.motionCascadeSelectedBranches = motionCascadeSelectedBranches;',
        'this.assertMotionCascadeBranches = assertMotionCascadeBranches;',
        'this.canvasRunTypes = canvasRunTypes;',
        'this.cascadeParallelLimit = cascadeParallelLimit;',
        'this.computeCascadeOrder = computeCascadeOrder;',
        'this.loopCascadeContextForRound = loopCascadeContextForRound;',
        'this.runCascadeNodeByType = runCascadeNodeByType;',
    ].join('\n'), context);
    return context;
}

function response(ok, payload, status=ok ? 200 : 422){
    return {ok, status, json:async () => payload};
}

function cascadeIndexContext(mode){
    const loop = {
        id:'loop', type:'loop', mode, count:3, loopStart:2,
        imageInput:true, imageBatchSize:2, videoInput:true, videoBatchSize:3,
    };
    const target = {id:'target', type:'video', running:false};
    const seen = [];
    const context = {
        nodes:[loop, target],
        loopContext:null,
        Math, Number, String, Array, Object, Promise, Error,
        alert:message => { throw new Error(message); },
        computeCascadeOrder:() => ['target'],
        resolveCascadeLoop:() => ({node:loop, count:3, mode}),
        beginCascade:() => ({message:'mixed loop', currentNodeId:''}),
        refreshNodes:() => {},
        cascadeUiNodeIds:() => ['loop', 'target'],
        ensureCascadeActive:() => {},
        cascadeParallelLimit:() => 3,
        runLimitedCascadeRounds:async (rounds, _limit, worker) => Promise.allSettled(rounds.map(worker)),
        runCascadeNodeWithLoopContext:async (_node, loopCtx) => {
            seen.push(JSON.parse(JSON.stringify(loopCtx)));
        },
        loopInputVideoRefs:(_node, loopCtx) => [{url:`/assets/video-${loopCtx.videoIndex}.mp4`, kind:'video'}],
        finalizeCascade:() => {},
        isCascadeAbortError:() => false,
        tr:key => key,
    };
    vm.createContext(context);
    vm.runInContext([
        productionFunction('loopCascadeContextForRound'),
        productionFunction('runNodeCascade'),
        'this.runNodeCascade = runNodeCascade;',
    ].join('\n'), context);
    return {context, seen};
}

function runtimeMotionContext(nodes, connections, fetch){
    const context = {
        nodes, connections, fetch, loopContext:null, comfyBackendCount:1,
        Math, Number, String, Array, Object, Set, Promise, JSON, RegExp, URL, encodeURIComponent,
        normalizedFromPort:connection => connection.fromPort || '',
        videoRefsFromNode:node => node?.refs || [],
        imageRefsFromNode:() => [],
        motionVideoRefMetadata:() => ({}),
        render:() => {}, scheduleSave:() => {}, refreshNodes:() => {},
        tr:key => ({
            'canvas.motionFailed':'Motion task failed',
            'canvas.motionNeedVideo':'Connect one video input',
            'canvas.motionOnlyOneVideo':'Only one video input is supported',
            'canvas.motionDepthUnavailable':'Depth result is unavailable',
            'canvas.motionPoseUnavailable':'Pose result is unavailable',
            'canvas.motionResultPortRequired':'A motion result port is required',
            'canvas.loopRound':'Round',
        }[key] || key),
        sleep:async () => {},
        resolveCascadeLoop:() => ({node:nodes.find(node => node.type === 'loop'), count:2, mode:'serial'}),
        beginCascade:() => ({message:'motion loop'}),
        ensureCascadeActive:() => {},
        isCascadeAbortError:() => false,
        finalizeCascade:() => {},
        cascadeUiNodeIds:(_id, order) => order,
        alert:() => {},
    };
    vm.createContext(context);
    vm.runInContext([
        productionFunction('loopInputVideoRefs'),
        productionFunction('motionOutputVideoRefs'),
        productionFunction('motionLoopItemState'),
        productionFunction('persistMotionLoopItem'),
        productionFunction('snapshotMotionLoopItem'),
        productionFunction('motionLoopCurrentVideoRef'),
        productionFunction('motionInputVideoRefs'),
        productionFunction('resolveMotionInputVideo'),
        productionFunction('motionTaskIdIsSafe'),
        productionFunction('motionTaskIsTerminal'),
        productionFunction('motionTaskIsPolling'),
        productionFunction('motionTaskStateIsKnown'),
        productionFunction('motionTaskSafeState'),
        productionFunction('motionTaskSafeStage'),
        productionFunction('motionTaskSafeBranchState'),
        productionFunction('motionTaskCanTransition'),
        productionFunction('motionTaskSafeUrl'),
        productionFunction('motionTaskSafeMessage'),
        productionFunction('motionTaskResponseError'),
        productionFunction('motionTaskNode'),
        productionFunction('motionTaskPersist'),
        productionFunction('applyCanvasMotionTask'),
        productionFunction('createCanvasMotionTask'),
        productionFunction('pollCanvasMotionTask'),
        productionFunction('runMotionExtractNode'),
        productionFunction('retryMotionExtract'),
        productionFunction('motionCascadeSelectedBranches'),
        productionFunction('assertMotionCascadeBranches'),
        productionFunction('canvasRunTypes'),
        productionFunction('computeCascadeOrder'),
        productionFunction('runCascadeNodeByType'),
        productionFunction('runCascadeNodeWithLoopContext'),
        productionFunction('loopCascadeContextForRound'),
        productionFunction('runNodeCascade'),
        'this.runMotionExtractNode = runMotionExtractNode;',
        'this.retryMotionExtract = retryMotionExtract;',
        'this.runNodeCascade = runNodeCascade;',
    ].join('\n'), context);
    return context;
}

// Break caught: a render-time default must not erase a user's enabled video loop input or its configured batch size.
{
    const videos = [{url:'/assets/a.mp4', kind:'video'}, {url:'/assets/b.mp4', kind:'video'}];
    const loop = {id:'loop', type:'loop', videoInput:true, videoBatchSize:2, loopStart:1};
    const context = contextFor([{id:'source', refs:videos}, loop], [{from:'source', to:'loop'}]);
    assert.deepEqual(JSON.parse(JSON.stringify(context.loopInputVideoRefs(loop, {index:1}))), videos);
    assert.equal(loop.videoInput, true);
    assert.equal(loop.videoBatchSize, 2);
}

// Break caught: unequal image/video batch sizes must advance independent source indices in both serial and parallel cascades.
{
    for(const mode of ['serial', 'parallel']){
        const loop = {
            id:'loop', type:'loop', mode, loopStart:2, imageInput:true, imageBatchSize:2,
            videoInput:true, videoBatchSize:3,
        };
        const context = contextFor([loop], []);
        assert.deepEqual(
            JSON.parse(JSON.stringify(context.loopCascadeContextForRound(loop, 1, 3))),
            {index:2, total:4, nodeId:'loop', imageIndex:2, videoIndex:2},
        );
        assert.deepEqual(
            JSON.parse(JSON.stringify(context.loopCascadeContextForRound(loop, 2, 3))),
            {index:3, total:4, nodeId:'loop', imageIndex:4, videoIndex:5},
        );
        assert.deepEqual(
            JSON.parse(JSON.stringify(context.loopCascadeContextForRound(loop, 3, 3))),
            {index:4, total:4, nodeId:'loop', imageIndex:6, videoIndex:8},
        );
    }
}

// Break caught: both cascade execution branches must pass those independent indices to downstream nodes.
for(const mode of ['serial', 'parallel']){
    const {context, seen} = cascadeIndexContext(mode);
    await context.runNodeCascade('target');
    seen.sort((left, right) => left.index - right.index);
    assert.deepEqual(
        seen.map(item => ({
            index:item.index, imageIndex:item.imageIndex, videoIndex:item.videoIndex,
            currentVideo:item.currentVideoRef?.url,
        })),
        [
            {index:2, imageIndex:2, videoIndex:2, currentVideo:'/assets/video-2.mp4'},
            {index:3, imageIndex:4, videoIndex:5, currentVideo:'/assets/video-5.mp4'},
            {index:4, imageIndex:6, videoIndex:8, currentVideo:'/assets/video-8.mp4'},
        ],
        mode,
    );
}

// Break caught: each loop iteration must retain task state and branch URLs under its own identity rather than inheriting a preceding video result.
{
    const motion = {id:'motion', type:'motionExtract'};
    const context = contextFor([motion], []);
    const first = context.motionLoopItemState(motion, {loopContext:{nodeId:'loop', index:1, currentVideoRef:{url:'/assets/a.mp4'}}});
    first.taskId = 'canvas_motion_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
    first.status = 'completed';
    first.depthUrl = '/assets/depth-a.mp4';
    first.poseUrl = '/assets/pose-a.mp4';
    context.persistMotionLoopItem(motion, first);
    const second = context.motionLoopItemState(motion, {loopContext:{nodeId:'loop', index:2, currentVideoRef:{url:'/assets/b.mp4'}}});
    assert.notEqual(second, first);
    assert.equal(second.taskId, '');
    assert.equal(second.depthUrl, '');
    assert.equal(second.poseUrl, '');
    assert.equal(motion.motionLoopItems['loop:1:/assets/a.mp4'].depthUrl, '/assets/depth-a.mp4');
}

// Break caught: an unvalidated loop source must never become an object key, source field, or serialized canvas value.
{
    const unsafeSources = [
        '/assets/a.mp4?token=secret',
        '/assets/a.mp4#fragment',
        'https://user:secret@example.invalid/a.mp4',
        String.raw`C:\private\a.mp4`,
    ];
    for(const sourceUrl of unsafeSources){
        const motion = {id:'motion', type:'motionExtract'};
        const context = contextFor([motion], []);
        assert.equal(
            context.motionLoopItemState(motion, {
                loopContext:{nodeId:'loop', index:1, currentVideoRef:{url:sourceUrl}},
            }),
            null,
        );
        assert.equal(JSON.stringify(motion).includes(sourceUrl), false);
        assert.equal(Object.hasOwn(motion, 'motionLoopItems'), false);
    }
}

// Break caught: motion extraction is a runnable cascade step but remains outside generator provider classification and is serialized with GPU work.
{
    const nodes = [
        {id:'source', type:'video'},
        {id:'motion', type:'motionExtract', depthEnabled:true, depthState:'completed', depthUrl:'/assets/depth.mp4'},
        {id:'child', type:'video'},
    ];
    const connections = [
        {from:'source', to:'motion'},
        {from:'motion', to:'child', fromPort:'depth'},
    ];
    const context = contextFor(nodes, connections);
    assert.deepEqual(JSON.parse(JSON.stringify(context.computeCascadeOrder('child'))), ['source', 'motion', 'child']);
    assert.equal(context.canvasRunTypes().includes('motionExtract'), true);
    assert.equal(context.cascadeParallelLimit(['motion', 'child'], 4), 1);
    await context.runCascadeNodeByType(nodes[1], {loopContext:{nodeId:'loop', index:2}});
    assert.deepEqual(JSON.parse(JSON.stringify(context.runs)), [{id:'motion', options:{cascade:true, loopContext:{nodeId:'loop', index:2}}}]);
}

// Break caught: the child branch must be explicit; depth and pose URLs must neither exchange nor merge, and failed/disabled pose blocks only pose consumers.
{
    const motion = {
        id:'motion', type:'motionExtract', depthEnabled:true, depthState:'completed', depthUrl:'/assets/depth.mp4',
        poseEnabled:false, poseState:'disabled', poseUrl:'',
    };
    const context = contextFor([motion, {id:'depth-child', type:'video'}, {id:'pose-child', type:'video'}], [
        {from:'motion', to:'depth-child', fromPort:'depth'},
        {from:'motion', to:'pose-child', fromPort:'pose'},
    ]);
    assert.deepEqual(JSON.parse(JSON.stringify(context.motionCascadeSelectedBranches(motion))), ['depth', 'pose']);
    assert.deepEqual(JSON.parse(JSON.stringify(context.motionOutputVideoRefs(motion, 'depth'))), [{url:'/assets/depth.mp4', name:'depth.mp4', kind:'video'}]);
    assert.deepEqual(JSON.parse(JSON.stringify(context.motionOutputVideoRefs(motion, 'pose'))), []);
    assert.throws(() => context.assertMotionCascadeBranches(motion), /Pose result is unavailable/);
}

// Break caught: a loop must post each current video in source order, retain failed branch state, continue to the next item, and retry only the named failed item.
{
    const taskA = 'canvas_motion_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
    const taskB = 'canvas_motion_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';
    const retryA = 'canvas_motion_cccccccccccccccccccccccccccccccc';
    const loop = {id:'loop', type:'loop', videoInput:true, videoBatchSize:1, loopStart:1, count:2, mode:'serial'};
    const motion = {id:'motion', type:'motionExtract', depthEnabled:true, poseEnabled:false, motionTaskId:'', motionState:'idle'};
    const source = {id:'source', type:'video', refs:[{url:'/assets/a.mp4', kind:'video'}, {url:'/assets/b.mp4', kind:'video'}]};
    const requests = [];
    const context = runtimeMotionContext([source, loop, motion], [{from:'source', to:'loop'}, {from:'loop', to:'motion'}], async (url, options={}) => {
        requests.push({url, options});
        if(options.method === 'POST'){
            const sourceUrl = JSON.parse(options.body).source_url;
            const taskId = sourceUrl === '/assets/a.mp4' && requests.filter(request => request.options.method === 'POST').length === 1 ? taskA
                : sourceUrl === '/assets/b.mp4' ? taskB : retryA;
            return response(true, {task_id:taskId, state:'queued', stage:'queued', progress:0, depth_state:'pending', pose_state:'disabled', warnings:[]});
        }
        if(url.endsWith(taskA)) return response(true, {task_id:taskA, state:'failed', stage:'failed', progress:100, depth_state:'failed', depth_error:'first item failed', pose_state:'disabled', pose_error:'', warnings:[], error:'first item failed'});
        if(url.endsWith(taskB)) return response(true, {task_id:taskB, state:'completed', stage:'completed', progress:100, depth_state:'completed', depth_url:'/assets/depth-b.mp4', pose_state:'disabled', pose_error:'', warnings:[]});
        return response(true, {task_id:retryA, state:'completed', stage:'completed', progress:100, depth_state:'completed', depth_url:'/assets/depth-a.mp4', pose_state:'disabled', pose_error:'', warnings:[]});
    });
    await context.runNodeCascade('motion');
    const posts = requests.filter(request => request.options.method === 'POST').map(request => JSON.parse(request.options.body).source_url);
    assert.deepEqual(posts, ['/assets/a.mp4', '/assets/b.mp4']);
    const failedKey = 'loop:1:/assets/a.mp4';
    assert.deepEqual(JSON.parse(JSON.stringify(context.nodes[2].motionLoopItems[failedKey])), {
        key:failedKey, loopNodeId:'loop', index:1, sourceUrl:'/assets/a.mp4', taskId:taskA, status:'failed', error:'first item failed',
        depthState:'failed', depthUrl:'', depthError:'first item failed', poseState:'disabled', poseUrl:'', poseError:'',
    });
    loop.loopStart = 2;
    await context.retryMotionExtract('motion');
    assert.deepEqual(requests.filter(request => request.options.method === 'POST').map(request => JSON.parse(request.options.body).source_url), ['/assets/a.mp4', '/assets/b.mp4', '/assets/a.mp4']);
}

// Break caught: a queued task for the current loop item is polled rather than submitted again.
{
    const task = 'canvas_motion_dddddddddddddddddddddddddddddddd';
    const loop = {id:'loop', type:'loop', videoInput:true, videoBatchSize:1, loopStart:1};
    const motion = {id:'motion', type:'motionExtract', depthEnabled:true, poseEnabled:false, motionTaskId:task, motionState:'queued', motionRunToken:0};
    const source = {id:'source', type:'video', refs:[{url:'/assets/a.mp4', kind:'video'}]};
    motion.motionLoopItems = {'loop:1:/assets/a.mp4':{key:'loop:1:/assets/a.mp4', loopNodeId:'loop', index:1, sourceUrl:'/assets/a.mp4', taskId:task, status:'queued', error:'', depthState:'pending', depthUrl:'', depthError:'', poseState:'disabled', poseUrl:'', poseError:''}};
    const requests = [];
    const context = runtimeMotionContext([source, loop, motion], [{from:'source', to:'loop'}, {from:'loop', to:'motion'}], async (url, options={}) => {
        requests.push({url, options});
        return response(true, {task_id:task, state:'completed', stage:'completed', progress:100, depth_state:'completed', depth_url:'/assets/depth-a.mp4', pose_state:'disabled', warnings:[]});
    });
    await context.runMotionExtractNode('motion', {loopContext:{nodeId:'loop', index:1, currentVideoRef:{url:'/assets/a.mp4'}}});
    assert.deepEqual(requests.map(request => request.options.method || 'GET'), ['GET']);
}

console.log('canvas-motion-loop-cascade: passed');
