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

// Break caught: motion results must be usable as LLM input.
assert.equal(connectionContext.canConnect(motion.id, llm.id), true);

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

mediaContext.connections = [{from:motionReady.id, to:targetLLM.id, fromPort:'depth'}];
assert.deepEqual(Array.from(mediaContext.llmInputVideos(targetLLM)), [depthUrl]);

mediaContext.connections = [{from:motionReady.id, to:targetLLM.id, fromPort:'pose'}];
assert.deepEqual(Array.from(mediaContext.llmInputVideos(targetLLM)), [poseUrl]);
