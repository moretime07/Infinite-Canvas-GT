const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '..', 'static', 'js', 'canvas.js'), 'utf8');

function productionFunction(name){
    const start = source.indexOf(`function ${name}(`);
    assert.notEqual(start, -1, `canvas.js should define ${name}`);
    const next = source.indexOf('\nfunction ', start + 1);
    return source.slice(start, next === -1 ? source.length : next);
}

function controlsContext(){
    const alerts = [];
    const context = {
        nodes:[],
        uid:prefix => `${prefix}-1`,
        defaultPoint:(x, y) => ({x, y}),
        addNode:node => { context.nodes.push(node); return node; },
        tr:key => ({'canvas.motionProcessorRequired':'至少启用一个动作处理器'}[key] || key),
        alert:message => alerts.push(message),
        render:() => {},
        scheduleSave:() => {},
    };
    vm.createContext(context);
    vm.runInContext([
        productionFunction('addMotionExtractNode'),
        productionFunction('setMotionExtractProcessorEnabled'),
        'this.addMotionExtractNode = addMotionExtractNode;',
        'this.setMotionExtractProcessorEnabled = setMotionExtractProcessorEnabled;',
    ].join('\n'), context);
    return {context, alerts};
}

// Break caught: new motion nodes must begin with the persisted state contract, including disabled pose output.
{
    const {context} = controlsContext();
    const node = context.addMotionExtractNode({x:12, y:34});
    assert.deepEqual(JSON.parse(JSON.stringify(node)), {
        id:'motion-1', type:'motionExtract', x:12, y:34, w:480, h:680,
        depthEnabled:true, poseEnabled:false, preserveAudio:false,
        motionTaskId:'', motionState:'idle', motionStage:'', motionProgress:0,
        depthState:'pending', depthUrl:'', poseState:'disabled', poseUrl:'',
        motionWarnings:[], motionError:'',
    });
}

// Break caught: disabling the only processor must preserve an executable node and explain why.
{
    const {context, alerts} = controlsContext();
    const node = context.addMotionExtractNode({x:0, y:0});
    assert.equal(context.setMotionExtractProcessorEnabled(node, 'depth', false), false);
    assert.equal(node.depthEnabled, true);
    assert.match(alerts[0], /至少启用一个/);
}

// Break caught: pose is independently selectable, both processors may run, and audio is orthogonal.
{
    const {context} = controlsContext();
    const node = context.addMotionExtractNode({x:0, y:0});
    assert.equal(context.setMotionExtractProcessorEnabled(node, 'pose', true), true);
    assert.equal(node.poseEnabled, true);
    node.preserveAudio = true;
    assert.equal(node.depthEnabled, true);
    assert.equal(node.poseEnabled, true);
    assert.equal(node.preserveAudio, true);
    assert.equal(context.setMotionExtractProcessorEnabled(node, 'depth', false), true);
    assert.equal(node.depthEnabled, false);
    assert.equal(node.depthUrl, '');
    assert.equal(node.depthState, 'disabled');
}

function resolverContext(nodes, connections, loopRefs=()=>[]){
    const context = {
        nodes,
        connections,
        loopContext:null,
        normalizedFromPort:c => c.fromPort || '',
        videoRefsFromNode:(node, port='') => {
            if(node?.type === 'motionExtract') return context.motionOutputVideoRefs(node, port);
            return node?.refs || [];
        },
        imageRefsFromNode:node => node?.imageRefs || [],
        loopInputVideoRefs:loopRefs,
        tr:key => ({
            'canvas.motionNeedVideo':'请连接一个视频输入',
            'canvas.motionOnlyOneVideo':'动作提取一次只能处理一个视频，请保留一个输入',
            'canvas.motionImageInputRejected':'动作提取不支持图片输入，请连接一个视频',
            'canvas.motionDepthUnavailable':'深度结果不可用',
            'canvas.motionPoseUnavailable':'姿态结果不可用',
        }[key] || key),
    };
    vm.createContext(context);
    vm.runInContext([
        productionFunction('motionOutputVideoRefs'),
        productionFunction('motionInputVideoRefs'),
        productionFunction('resolveMotionInputVideo'),
        'this.motionOutputVideoRefs = motionOutputVideoRefs;',
        'this.motionInputVideoRefs = motionInputVideoRefs;',
        'this.resolveMotionInputVideo = resolveMotionInputVideo;',
    ].join('\n'), context);
    return context;
}

