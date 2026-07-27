const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '..', 'static', 'js', 'canvas.js'), 'utf8');
const providerMode = require('../static/js/canvas-provider-mode.js');
const providerDefaults = require('../static/js/provider-defaults.js');

function productionFunction(name){
    const asyncStart = source.indexOf(`async function ${name}(`);
    const syncStart = source.indexOf(`function ${name}(`);
    const start = asyncStart === -1 ? syncStart : asyncStart;
    assert.notEqual(start, -1, `canvas.js should define ${name}`);
    const next = source.slice(start + 1).search(/\n(?:async )?function /);
    return source.slice(start, next === -1 ? source.length : start + 1 + next);
}

const orangeProvider = {
    id:'orange', name:'Orange', enabled:true,
    base_url:'https://api.aig-ai.com/v1',
    video_base_url:'https://vg-api.aig-ai.com/v1',
    video_models:['gemini-omni-flash-preview']
};
const unrelatedProvider = {
    id:'unrelated', name:'Unrelated', enabled:true,
    base_url:'https://example.test/v1', video_models:['gemini-omni-flash-preview']
};
const lookalikeProvider = {
    id:'lookalike', name:'Lookalike', enabled:true,
    base_url:'https://api.aig-ai.com.evil.test/v1', video_models:['gemini-omni-flash-preview']
};

const sandbox = {
    URL,
    apiProviders:[orangeProvider, unrelatedProvider, lookalikeProvider],
    defaultApiProviders:() => [],
    ProviderDefaults:providerDefaults,
    CanvasProviderMode:providerMode,
    escapeHtml:value => String(value),
    tr:key => key,
    trf:(key, values={}) => `${key}:${JSON.stringify(values)}`,
    CANVAS_REFERENCE_IMAGE_MAX:8,
    isVideoUrl:() => false,
    isAudioUrl:() => false,
    isTextUrl:() => false,
    nodes:[], alerts:[], fetchCalls:[],
    cascadeTargetIdFromOptions:() => '',
    generatorSources:node => node.sources || [],
    orderedSources:(_node, sources) => sources,
    openRouterVideoReferenceState:() => ({conflict:false}),
    outputForNode:() => null,
    uid:() => 'pending-1',
    runSnapshot:node => ({node:{apiProvider:node.apiProvider, model:node.model}}),
    makePendingForRun:() => ({}),
    refreshRunNodes:() => {}, scheduleSave:() => {},
    waitCanvasVideoTaskResult:async () => ({videos:['/assets/output/video.mp4']}),
    collectRunMeta:() => ({runMs:0}), resultMediaUrls:result => result.videos || [], outputUrlValue:value => value,
    requestMetaFromResult:() => ({}), appendOutputImages:() => {}, mergeGeneratedOutputs:() => {}, addGenerationLog:() => {},
    isCascadeAbortError:() => false, langIsEn:() => false,
    alert:message => sandbox.alerts.push(String(message)),
    cascadeFetch:async (url, options={}) => {
        sandbox.fetchCalls.push({url, body:JSON.parse(options.body || '{}')});
        return {ok:true, json:async () => ({videos:['/assets/output/video.mp4']})};
    }
};
vm.createContext(sandbox);
vm.runInContext([
    productionFunction('uniqueModels'),
    productionFunction('preferredProviderId'),
    productionFunction('canvasProviderMode'),
    productionFunction('videoProviderModeOptions'),
    productionFunction('syncCanvasNodeProvider'),
    productionFunction('syncDefaultCanvasNodeProvider'),
    productionFunction('unresolvedDefaultCanvasNodeError'),
    productionFunction('stopUnresolvedDefaultCanvasRun'),
    productionFunction('resolveCanvasNodeRequest'),
    productionFunction('preflightCanvasNodeRequest'),
    productionFunction('videoApiProviders'),
    productionFunction('resolveVideoProviderId'),
    productionFunction('videoProviderOptions'),
    productionFunction('providerVideoModels'),
    productionFunction('videoModelOptions'),
    productionFunction('mediaKindForRef'),
    productionFunction('imageRefsOnly'),
    productionFunction('videoRefsOnly'),
    productionFunction('tempShUploadedUrlForNode'),
    productionFunction('applyUploadedUrlToRefs'),
    productionFunction('manualVideoUrlForNode'),
    productionFunction('audioRefsOnly'),
    productionFunction('isOminiLinkProvider'),
    productionFunction('omniFlashVideoValidationError'),
    productionFunction('runVideoNode'),
    'this.controls = {isOminiLinkProvider, omniFlashVideoValidationError, videoProviderOptions, videoModelOptions, runVideoNode};'
].join('\n'), sandbox);

const {controls} = sandbox;

for(const url of [
    'https://api.aig-ai.com/v1', 'https://vg-api.aig-ai.com/v1',
    'https://api.ominilink.ai/v1', 'https://vg-api.ominilink.ai/v1'
]) assert.equal(controls.isOminiLinkProvider({base_url:url}), true, `${url} must be recognized`);
for(const url of [
    'https://portal.ominilink.ai/', 'https://api.aig-ai.com.evil.test/v1',
    'https://notominilink.ai/v1', 'not a url'
]) assert.equal(controls.isOminiLinkProvider({base_url:url}), false, `${url} must be rejected`);
assert.equal(controls.isOminiLinkProvider({video_base_url:'https://vg-api.aig-ai.com/v1'}), true);

