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

const imageUrl = '/assets/generated.png';
const videoUrl = '/assets/generated.mp4';
const allNodes = [
    {id:'image', type:'image', url:'/assets/input.png'},
    {id:'prompt', type:'prompt', text:'prompt'},
    {id:'loop', type:'loop', imageInput:true, videoInput:true, showPrompt:true},
    {id:'group', type:'group', items:[]},
    {id:'prompt-group', type:'promptGroup', items:[]},
    {id:'generator', type:'generator', generatedOutputs:[{url:imageUrl, kind:'image'}]},
    {id:'msgen', type:'msgen', generatedOutputs:[{url:imageUrl, kind:'image'}]},
    {id:'comfy', type:'comfy', generatedOutputs:[{url:imageUrl, kind:'image'}]},
    {id:'ltx', type:'ltxDirector', generatedOutputs:[{url:videoUrl, kind:'video'}]},
    {id:'video', type:'video', generatedOutputs:[{url:videoUrl, kind:'video'}]},
    {id:'rh', type:'rh', generatedOutputs:[{url:videoUrl, kind:'video'}]},
    {id:'motion', type:'motionExtract', depthEnabled:true, poseEnabled:true},
    {id:'output', type:'output', images:[{url:videoUrl, kind:'video'}]},
    {id:'llm', type:'llm', outputText:'analysis'},
];

const context = {
    nodes:allNodes,
    connections:[],
    CANVAS_GENERATOR_TYPES:['generator', 'msgen', 'comfy', 'ltxDirector', 'video', 'rh'],
    CANVAS_MEDIA_OUTPUT_TYPES:['video', 'rh'],
    wouldCreateGeneratorCycle:() => false,
};
vm.createContext(context);
vm.runInContext([
    productionFunction('nodeOutputPorts'),
    productionFunction('normalizedFromPort'),
    productionFunction('connectionKey'),
    productionFunction('nodeCanReceive'),
    productionFunction('nodeCanEmit'),
    productionFunction('canConnect'),
    productionFunction('sanitizeConnections'),
    'this.canConnect = canConnect;',
    'this.sanitizeConnections = sanitizeConnections;',
].join('\n'), context);

const controls = context;

// Break caught: structural compatibility must permit LLM feedback and result-producing nodes as LLM sources.
assert.equal(controls.canConnect('llm', 'video'), true);
context.connections.push({id:'llm-video', from:'llm', to:'video'});
assert.equal(controls.canConnect('video', 'llm'), true);

for (const sourceId of ['generator','msgen','comfy','ltx','video','rh','motion','output']) {
    assert.equal(controls.canConnect(sourceId, 'llm'), true, `${sourceId} should connect to LLM`);
}

assert.equal(controls.canConnect('video', 'video'), false);

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
