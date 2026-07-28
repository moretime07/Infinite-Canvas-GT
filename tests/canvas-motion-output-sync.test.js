const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '..', 'static', 'js', 'canvas.js'), 'utf8');
const TASK_ID = 'canvas_motion_55555555555555555555555555555555';

function productionFunction(name){
    const match = new RegExp(`(?:async )?function ${name}\\(`).exec(source);
    assert.ok(match, `canvas.js should define ${name}`);
    const start = match.index;
    const next = /\n(?:async )?function \w+\(/g;
    next.lastIndex = start + match[0].length;
    const following = next.exec(source);
    return source.slice(start, following ? following.index : source.length);
}

function outputSyncContext(){
    const renders = {count:0};
    const saves = {count:0};
    const motion = {
        id:'motion', type:'motionExtract', depthEnabled:true, poseEnabled:true,
        motionTaskId:TASK_ID, motionState:'running', motionStage:'decoding', motionProgress:80,
        depthState:'running', depthUrl:'', poseState:'running', poseUrl:'',
        motionWarnings:[], motionError:'',
    };
    const depthOutput = {id:'depth-output', type:'output', images:[]};
    const poseOutput = {id:'pose-output', type:'output', images:[]};
    const lateOutput = {id:'late-output', type:'output', images:[]};
    const context = {
        nodes:[motion, depthOutput, poseOutput, lateOutput],
        connections:[
            {id:'depth-link', from:'motion', to:'depth-output', fromPort:'depth'},
            {id:'pose-link', from:'motion', to:'pose-output', fromPort:'pose'},
        ],
        tr:key => ({
            'canvas.motionFailed':'Motion task failed',
            'canvas.motionDepthUnavailable':'Depth result unavailable',
            'canvas.motionPoseUnavailable':'Pose result unavailable',
            'canvas.motionResultPortRequired':'Choose a motion result port',
        }[key] || key),
        render:() => { renders.count += 1; },
        scheduleSave:() => { saves.count += 1; },
        scheduleOutputNodeAutoExport:() => {},
        URL,
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
        'motionVideoRefMetadata',
        'motionOutputVideoRefs',
        'normalizedFromPort',
        'outputUrlValue',
        'outputNodesForSource',
        'outputHasUrl',
        'appendOutputImages',
        'appendOutputImagesWithoutDuplicates',
        'syncConnectedOutputsFromMotion',
        'syncLatestGeneratedOutputToConnection',
        'motionTaskPersist',
        'applyCanvasMotionTask',
        'resumePendingCanvasMotionTasks',
    ];
    vm.runInContext(names.map(productionFunction).concat(names.map(name => `this.${name} = ${name};`)).join('\n'), context);
    return {context, motion, depthOutput, poseOutput, lateOutput, renders, saves};
}

// Break caught: terminal motion branch URLs remained inside the motion node instead of reaching their connected Output nodes.
{
    const {context, motion, depthOutput, poseOutput} = outputSyncContext();
    context.applyCanvasMotionTask(motion, {
        task_id:TASK_ID,
        state:'completed',
        stage:'completed',
        progress:100,
        depth_state:'completed',
        depth_url:'/assets/output/motion/depth.mp4',
        pose_state:'completed',
        pose_url:'/assets/output/motion/pose.mp4',
        warnings:[],
    });
    assert.deepEqual(
        JSON.parse(JSON.stringify(depthOutput.images.map(item => ({url:item.url, kind:item.kind})))),
        [{url:'/assets/output/motion/depth.mp4', kind:'video'}],
    );
    assert.deepEqual(
        JSON.parse(JSON.stringify(poseOutput.images.map(item => ({url:item.url, kind:item.kind})))),
        [{url:'/assets/output/motion/pose.mp4', kind:'video'}],
    );

    context.applyCanvasMotionTask(motion, {
        task_id:TASK_ID,
        state:'completed',
        stage:'completed',
        progress:100,
        depth_state:'completed',
        depth_url:'/assets/output/motion/depth.mp4',
        pose_state:'completed',
        pose_url:'/assets/output/motion/pose.mp4',
        warnings:[],
    });
    assert.equal(depthOutput.images.length, 1);
    assert.equal(poseOutput.images.length, 1);
}

// Break caught: reopening a canvas with an already-completed motion task left a connected Output node empty.
{
    const {context, motion, depthOutput, renders, saves} = outputSyncContext();
    motion.motionState = 'completed';
    motion.motionStage = 'completed';
    motion.motionProgress = 100;
    motion.depthState = 'completed';
    motion.depthUrl = '/assets/output/motion/depth.mp4';
    motion.poseState = 'disabled';
    motion.poseUrl = '';
    context.resumePendingCanvasMotionTasks();
    assert.deepEqual(
        JSON.parse(JSON.stringify(depthOutput.images.map(item => ({url:item.url, kind:item.kind})))),
        [{url:'/assets/output/motion/depth.mp4', kind:'video'}],
    );
    assert.equal(renders.count, 1);
    assert.equal(saves.count, 1);
}

// Break caught: connecting an Output node after extraction completed failed to backfill the selected named result.
{
    const {context, motion, lateOutput} = outputSyncContext();
    motion.motionState = 'completed';
    motion.depthState = 'completed';
    motion.depthUrl = '/assets/output/motion/depth.mp4';
    assert.equal(context.syncLatestGeneratedOutputToConnection('motion', 'late-output', 'depth'), true);
    assert.deepEqual(
        JSON.parse(JSON.stringify(lateOutput.images.map(item => ({url:item.url, kind:item.kind})))),
        [{url:'/assets/output/motion/depth.mp4', kind:'video'}],
    );
}

console.log('Canvas motion Output sync tests passed');
