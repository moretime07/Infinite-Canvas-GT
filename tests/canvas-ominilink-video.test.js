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

const provider = {
    id:'orange',
    name:'Orange',
    enabled:true,
    base_url:'https://api.aig-ai.com/v1',
    video_base_url:'https://vg-api.aig-ai.com/v1',
    video_models:['gemini-omni-flash-preview']
};

const sandbox = {
    URL,
    apiProviders:[provider],
    defaultApiProviders:() => [],
    ProviderDefaults:require('../static/js/provider-defaults.js'),
    CanvasProviderMode:{mode:() => 'fixed'},
    escapeHtml:value => String(value),
    tr:key => key,
    CANVAS_REFERENCE_IMAGE_MAX:8,
    isVideoUrl:() => false,
    isAudioUrl:() => false,
    isTextUrl:() => false,
    nodes:[],
    alerts:[],
    submitted:[],
    fetchCalls:0,
    syncDefaultCanvasNodeProvider:() => false,
    stopUnresolvedDefaultCanvasRun:() => false,
    preflightCanvasNodeRequest:node => ({providerId:node.apiProvider, model:node.model}),
    videoProviderModeOptions:() => ({}),
    cascadeTargetIdFromOptions:() => '',
    generatorSources:() => [],
    orderedSources:(_node, sources) => sources,
    openRouterVideoReferenceState:() => ({conflict:false}),
    outputForNode:() => null,
    uid:() => 'pending-1',
    runSnapshot:() => ({}),
    makePendingForRun:() => ({}),
    refreshRunNodes:() => {},
    scheduleSave:() => {},
    manualVideoUrlForNode:() => '',
    tempShUploadedUrlForNode:(_node, url) => url,
    createCanvasVideoTask:async payload => {
        sandbox.submitted.push(payload);
        return {task_id:'task-1', status:'queued'};
    },
    waitCanvasVideoTaskResult:async () => ({videos:['/assets/output/video.mp4']}),
    collectRunMeta:() => ({runMs:0}),
    resultMediaUrls:result => result.videos || [],
    outputUrlValue:value => value,
    requestMetaFromResult:() => ({}),
    appendOutputImages:() => {},
    mergeGeneratedOutputs:() => {},
    addGenerationLog:() => {},
    isCascadeAbortError:() => false,
    langIsEn:() => false,
    alert:message => sandbox.alerts.push(String(message)),
    fetch:async () => {
        sandbox.fetchCalls += 1;
        throw new Error('network must not be reached');
    }
};
vm.createContext(sandbox);
vm.runInContext([
    productionFunction('uniqueModels'),
    productionFunction('preferredProviderId'),
    productionFunction('videoApiProviders'),
    productionFunction('resolveVideoProviderId'),
    productionFunction('videoProviderOptions'),
    productionFunction('providerVideoModels'),
    productionFunction('videoModelOptions'),
    productionFunction('mediaKindForRef'),
    productionFunction('imageRefsOnly'),
    productionFunction('videoRefsOnly'),
    productionFunction('applyUploadedUrlToRefs'),
    productionFunction('audioRefsOnly'),
    productionFunction('runVideoNode'),
    productionFunction('isOminiLinkProvider'),
    productionFunction('omniFlashVideoValidationError'),
    'this.controls = {isOminiLinkProvider, omniFlashVideoValidationError, videoProviderOptions, videoModelOptions, runVideoNode};'
].join('\n'), sandbox);

const {controls} = sandbox;

for(const url of [
    'https://api.aig-ai.com/v1',
    'https://vg-api.aig-ai.com/v1',
    'https://api.ominilink.ai/v1',
    'https://vg-api.ominilink.ai/v1'
]){
    assert.equal(controls.isOminiLinkProvider({base_url:url}), true, `${url} must be recognized as OminiLink`);
}
for(const url of [
    'https://portal.ominilink.ai/',
    'https://api.aig-ai.com.evil.test/v1',
    'https://notominilink.ai/v1',
    'not a url'
]){
    assert.equal(controls.isOminiLinkProvider({base_url:url}), false, `${url} must not be recognized as OminiLink`);
}
assert.equal(controls.isOminiLinkProvider({video_base_url:'https://vg-api.aig-ai.com/v1'}), true);

const providerMarkup = controls.videoProviderOptions('orange');
const modelMarkup = controls.videoModelOptions('gemini-omni-flash-preview', 'orange');
assert.match(providerMarkup, /value="orange"/, 'the exact OminiLink provider must remain selectable');
assert.match(modelMarkup, /value="gemini-omni-flash-preview"/, 'the exact Omni Flash model must remain selectable');

