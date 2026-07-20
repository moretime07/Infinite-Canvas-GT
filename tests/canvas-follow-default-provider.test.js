const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

global.ProviderDefaults = require('../static/js/provider-defaults.js');
const mode = require('../static/js/canvas-provider-mode.js');
const source = fs.readFileSync(path.resolve(__dirname, '..', 'static', 'js', 'canvas.js'), 'utf8');

function productionFunction(name){
    const asyncStart = source.indexOf(`async function ${name}(`);
    const start = asyncStart === -1 ? source.indexOf(`function ${name}(`) : asyncStart;
    assert.notEqual(start, -1, `canvas.js should define ${name}`);
    const nextSync = source.indexOf('\nfunction ', start + 1);
    const nextAsync = source.indexOf('\nasync function ', start + 1);
    const next = [nextSync, nextAsync].filter(index => index !== -1).sort((a, b) => a - b)[0] ?? -1;
    return source.slice(start, next === -1 ? source.length : next);
}

const providers = [
    {id:'chat-only', enabled:true, primary:false, chat_models:['chat-a'], image_models:[], video_models:[]},
    {id:'openrouter', enabled:true, primary:true, chat_models:['chat-b'], image_models:['image-b'], video_models:['video-b']},
    {id:'fallback', enabled:true, primary:false, chat_models:['chat-c'], image_models:['image-c'], video_models:['video-c']}
];

assert.equal(mode.mode({}), 'fixed');
assert.equal(mode.mode({providerMode:'default'}), 'default');

const following = {providerMode:'default', apiProvider:'fallback', model:'image-b'};
assert.deepEqual(
    mode.resolve(following, providers, {capability:'image_models', providerField:'apiProvider'}),
    {providerMode:'default', providerId:'openrouter', model:'image-b', changed:true}
);

const fixed = {apiProvider:'fallback', model:'image-c'};
assert.equal(
    mode.resolve(fixed, providers, {capability:'image_models', providerField:'apiProvider'}).providerId,
    'fallback'
);

const changedPrimaryProviders = providers.map(provider => ({
    ...provider,
    primary:provider.id === 'fallback'
}));
const mixed = [
    {type:'generator', providerMode:'default', apiProvider:'fallback', model:'image-c'},
    {type:'generator', providerMode:'fixed', apiProvider:'fallback', model:'image-c'},
    {type:'generator', apiProvider:'fallback', model:'image-c'}
];
const mixedResolved = mixed.map(node => mode.resolve(
    node,
    changedPrimaryProviders,
    {capability:'image_models', providerField:'apiProvider'}
));
assert.equal(mixedResolved[0].changed, false);
assert.equal(mixedResolved[0].providerId, 'fallback');
assert.equal(mixedResolved[1].changed, false);
assert.equal(mixedResolved[1].providerId, 'fallback');
assert.equal(mixedResolved[2].changed, false);
assert.equal(mixedResolved[2].providerId, 'fallback');

const nextPrimaryProviders = providers.map(provider => ({
    ...provider,
    primary:provider.id === 'openrouter'
}));
const afterPrimaryChange = mixed.map(node => mode.resolve(
    node,
    nextPrimaryProviders,
    {capability:'image_models', providerField:'apiProvider'}
));
assert.equal(afterPrimaryChange[0].changed, true);
assert.equal(afterPrimaryChange[0].providerId, 'openrouter');
assert.equal(afterPrimaryChange[1].changed, false);
assert.equal(afterPrimaryChange[1].providerId, 'fallback');
assert.equal(afterPrimaryChange[2].changed, false);
assert.equal(afterPrimaryChange[2].providerId, 'fallback');

assert.deepEqual(mode.select(following, mode.DEFAULT_VALUE), {providerMode:'default', requestedId:''});
assert.deepEqual(mode.select(following, 'fallback'), {providerMode:'fixed', requestedId:'fallback'});

const incompatiblePrimary = [
    {id:'primary-chat', enabled:true, primary:true, chat_models:['chat-primary'], image_models:[]},
    {id:'image-provider', enabled:true, primary:false, chat_models:[], image_models:['image-fallback']}
];
assert.equal(
    mode.resolve(
        {providerMode:'default', apiProvider:'primary-chat', model:''},
        incompatiblePrimary,
        {capability:'image_models', providerField:'apiProvider'}
    ).providerId,
    'image-provider'
);

