const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '..', 'static', 'js', 'canvas.js'), 'utf8');

function productionFunction(name){
    const asyncStart = source.indexOf(`async function ${name}(`);
    const start = asyncStart >= 0 ? asyncStart : source.indexOf(`function ${name}(`);
    assert.notEqual(start, -1, `canvas.js should define ${name}`);
    const nextFunction = source.indexOf('\nfunction ', start + 1);
    const nextAsyncFunction = source.indexOf('\nasync function ', start + 1);
    const candidates = [nextFunction, nextAsyncFunction].filter(index => index >= 0);
    const next = candidates.length ? Math.min(...candidates) : source.length;
    return source.slice(start, next);
}

const llm = {
    id:'llm-1',
    type:'llm',
    mode:'node',
    outputText:'LLM generated RunningHub prompt',
};
const rh = {
    id:'rh-1',
    type:'rh',
    inputs:[],
    rhParams:{'25::prompt':{value:''}},
};
const promptField = {
    nodeId:'25',
    fieldName:'prompt',
    fieldType:'STRING',
    fieldValue:'',
};
const promptContext = {
    rh,
    promptField,
    nodes:[llm, rh],
    connections:[{id:'c-1', from:llm.id, to:rh.id}],
    CANVAS_MEDIA_OUTPUT_TYPES:[],
    generatedImageRefs:() => [],
    mediaKindForNode:() => 'image',
    outputUrlValue:() => '',
    mediaKindForOutputItem:() => 'image',
    outputImageName:() => '',
    renderLoopPrompt:() => '',
    loopInputImageRefs:() => [],
    loopCount:() => 1,
    tr:() => '',
    loopContext:null,
    imageRefsOnly:() => [],
    videoRefsOnly:() => [],
    audioRefsOnly:() => [],
    rhParamKey:(nodeId, fieldName) => `${nodeId}::${fieldName}`,
    rhFieldKind:field => field.fieldName === 'prompt' ? 'text' : String(field.fieldType || '').toLowerCase(),
    rhActiveFields:() => [promptField],
    rhFieldIndexes:() => ({}),
    rhCurrentKind:() => 'app',
    rhDefaultValue:field => field.fieldValue || '',
    rhRandomEnabled:() => false,
    rhFieldRole:field => field.fieldName === 'prompt' ? 'prompt' : 'text',
    rhCoerceFieldOption:(_field, value) => value,
};
vm.createContext(promptContext);
vm.runInContext([
    productionFunction('generatorSources'),
    productionFunction('orderedSources'),
    productionFunction('rhMediaSources'),
    productionFunction('rhFieldValue'),
    'this.readPrompt = () => rhFieldValue(rh, promptField, rhMediaSources(rh));',
].join('\n'), promptContext);

assert.equal(
    promptContext.readPrompt(),
    llm.outputText,
    'a legacy empty RunningHub prompt value must not block connected LLM text',
);
rh.rhParams['25::prompt'].value = 'Manual override';
assert.equal(
    promptContext.readPrompt(),
    'Manual override',
    'a non-empty manual RunningHub prompt must still override connected text',
);

let syncGeneratorInputsCalls = 0;
let refreshGeneratorInputViewsCalls = 0;
const refreshContext = {
    nodes:[{
        id:'llm-2',
        type:'llm',
        mode:'node',
        userInput:'input',
        outputText:'',
        running:false,
    }],
    cascadeTargetIdFromOptions:() => '',
    llmInputText:() => 'input',
    callCanvasLLM:async () => 'fresh downstream prompt',
    refreshNodes:() => {},
    syncGeneratorInputs:() => { syncGeneratorInputsCalls += 1; },
    refreshGeneratorInputViews:() => { refreshGeneratorInputViewsCalls += 1; },
    scheduleSave:() => {},
    isCascadeAbortError:() => false,
    alert:() => {},
    tr:key => key,
};
vm.createContext(refreshContext);
vm.runInContext(
    `${productionFunction('runLLMNode')}\nthis.run = runLLMNode;`,
    refreshContext,
);

(async () => {
    await refreshContext.run('llm-2');
    assert.equal(
        refreshGeneratorInputViewsCalls,
        1,
        'a completed LLM run must refresh connected generator and RunningHub prompt views',
    );
    console.log('RunningHub LLM prompt forwarding tests passed');
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