assert.equal(
    controls.omniFlashVideoValidationError(
        {model:'gemini-omni-flash-preview', duration:6},
        [{kind:'image'}, {kind:'video'}]
    ),
    'Omni Flash \u4e0d\u80fd\u540c\u65f6\u63d0\u4ea4\u56fe\u7247\u548c\u89c6\u9891\u53c2\u8003\u3002'
);
assert.equal(
    controls.omniFlashVideoValidationError({model:'gemini-omni-flash-preview', duration:2}, []),
    'Omni Flash \u89c6\u9891\u65f6\u957f\u5fc5\u987b\u5728 3 \u5230 10 \u79d2\u4e4b\u95f4\u3002'
);
assert.equal(controls.omniFlashVideoValidationError({model:'other-video', duration:2}, []), '');

async function runCase({duration=6, refs=[], cascade=false}){
    const node = {
        id:'video-1',
        type:'video',
        apiProvider:'orange',
        model:'gemini-omni-flash-preview',
        duration,
        sources:[{prompt:'A moving landscape', refs}]
    };
    sandbox.nodes = [node];
    sandbox.alerts = [];
    sandbox.submitted = [];
    sandbox.fetchCalls = 0;
    sandbox.generatorSources = current => current.sources;
    let thrown = '';
    try {
        await controls.runVideoNode(node.id, cascade ? {cascade:true} : {});
    } catch(error) {
        thrown = error.message || String(error);
    }
    return {node, alerts:sandbox.alerts, submitted:sandbox.submitted, fetchCalls:sandbox.fetchCalls, thrown};
}

(async () => {
    const valid = await runCase({duration:6});
    assert.equal(valid.submitted.length, 1, 'a valid Omni Flash request should submit exactly once');
    assert.equal(valid.fetchCalls, 0, 'the task helper, not a direct fetch, handles a valid request');
    assert.equal(valid.submitted[0].provider_id, 'orange', 'the chosen OminiLink provider must be serialized unchanged');
    assert.equal(valid.submitted[0].model, 'gemini-omni-flash-preview', 'the chosen Omni Flash model must be serialized unchanged');

    const invalidCases = [
        ['duration below range', {duration:2}, /\u89c6\u9891\u65f6\u957f.*3.*10/],
        ['duration above range', {duration:11}, /\u89c6\u9891\u65f6\u957f.*3.*10/],
        ['audio reference', {refs:[{kind:'audio', url:'/assets/input/audio.mp3'}]}, /\u4e0d\u652f\u6301\u97f3\u9891/],
        ['mixed image and video', {refs:[{kind:'image', url:'/assets/input/image.png'}, {kind:'video', url:'/assets/input/video.mp4'}]}, /\u4e0d\u80fd\u540c\u65f6/],
        ['two images', {refs:[{kind:'image', url:'/assets/input/one.png'}, {kind:'image', url:'/assets/input/two.png'}]}, /\u53ea\u652f\u6301\u4e00\u5f20\u53c2\u8003\u56fe/],
        ['two videos', {refs:[{kind:'video', url:'/assets/input/one.mp4'}, {kind:'video', url:'/assets/input/two.mp4'}]}, /\u53ea\u652f\u6301\u4e00\u4e2a\u53c2\u8003\u89c6\u9891/]
    ];
    for(const [name, input, expected] of invalidCases){
        const result = await runCase(input);
        assert.equal(result.submitted.length, 0, `${name} must fail before creating a paid task`);
        assert.equal(result.fetchCalls, 0, `${name} must make zero network calls`);
        assert.match(result.alerts.join('\n'), expected, `${name} must show a Chinese validation error`);
    }

    const cascade = await runCase({duration:2, cascade:true});
    assert.equal(cascade.submitted.length, 0, 'cascade validation must fail before creating a paid task');
    assert.equal(cascade.fetchCalls, 0, 'cascade validation must make zero network calls');
    assert.match(cascade.thrown, /\u89c6\u9891\u65f6\u957f.*3.*10/, 'cascade validation must propagate its Chinese error');

    const runVideoNode = productionFunction('runVideoNode');
    assert.ok(
        runVideoNode.indexOf('omniFlashVideoValidationError(') < runVideoNode.indexOf('createCanvasVideoTask('),
        'Omni validation must run before the paid task is created'
    );
    console.log('canvas OminiLink Omni Flash tests passed');
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
