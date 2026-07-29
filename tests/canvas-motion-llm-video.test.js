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
const motionFailed = {
    id:'motion-failed',
    type:'motionExtract',
    depthEnabled:true,
    depthState:'failed',
    depthUrl:'/assets/motion/failed.mp4',
};
const motionCompletedWithoutUrl = {
    id:'motion-completed-without-url',
    type:'motionExtract',
    depthEnabled:true,
    depthState:'completed',
    depthUrl:'',
};
const targetLLM = {id:'llm-target', type:'llm'};
const mediaContext = {
    nodes:[motionReady, motionPending, motionFailed, motionCompletedWithoutUrl, targetLLM],
    connections:[
        {from:motionReady.id, to:targetLLM.id, fromPort:'pose'},
        {from:motionReady.id, to:targetLLM.id, fromPort:'depth'},
        {from:motionReady.id, to:targetLLM.id, fromPort:'depth'},
        {from:motionPending.id, to:targetLLM.id, fromPort:'depth'},
        {from:motionPending.id, to:targetLLM.id, fromPort:'pose'},
        {from:motionReady.id, to:targetLLM.id},
    ],
    tr:key => key,
    mediaKindForNode:node => node?.mediaKind || (String(node?.url || '').endsWith('.mp4') ? 'video' : 'image'),
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

// Break caught: failed results must not become usable video references.
mediaContext.connections = [{from:motionFailed.id, to:targetLLM.id, fromPort:'depth'}];
assert.deepEqual(Array.from(mediaContext.llmInputVideos(targetLLM)), []);

// Break caught: a completed branch without a URL is still unavailable.
mediaContext.connections = [{from:motionCompletedWithoutUrl.id, to:targetLLM.id, fromPort:'depth'}];
assert.deepEqual(Array.from(mediaContext.llmInputVideos(targetLLM)), []);

const normalVideo = {id:'normal-video', type:'image', url:'/assets/input/normal.mp4'};
const output = {
    id:'video-output',
    type:'output',
    images:[
        {url:'/assets/output/older.mp4'},
        {url:'/assets/output/latest.mp4'},
        {url:'/assets/output/final-still.png'},
    ],
};
const groupVideo = {id:'group-video', type:'image', url:'/assets/group/unique.mp4'};
const duplicateGroupVideo = {id:'group-duplicate', type:'image', url:normalVideo.url};
const groupImage = {id:'group-image', type:'image', url:'/assets/group/still.png'};
const group = {id:'video-group', type:'group', items:[duplicateGroupVideo.id, groupImage.id, groupVideo.id]};
mediaContext.nodes.push(normalVideo, output, groupVideo, duplicateGroupVideo, groupImage, group);
mediaContext.connections = [
    {from:normalVideo.id, to:targetLLM.id},
    {from:output.id, to:targetLLM.id},
    {from:group.id, to:targetLLM.id},
    {from:motionReady.id, to:targetLLM.id, fromPort:'pose'},
    {from:motionReady.id, to:targetLLM.id, fromPort:'depth'},
    {from:normalVideo.id, to:targetLLM.id},
];

// Break caught: motion support must preserve ordinary video, Output, and group ordering and deduplication.
assert.deepEqual(
    Array.from(mediaContext.llmInputVideos(targetLLM)),
    [normalVideo.url, '/assets/output/latest.mp4', groupVideo.url, poseUrl, depthUrl]
);

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