// Break caught: exact single-video resolution must not choose an arbitrary incoming reference.
{
    const video = {id:'video', type:'video', refs:[{url:'/clip.mp4', name:'clip.mp4', kind:'video'}]};
    const motion = {id:'motion', type:'motionExtract'};
    const context = resolverContext([video, motion], [{from:'video', to:'motion'}]);
    assert.deepEqual(JSON.parse(JSON.stringify(context.resolveMotionInputVideo(motion, {}))), {
        video:{url:'/clip.mp4', name:'clip.mp4', kind:'video'}, error:'',
    });
    context.connections = [];
    assert.match(context.resolveMotionInputVideo(motion, {}).error, /视频输入/);
    context.connections = [{from:'video', to:'motion'}, {from:'video', to:'motion', id:'duplicate'}];
    assert.match(context.resolveMotionInputVideo(motion, {}).error, /只能处理一个视频/);
}

// Break caught: a motion node inside a loop must receive exactly the current loop item, never the whole batch.
{
    const loop = {id:'loop', type:'loop'};
    const motion = {id:'motion', type:'motionExtract'};
    const seen = [];
    const context = resolverContext([loop, motion], [{from:'loop', to:'motion'}], (node, ctx) => {
        seen.push([node.id, ctx.index]);
        return [{url:`/loop-${ctx.index}.mp4`, name:'loop.mp4', kind:'video'}];
    });
    const resolved = context.resolveMotionInputVideo(motion, {loopContext:{index:7}});
    assert.equal(resolved.video.url, '/loop-7.mp4');
    assert.deepEqual(JSON.parse(JSON.stringify(seen)), [['loop', 7]]);
}

// Break caught: image-only sources must be rejected explicitly rather than converted or ignored.
{
    const image = {id:'image', type:'image', imageRefs:[{url:'/still.png', kind:'image'}]};
    const motion = {id:'motion', type:'motionExtract'};
    const context = resolverContext([image, motion], [{from:'image', to:'motion'}]);
    const resolved = context.resolveMotionInputVideo(motion, {});
    assert.equal(resolved.video, null);
    assert.match(resolved.error, /不支持图片输入/);
}

// Break caught: downstream consumers must receive only the completed branch selected by the named port.
{
    const context = resolverContext([], []);
    const node = {
        type:'motionExtract', depthEnabled:true, depthState:'completed', depthUrl:'/depth.mp4',
        poseEnabled:true, poseState:'completed', poseUrl:'/pose.mp4',
    };
    assert.deepEqual(JSON.parse(JSON.stringify(context.motionOutputVideoRefs(node, 'depth'))), [{url:'/depth.mp4', name:'depth.mp4', kind:'video'}]);
    assert.deepEqual(JSON.parse(JSON.stringify(context.motionOutputVideoRefs(node, 'pose'))), [{url:'/pose.mp4', name:'pose.mp4', kind:'video'}]);
    node.depthState = 'pending';
    assert.deepEqual(JSON.parse(JSON.stringify(context.motionOutputVideoRefs(node, 'depth'))), []);
    assert.equal(context.motionOutputVideoRefs(node, 'depth').error, '深度结果不可用');
    node.depthState = 'failed';
    node.depthUrl = '';
    assert.deepEqual(JSON.parse(JSON.stringify(context.motionOutputVideoRefs(node, 'depth'))), []);
    assert.equal(context.motionOutputVideoRefs(node, 'depth').error, '深度结果不可用');
    node.poseEnabled = false;
    node.poseState = 'disabled';
    node.poseUrl = '';
    assert.deepEqual(JSON.parse(JSON.stringify(context.motionOutputVideoRefs(node, 'pose'))), []);
    assert.equal(context.motionOutputVideoRefs(node, 'pose').error, '姿态结果不可用');
}

console.log('Canvas motion node tests passed');