assert.match(controls.videoProviderOptions('orange'), /value="orange"/);
assert.match(controls.videoModelOptions('gemini-omni-flash-preview', 'orange'), /value="gemini-omni-flash-preview"/);

async function runCase({providerId='orange', duration=6, refs=[], manualVideo='', cascade=false}){
    const node = {
        id:'video-1', type:'video', providerMode:'fixed', apiProvider:providerId,
        model:'gemini-omni-flash-preview', duration,
        manualVideoUrls:manualVideo ? [manualVideo] : [],
        sources:[{prompt:'A moving landscape', refs}]
    };
    sandbox.nodes = [node];
    sandbox.alerts = [];
    sandbox.fetchCalls = [];
    let thrown = '';
    try { await controls.runVideoNode(node.id, cascade ? {cascade:true} : {}); }
    catch(error) { thrown = error.message || String(error); }
    return {node, alerts:sandbox.alerts, fetchCalls:sandbox.fetchCalls, thrown};
}

(async () => {
    const valid = await runCase({duration:'6'});
    assert.equal(valid.fetchCalls.length, 1, 'valid OminiLink input must reach the real task fetch boundary once');
    assert.equal(valid.fetchCalls[0].url, '/api/canvas-video');
    assert.equal(valid.fetchCalls[0].body.provider_id, 'orange');
    assert.equal(valid.fetchCalls[0].body.model, 'gemini-omni-flash-preview');
    assert.equal(valid.fetchCalls[0].body.duration, 6, 'the serialized duration must be normalized once');
    assert.equal(valid.node.apiProvider, 'orange', 'the fixed provider selection must persist on the node');
    assert.equal(valid.node.model, 'gemini-omni-flash-preview', 'the selected model must persist on the node');

    const manual = await runCase({duration:'6', manualVideo:'https://cdn.example.test/input.mp4'});
    assert.equal(manual.fetchCalls.length, 1, 'a valid manual-video request must reach the fetch boundary once');
    assert.deepEqual(manual.fetchCalls[0].body.videos, ['https://cdn.example.test/input.mp4']);
    assert.equal(manual.fetchCalls[0].body.duration, 6);

    for(const providerId of ['unrelated', 'lookalike']){
        const unchanged = await runCase({
            providerId, duration:2,
            refs:[{kind:'image', url:'/assets/input/image.png'}, {kind:'video', url:'/assets/input/video.mp4'}]
        });
        assert.equal(unchanged.fetchCalls.length, 1, `${providerId} must retain existing behavior for the same model`);
        assert.equal(unchanged.alerts.length, 0, `${providerId} must not receive the OminiLink preflight`);
    }

    const invalidCases = [
        ['duration below range', {duration:2}, /\u89c6\u9891\u65f6\u957f.*3.*10/],
        ['duration above range', {duration:11}, /\u89c6\u9891\u65f6\u957f.*3.*10/],
        ['non-numeric duration', {duration:'six'}, /\u89c6\u9891\u65f6\u957f.*3.*10/],
        ['non-finite duration', {duration:Infinity}, /\u89c6\u9891\u65f6\u957f.*3.*10/],
        ['audio reference', {refs:[{kind:'audio', url:'/assets/input/audio.mp3'}]}, /\u4e0d\u652f\u6301\u97f3\u9891/],
        ['mixed image and video', {refs:[{kind:'image', url:'/assets/input/image.png'}, {kind:'video', url:'/assets/input/video.mp4'}]}, /\u4e0d\u80fd\u540c\u65f6/],
        ['image plus manual video', {refs:[{kind:'image', url:'/assets/input/image.png'}], manualVideo:'https://cdn.example.test/input.mp4'}, /\u4e0d\u80fd\u540c\u65f6/],
        ['two images', {refs:[{kind:'image', url:'/assets/input/one.png'}, {kind:'image', url:'/assets/input/two.png'}]}, /\u53ea\u652f\u6301\u4e00\u5f20\u53c2\u8003\u56fe/],
        ['two videos', {refs:[{kind:'video', url:'/assets/input/one.mp4'}, {kind:'video', url:'/assets/input/two.mp4'}]}, /\u53ea\u652f\u6301\u4e00\u4e2a\u53c2\u8003\u89c6\u9891/]
    ];
    for(const [name, input, expected] of invalidCases){
        const result = await runCase(input);
        assert.equal(result.fetchCalls.length, 0, `${name} must make zero task/network requests`);
        assert.match(result.alerts.join('\n'), expected, `${name} must show a Chinese validation error`);
    }

    const cascade = await runCase({duration:'six', cascade:true});
    assert.equal(cascade.fetchCalls.length, 0, 'cascade validation must stop before fetch');
    assert.match(cascade.thrown, /\u89c6\u9891\u65f6\u957f.*3.*10/);

    const runVideoNode = productionFunction('runVideoNode');
    assert.match(runVideoNode, /body:JSON\.stringify\(requestPayload\)/, 'the validated canonical payload must be handed directly to fetch');
    assert.ok(runVideoNode.indexOf('omniFlashVideoValidationError(') < runVideoNode.indexOf("body:JSON.stringify(requestPayload)"));
    console.log('canvas OminiLink Omni Flash tests passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
