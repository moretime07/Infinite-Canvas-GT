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

const imageOne = {id:'image-1', type:'image', url:'/assets/input/one.png', name:'one.png'};
const imageTwo = {id:'image-2', type:'image', url:'/assets/input/two.png', name:'two.png'};
const imageThree = {id:'image-3', type:'image', url:'/assets/input/three.png', name:'three.png'};
const loop = {
    id:'loop-1',
    type:'loop',
    imageInput:true,
    imageBatchSize:2,
    loopStart:1,
};
const llm = {
    id:'llm-1',
    type:'llm',
    llmProvider:'openrouter',
    model:'vision-model',
    systemPrompt:'Describe the supplied image.',
};

const sandbox = {
    nodes:[imageOne, imageTwo, imageThree, loop, llm],
    connections:[
        {from:imageOne.id, to:loop.id},
        {from:imageTwo.id, to:loop.id},
        {from:imageThree.id, to:loop.id},
        {from:loop.id, to:llm.id},
    ],
    loopContext:null,
    CANVAS_IMAGE_OUTPUT_TYPES:[],
    CANVAS_MEDIA_OUTPUT_TYPES:[],
    isVideoUrl:() => false,
    isAudioUrl:() => false,
    outputUrlValue:item => typeof item === 'string' ? item : item?.url || '',
    outputImageName:url => String(url || '').split('/').pop(),
    generatedImageRefs:() => [],
    syncDefaultCanvasNodeProvider:() => {},
    unresolvedDefaultCanvasNodeError:() => '',
    resolveCanvasNodeRequest:() => ({providerId:'openrouter', model:'vision-model'}),
    chatProviderModeOptions:() => ({}),
    resolveChatModel:value => value,
    responseErrorMessage:async () => 'LLM failed',
    capturedRequests:[],
};
sandbox.cascadeFetch = async (url, options={}) => {
    sandbox.capturedRequests.push({url, body:JSON.parse(options.body || '{}')});
    return {ok:true, json:async () => ({text:'image recognized'})};
};

vm.createContext(sandbox);
vm.runInContext([
    productionFunction('mediaKindForNode'),
    productionFunction('imageRefsFromNode'),
    productionFunction('loopInputImageRefs'),
    productionFunction('llmInputImages'),
    productionFunction('llmInputVideos'),
    productionFunction('callCanvasLLM'),
    'this.controls = {llmInputImages, callCanvasLLM};',
].join('\n'), sandbox);

assert.deepEqual(
    Array.from(sandbox.controls.llmInputImages(llm)),
    ['/assets/input/one.png', '/assets/input/two.png'],
    'the LLM badge/input state should recognize the complete starting image batch'
);

llm._activeLoopCtx = {index:3, total:3, nodeId:loop.id};
assert.deepEqual(
    Array.from(sandbox.controls.llmInputImages(llm)),
    ['/assets/input/three.png'],
    'the LLM should recognize the image batch selected for the active loop round'
);

(async () => {
    const text = await sandbox.controls.callCanvasLLM(llm, 'Describe this image');
    assert.equal(text, 'image recognized');
    assert.equal(sandbox.capturedRequests.length, 1);
    assert.equal(sandbox.capturedRequests[0].url, '/api/canvas-llm');
    assert.deepEqual(
        Array.from(sandbox.capturedRequests[0].body.images),
        ['/assets/input/three.png'],
        'the complete active loop image batch must be sent in the real LLM request body'
    );
    console.log('Canvas loop-to-LLM image tests passed');
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
