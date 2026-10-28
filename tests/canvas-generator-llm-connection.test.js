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

const imageUrl = '/assets/generator.png';
const videoUrl = '/assets/video.mp4';
const allNodes = [
    {id:'image', type:'image', url:'/assets/input.png'},
    {id:'prompt', type:'prompt', text:'prompt'},
    {id:'loop', type:'loop', imageInput:true, videoInput:true, showPrompt:true},
    {id:'group', type:'group', items:[]},
    {id:'prompt-group', type:'promptGroup', items:[]},
    {id:'generator', type:'generator', generatedOutputs:[{url:imageUrl, kind:'image'}]},
    {id:'msgen', type:'msgen', generatedOutputs:[{url:'/assets/msgen.png', kind:'image'}]},
    {id:'comfy', type:'comfy', generatedOutputs:[{url:'/assets/comfy.png', kind:'image'}]},
    {id:'ltx', type:'ltxDirector', generatedOutputs:[{url:'/assets/ltx.mp4', kind:'video'}]},
    {id:'video', type:'video', generatedOutputs:[{url:videoUrl, kind:'video'}]},
    {id:'rh', type:'rh', generatedOutputs:[{url:'/assets/rh.mp4', kind:'video'}]},
    {
        id:'motion',
        type:'motionExtract',
        depthEnabled:true,
        poseEnabled:true,
        depthState:'completed',
        depthUrl:'/assets/depth.mp4',
        poseState:'completed',
        poseUrl:'/assets/pose.mp4',
    },
    {id:'output', type:'output', images:[{url:videoUrl, kind:'video'}]},
    {id:'llm', type:'llm', outputText:'analysis'},
];

const context = {
    nodes:allNodes,
    connections:[],
    CANVAS_GENERATOR_TYPES:['generator', 'msgen', 'comfy', 'ltxDirector', 'video', 'rh'],
    CANVAS_MEDIA_OUTPUT_TYPES:['generator', 'msgen', 'comfy', 'ltxDirector', 'video', 'rh'],
    wouldCreateGeneratorCycle:() => false,
    outputUrlValue:item => typeof item === 'string' ? item : item?.url || '',
    outputImageName:url => String(url || '').split('/').pop(),
    mediaKindForOutputItem:item => item?.kind || (String(item?.url || item || '').endsWith('.mp4') ? 'video' : 'image'),
    mediaKindForNode:node => node?.kind || (String(node?.url || '').endsWith('.mp4') ? 'video' : 'image'),
    isVideoUrl:url => String(url || '').endsWith('.mp4'),
    isAudioUrl:url => String(url || '').endsWith('.mp3'),
    loopContext:null,
    loopInputImageRefs:() => [],
    motionOutputVideoRefs:(node, portName) => {
        if(portName === 'depth' && node.depthUrl) return [{url:node.depthUrl, kind:'video'}];
        if(portName === 'pose' && node.poseUrl) return [{url:node.poseUrl, kind:'video'}];
        return [];
    },
};
vm.createContext(context);
vm.runInContext([
    productionFunction('generatedImageRefs'),
    productionFunction('mediaRefsFromNode'),
    productionFunction('llmInputImages'),
    productionFunction('llmInputVideos'),
    productionFunction('nodeOutputPorts'),
    productionFunction('normalizedFromPort'),
    productionFunction('connectionKey'),
    productionFunction('nodeCanReceive'),
    productionFunction('nodeCanEmit'),
    productionFunction('canConnect'),
    productionFunction('sanitizeConnections'),
    'this.controls = {canConnect, sanitizeConnections, llmInputImages, llmInputVideos};',
].join('\n'), context);

const controls = context.controls;

// Break caught: structural compatibility must permit LLM feedback and result-producing nodes as LLM sources.
assert.equal(controls.canConnect('llm', 'video'), true);
context.connections.push({id:'llm-video', from:'llm', to:'video'});
assert.equal(controls.canConnect('video', 'llm'), true);

for (const sourceId of ['generator','msgen','comfy','ltx','video','rh','motion','output']) {
    assert.equal(controls.canConnect(sourceId, 'llm'), true, `${sourceId} should connect to LLM`);
}

assert.equal(controls.canConnect('video', 'video'), false);

// Break caught: a valid structural link must also deliver each generator's media to the LLM payload.
context.connections = [
    {id:'generator-llm', from:'generator', to:'llm'},
    {id:'msgen-llm', from:'msgen', to:'llm'},
    {id:'comfy-llm', from:'comfy', to:'llm'},
    {id:'ltx-llm', from:'ltx', to:'llm'},
    {id:'video-llm', from:'video', to:'llm'},
    {id:'rh-llm', from:'rh', to:'llm'},
    {id:'depth-llm', from:'motion', to:'llm', fromPort:'depth'},
    {id:'pose-llm', from:'motion', to:'llm', fromPort:'pose'},
];
assert.deepEqual(
    Array.from(controls.llmInputImages(allNodes.find(node => node.id === 'llm'))),
    ['/assets/generator.png', '/assets/msgen.png', '/assets/comfy.png']
);
assert.deepEqual(
    Array.from(controls.llmInputVideos(allNodes.find(node => node.id === 'llm'))),
    ['/assets/ltx.mp4', '/assets/video.mp4', '/assets/rh.mp4', '/assets/depth.mp4', '/assets/pose.mp4']
);

// Break caught: cleanup removes only malformed and exact-duplicate links, retaining feedback and distinct named outputs.
context.connections = [
    {id:'forward', from:'llm', to:'video'},
    {id:'feedback', from:'video', to:'llm'},
    {id:'feedback-copy', from:'video', to:'llm'},
    {id:'depth', from:'motion', to:'llm', fromPort:'depth'},
    {id:'pose', from:'motion', to:'llm', fromPort:'pose'},
    {id:'self', from:'llm', to:'llm'},
    {id:'orphan', from:'missing', to:'llm'},
];
controls.sanitizeConnections();
assert.deepEqual(
    JSON.parse(JSON.stringify(context.connections.map(link => link.id))),
    ['forward','feedback','depth','pose']
);

console.log('Canvas generator-to-LLM connection tests passed');