assert.equal(
    mode.resolve(
        {apiProvider:'fallback', model:'incompatible-model'},
        providers,
        {capability:'image_models', providerField:'apiProvider'}
    ).model,
    'image-c'
);

assert.match(productionFunction('addGeneratorNode'), /providerMode\s*:\s*['"]default['"]/);
assert.match(productionFunction('addLLMNode'), /providerMode\s*:\s*['"]default['"]/);
assert.match(productionFunction('addVideoNode'), /providerMode\s*:\s*['"]default['"]/);
assert.match(source, /鐠虹喖娈㈡妯款吇 API/);
assert.match(source, /CanvasProviderMode\.DEFAULT_VALUE/);
assert.match(source, /CanvasProviderMode\.select/);

['applyChatProviderSelection', 'applyImageProviderSelection', 'applyVideoProviderSelection'].forEach(name => {
    assert.match(source, new RegExp(`function\\s+${name}\\s*\\(`), `canvas.js should define ${name}`);
});
assert.match(productionFunction('renderLLMBody'), /providerSelect\.onchange[\s\S]*applyChatProviderSelection\(node,\s*e\.target\.value\)/);
assert.match(productionFunction('renderGeneratorBody'), /providerSelect\.onchange[\s\S]*applyImageProviderSelection\(node,\s*e\.target\.value\)/);
assert.match(productionFunction('renderVideoBody'), /providerSelect\.onchange[\s\S]*applyVideoProviderSelection\(node,\s*e\.target\.value\)/);
assert.match(productionFunction('syncFollowingDefaultCanvasNodes'), /node\.providerMode\s*!==\s*['"]default['"]/);
assert.match(productionFunction('refreshCanvasConfigFromSettings'), /const\s+changed\s*=\s*syncFollowingDefaultCanvasNodes\(\)/);
assert.match(productionFunction('refreshCanvasConfigFromSettings'), /if\s*\(changed\s*&&\s*canvas\)\s*scheduleSave\(\)/);
assert.match(productionFunction('renderNode'), /syncDefaultCanvasNodeProvider\(node\)/);
assert.match(productionFunction('renderGeneratorBody'), /prepareCanvasNodeForRender\(node\)/);
assert.match(productionFunction('renderLLMBody'), /prepareCanvasNodeForRender\(node\)/);
assert.match(productionFunction('renderVideoBody'), /prepareCanvasNodeForRender\(node\)/);
assert.match(productionFunction('saveCanvas'), /logs\s*:\s*serializableCanvasLogs\(\)/);

[
    ['runGenerator', 'gen'],
    ['runGeneratorLegacy', 'gen'],
    ['runVideoNode', 'node'],
    ['callCanvasLLM', 'node']
].forEach(([name, variable]) => {
    assert.match(
        productionFunction(name),
        new RegExp(`syncDefaultCanvasNodeProvider\\(${variable}\\)`),
        `${name} should resolve a following-default node before building requests`
    );
});

const sandbox = {
    CanvasProviderMode:mode,
    ProviderDefaults:global.ProviderDefaults,
    apiProviders:providers,
    defaultApiProviders:() => providers,
    preferredProviderId:capability => global.ProviderDefaults.pickProvider(providers, {capability})?.id || '',
    tr:key => ({
        'canvas.followDefaultApi':'Follow default API',
        'canvas.noApiProviders':'No API providers',
        'canvas.noApiProvidersHint':'No API providers. Add one in API Settings.',
        'canvas.noModelsHint':'No models. Add some in API Settings.'
    })[key] || key,
    escapeHtml:value => String(value),
    chatApiProviders:() => sandbox.apiProviders.filter(provider => provider.enabled !== false && provider.chat_models.length),
    imageApiProviders:() => sandbox.apiProviders.filter(provider => provider.enabled !== false && provider.image_models.length),
    videoApiProviders:() => sandbox.apiProviders.filter(provider => provider.enabled !== false && provider.video_models.length),
    uniqueModels:list => [...new Set((list || []).filter(Boolean))],
    allImageModels:id => id === 'removed-image' ? [] : ['image-b'],
    allChatModels:() => ['chat-b'],
    providerChatModels:id => id === 'disabled-chat' ? [] : ['chat-b'],
    providerVideoModels:id => id === 'removed-video' ? [] : ['video-b'],
    resolveImageModel:model => model || 'image-b',
    resolveChatModel:model => model || 'chat-b',
    resolveChatProviderId:id => id || 'openrouter',
    resolveImageProviderId:id => id || 'openrouter',
    resolveVideoProviderId:id => id || 'openrouter',
    uid:prefix => `${prefix}-1`,
    runPlatformLabel:() => 'API',
    runTaskLabel:() => 'model',
    canvas:null
};
const executableNames = [
    'chatApiProviders', 'videoApiProviders', 'providerChatModels', 'providerVideoModels',
    'allChatModels', 'resolveChatModel', 'addLLMNode', 'addVideoNode',
    'runGenerator', 'runGeneratorLegacy', 'runVideoNode', 'callCanvasLLM',
    'preferredProviderId', 'resolveChatProviderId', 'resolveImageProviderId', 'resolveVideoProviderId',
    'canvasProviderMode', 'chatProviderModeOptions', 'imageProviderModeOptions', 'videoProviderModeOptions',
    'syncCanvasNodeProvider', 'syncFollowingDefaultCanvasNodes', 'syncDefaultCanvasNodeProvider',
    'unresolvedDefaultCanvasNodeError', 'stopUnresolvedDefaultCanvasRun', 'prepareCanvasNodeForRender',
    'normalizeCanvasProviderSentinels', 'serializableCanvasNode', 'serializableCanvasLogs', 'runSnapshot', 'addGenerationLog',
    'followDefaultOption',
    'applyCanvasProviderSelection', 'applyChatProviderSelection',
    'applyImageProviderSelection', 'applyVideoProviderSelection',
    'chatProviderOptions', 'providerOptions', 'videoProviderOptions',
    'imageModelOptions', 'chatModelOptions', 'videoModelOptions'
];
vm.runInNewContext(
    `${executableNames.map(productionFunction).join('\n')}\nthis.controls = {${executableNames.join(',')}};`,
    sandbox
);
const controls = sandbox.controls;

sandbox.addNode = node => node;
sandbox.chatModels = ['fabricated-chat-model'];
sandbox.localChatModels = [];
sandbox.hasManagedChatModels = false;
sandbox.videoModels = ['fabricated-video-model'];
sandbox.DEFAULT_VIDEO_MODELS = ['fabricated-default-video-model'];

const noCompatibleProviders = [
    {id:'image-only', enabled:true, primary:true, chat_models:[], image_models:['image-only-model'], video_models:[]}
];
sandbox.apiProviders = noCompatibleProviders;
const noCompatibleLlm = controls.addLLMNode({x:10, y:20});
const noCompatibleVideo = controls.addVideoNode({x:30, y:40});
assert.deepEqual({
    chatProviders:Array.from(controls.chatApiProviders(), provider => provider.id),
    videoProviders:Array.from(controls.videoApiProviders(), provider => provider.id),
    llm:{provider:noCompatibleLlm.llmProvider, model:noCompatibleLlm.model},
    video:{provider:noCompatibleVideo.apiProvider, model:noCompatibleVideo.model}
}, {
    chatProviders:[],
    videoProviders:[],
    llm:{provider:'', model:''},
    video:{provider:'', model:''}
}, 'configured providers with no compatible chat/video capability must keep default nodes unresolved');
assert.match(controls.chatModelOptions('', '', {providerMode:'default'}), /value="" disabled selected/, 'an unresolved default LLM node should show the existing no-model hint');
assert.match(controls.videoModelOptions('', ''), /value="" disabled selected/, 'an unresolved default video node should show the existing no-model hint');
const noCompatibleChatProviders = controls.chatProviderOptions('', {providerMode:'default'});
assert.match(noCompatibleChatProviders, /value="__default__" selected/, 'unresolved LLM controls should retain the follow-default option');
assert.match(noCompatibleChatProviders, /value="" disabled[^>]*>No API providers</, 'unresolved LLM controls should include the no-provider hint');
const noCompatibleVideoProviders = controls.videoProviderOptions('', {providerMode:'default'});
assert.match(noCompatibleVideoProviders, /value="__default__" selected/, 'unresolved video controls should retain the follow-default option');
assert.match(noCompatibleVideoProviders, /value="" disabled[^>]*>No API providers</, 'unresolved video controls should include the no-provider hint');
sandbox.apiProviders = providers;

assert.notEqual(controls.resolveImageProviderId(mode.DEFAULT_VALUE), mode.DEFAULT_VALUE);
assert.notEqual(controls.resolveChatProviderId(mode.DEFAULT_VALUE), mode.DEFAULT_VALUE);
assert.notEqual(controls.resolveVideoProviderId(mode.DEFAULT_VALUE), mode.DEFAULT_VALUE);

sandbox.nodes = mixed.map(node => ({...node}));
sandbox.apiProviders = nextPrimaryProviders;
assert.equal(controls.syncFollowingDefaultCanvasNodes(), true);
assert.equal(sandbox.nodes[0].apiProvider, 'openrouter');
assert.equal(sandbox.nodes[0].model, 'image-b');
assert.equal(sandbox.nodes[1].apiProvider, 'fallback');
assert.equal(sandbox.nodes[1].model, 'image-c');
assert.equal(sandbox.nodes[2].apiProvider, 'fallback');
assert.equal(sandbox.nodes[2].model, 'image-c');
assert.equal(controls.syncFollowingDefaultCanvasNodes(), false);

const legacyFixed = {type:'generator', apiProvider:'fallback', model:'image-c'};
assert.equal(controls.syncDefaultCanvasNodeProvider(legacyFixed), false);
assert.deepEqual(legacyFixed, {type:'generator', apiProvider:'fallback', model:'image-c'});

const unavailableFixedNodes = [
    {type:'generator', providerMode:'fixed', apiProvider:'removed-image', model:'removed-image-model'},
    {type:'llm', llmProvider:'disabled-chat', model:'disabled-chat-model'},
    {type:'video', providerMode:'fixed', apiProvider:'removed-video', model:'removed-video-model'}
];
sandbox.nodes = unavailableFixedNodes.map(node => ({...node}));
assert.equal(controls.syncFollowingDefaultCanvasNodes(), false);
assert.deepEqual(sandbox.nodes, unavailableFixedNodes);

unavailableFixedNodes.forEach(node => {
    const rendered = {...node};
    controls.prepareCanvasNodeForRender(rendered);
    assert.deepEqual(rendered, node, 'render preparation must not rewrite fixed or missing-mode nodes');
});

assert.match(controls.providerOptions('removed-image', unavailableFixedNodes[0]), /value="removed-image" selected/);
assert.match(controls.chatProviderOptions('disabled-chat', unavailableFixedNodes[1]), /value="disabled-chat" selected/);
assert.match(controls.videoProviderOptions('removed-video', unavailableFixedNodes[2]), /value="removed-video" selected/);
assert.match(controls.imageModelOptions('removed-image-model', 'removed-image'), /value="removed-image-model" selected/);
assert.match(controls.chatModelOptions('disabled-chat-model', 'disabled-chat'), /value="disabled-chat-model" selected/);
assert.match(controls.videoModelOptions('removed-video-model', 'removed-video'), /value="removed-video-model" selected/);

sandbox.apiProviders = [{id:'chat-only', enabled:true, chat_models:['chat-a'], image_models:[], video_models:[]}];
assert.match(controls.providerOptions('removed-image', unavailableFixedNodes[0]), /value="removed-image" selected/);
sandbox.apiProviders = nextPrimaryProviders;

[
    {type:'generator', apiProvider:mode.DEFAULT_VALUE, model:'image-c'},
    {type:'llm', llmProvider:mode.DEFAULT_VALUE, model:'chat-c'},
    {type:'video', apiProvider:mode.DEFAULT_VALUE, model:'video-c'}
].forEach(node => {
    const serialized = controls.serializableCanvasNode(node);
    assert.notEqual(serialized.apiProvider, mode.DEFAULT_VALUE);
    assert.notEqual(serialized.llmProvider, mode.DEFAULT_VALUE);
});
assert.equal(
    JSON.stringify(controls.serializableCanvasNode(unavailableFixedNodes[0])),
    JSON.stringify(unavailableFixedNodes[0]),
    'serialization must not normalize legitimate unavailable fixed values'
);

const circularEditor = {};
circularEditor.self = circularEditor;
const serializedWithRuntimeState = controls.serializableCanvasNode({
    type:'generator',
    apiProvider:'fallback',
    model:'image-c',
    _ltxEditor:circularEditor
});
assert.equal(serializedWithRuntimeState._ltxEditor, undefined);

const malformedRun = controls.runSnapshot(
    {type:'generator', providerMode:'fixed', apiProvider:mode.DEFAULT_VALUE, model:'image-c'},
    'prompt'
);
assert.doesNotMatch(JSON.stringify(malformedRun), /__default__/);
assert.equal(malformedRun.node.apiProvider, 'openrouter');

sandbox.canvas = {logs:[]};
controls.addGenerationLog({
    run:{
        nodeType:'generator',
        node:{type:'generator', apiProvider:mode.DEFAULT_VALUE, model:'image-c'},
        request:{provider_id:mode.DEFAULT_VALUE},
        prompt:'prompt',
        refs:[]
    }
});
assert.doesNotMatch(JSON.stringify(sandbox.canvas.logs), /__default__/);
assert.doesNotMatch(JSON.stringify(controls.serializableCanvasLogs([
    {nodeType:'video', request:{provider_id:mode.DEFAULT_VALUE}, nested:{provider:mode.DEFAULT_VALUE}}
])), /__default__/);

function assertDefaultTransition(adapter, providerField, initialProvider, initialModel, expectedProvider, expectedModel){
    const node = {providerMode:'fixed', [providerField]:initialProvider, model:initialModel};
    adapter(node, mode.DEFAULT_VALUE);
    assert.equal(node.providerMode, 'default');
    assert.equal(node[providerField], expectedProvider);
    assert.notEqual(node[providerField], mode.DEFAULT_VALUE);
    assert.equal(node.model, expectedModel);
}
function assertFixedTransition(adapter, providerField, initialProvider, initialModel, expectedModel){
    const node = {providerMode:'default', [providerField]:initialProvider, model:initialModel};
    adapter(node, 'fallback');
    assert.equal(node.providerMode, 'fixed');
    assert.equal(node[providerField], 'fallback');
    assert.notEqual(node[providerField], mode.DEFAULT_VALUE);
    assert.equal(node.model, expectedModel);
}

assertDefaultTransition(controls.applyImageProviderSelection, 'apiProvider', 'fallback', 'image-c', 'openrouter', 'image-b');
assertFixedTransition(controls.applyImageProviderSelection, 'apiProvider', 'openrouter', 'image-b', 'image-c');
assertDefaultTransition(controls.applyChatProviderSelection, 'llmProvider', 'fallback', 'chat-c', 'openrouter', 'chat-b');
assertFixedTransition(controls.applyChatProviderSelection, 'llmProvider', 'openrouter', 'chat-b', 'chat-c');
assertDefaultTransition(controls.applyVideoProviderSelection, 'apiProvider', 'fallback', 'video-c', 'openrouter', 'video-b');
assertFixedTransition(controls.applyVideoProviderSelection, 'apiProvider', 'openrouter', 'video-b', 'video-c');

assert.doesNotMatch(controls.providerOptions('fallback'), /__default__/);
assert.doesNotMatch(controls.chatProviderOptions('fallback'), /__default__/);
assert.doesNotMatch(controls.videoProviderOptions('fallback'), /__default__/);
assert.match(controls.providerOptions('openrouter', {providerMode:'default'}), /value="__default__" selected/);
assert.match(controls.chatProviderOptions('openrouter', {providerMode:'default'}), /value="__default__" selected/);
assert.match(controls.videoProviderOptions('openrouter', {providerMode:'default'}), /value="__default__" selected/);

async function unresolvedRunResult(node, invoke){
    sandbox.apiProviders = [{id:'empty', enabled:true, primary:true, chat_models:[], image_models:[], video_models:[]}];
    sandbox.nodes = [node];
    sandbox.networkCalls = 0;
    sandbox.runErrors = [];
    sandbox.fetch = async () => {
        sandbox.networkCalls += 1;
        throw new Error('network attempted');
    };
    sandbox.cascadeFetch = sandbox.fetch;
    sandbox.createCanvasImageTask = async () => {
        sandbox.networkCalls += 1;
        throw new Error('network attempted');
    };
    sandbox.showErrorModal = message => sandbox.runErrors.push(String(message));
    sandbox.alert = message => sandbox.runErrors.push(String(message));
    let thrown = '';
    try {
        await invoke(node);
    } catch(error) {
        thrown = error.message || String(error);
    }
    return {
        networkCalls:sandbox.networkCalls,
        errors:[...sandbox.runErrors, thrown].filter(Boolean),
        provider:node.llmProvider ?? node.apiProvider,
        model:node.model
    };
}

sandbox.cascadeTargetIdFromOptions = () => '';
sandbox.generatorSources = () => [];
sandbox.orderedSources = () => [{prompt:'prompt', refs:[]}];
sandbox.imageRefsOnly = refs => refs || [];
sandbox.videoRefsOnly = refs => refs || [];
sandbox.audioRefsOnly = refs => refs || [];
sandbox.applyUploadedUrlToRefs = refs => refs || [];
sandbox.mediaKindForRef = () => '';
sandbox.outputForNode = () => null;
sandbox.runSnapshot = () => ({nodeType:'test', node:{}});
sandbox.generatorSizeForRun = async () => '1024x1024';
sandbox.normalizedImageQuality = () => '';
sandbox.nowMs = () => 0;
sandbox.refreshRunNodes = () => {};
sandbox.scheduleSave = () => {};
sandbox.addGenerationLog = () => {};
sandbox.collectRunMetas = () => [];
sandbox.collectRunMeta = () => ({runMs:0, run:{}});
sandbox.isCascadeAbortError = () => false;
sandbox.makePendingForRun = () => ({});
sandbox.manualVideoUrlForNode = () => '';
sandbox.tempShUploadedUrlForNode = (_node, url) => url;
sandbox.llmInputImages = () => [];
sandbox.llmInputVideos = () => [];
sandbox.CANVAS_REFERENCE_IMAGE_MAX = 8;

(async () => {
    const expectedError = 'No API providers. Add one in API Settings.';
    const results = {
        generator:await unresolvedRunResult(
            {id:'gen-run', type:'generator', providerMode:'default', apiProvider:'stale', model:'stale'},
            node => controls.runGenerator(node.id, {cascade:true})
        ),
        generatorLegacy:await unresolvedRunResult(
            {id:'gen-legacy-run', type:'generator', providerMode:'default', apiProvider:'stale', model:'stale'},
            node => controls.runGeneratorLegacy(node.id, {cascade:true})
        ),
        video:await unresolvedRunResult(
            {id:'video-run', type:'video', providerMode:'default', apiProvider:'stale', model:'stale'},
            node => controls.runVideoNode(node.id, {cascade:true})
        ),
        llm:await unresolvedRunResult(
            {id:'llm-run', type:'llm', providerMode:'default', llmProvider:'stale', model:'stale'},
            node => controls.callCanvasLLM(node, 'prompt')
        )
    };
    Object.entries(results).forEach(([name, result]) => {
        assert.equal(result.networkCalls, 0, `${name} must stop before fetch/request helpers`);
        assert.deepEqual(result.errors, [expectedError], `${name} should surface the existing no-provider error`);
        assert.equal(result.provider, '', `${name} should retain an empty resolved provider`);
        assert.equal(result.model, '', `${name} should retain an empty resolved model`);
    });
    assert.equal(
        controls.unresolvedDefaultCanvasNodeError(
            {providerMode:'default', llmProvider:'configured-chat', model:''},
            'llmProvider'
        ),
        'No models. Add some in API Settings.',
        'an explicit default node with a provider but no model should surface the existing no-model error'
    );

    const fixedLlm = await unresolvedRunResult(
        {id:'fixed-llm-run', type:'llm', providerMode:'fixed', llmProvider:'fixed-chat', model:'fixed-model'},
        node => controls.callCanvasLLM(node, 'prompt')
    );
    assert.equal(fixedLlm.networkCalls, 1, 'fixed LLM request behavior must not be stopped by the explicit-default guard');
    assert.equal(fixedLlm.provider, 'fixed-chat');
    assert.equal(fixedLlm.model, 'fixed-model');

    const legacyVideo = await unresolvedRunResult(
        {id:'legacy-video-run', type:'video', apiProvider:'legacy-video', model:'legacy-model'},
        node => controls.runVideoNode(node.id, {cascade:true})
    );
    assert.equal(legacyVideo.networkCalls, 1, 'missing-mode legacy video request behavior must not be stopped by the explicit-default guard');
    assert.equal(legacyVideo.provider, 'legacy-video');
    assert.equal(legacyVideo.model, 'legacy-model');
    console.log('canvas-follow-default-provider: passed');
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
