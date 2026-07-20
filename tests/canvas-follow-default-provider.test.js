const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

global.ProviderDefaults = require('../static/js/provider-defaults.js');
const mode = require('../static/js/canvas-provider-mode.js');
const source = fs.readFileSync(path.resolve(__dirname, '..', 'static', 'js', 'canvas.js'), 'utf8');

function productionFunction(name){
    const start = source.indexOf(`function ${name}(`);
    assert.notEqual(start, -1, `canvas.js should define ${name}`);
    const next = source.indexOf('\nfunction ', start + 1);
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

['runGenerator', 'runGeneratorLegacy', 'runVideoNode', 'callCanvasLLM'].forEach(name => {
    assert.doesNotMatch(
        productionFunction(name),
        /CanvasProviderMode\.DEFAULT_VALUE/,
        `${name} must never put the UI default sentinel in a request`
    );
});

const sandbox = {
    CanvasProviderMode:mode,
    apiProviders:providers,
    defaultApiProviders:() => providers,
    preferredProviderId:capability => global.ProviderDefaults.pickProvider(providers, {capability})?.id || '',
    tr:() => 'Follow default API',
    escapeHtml:value => String(value),
    chatApiProviders:() => providers.filter(provider => provider.chat_models.length),
    imageApiProviders:() => providers.filter(provider => provider.image_models.length),
    videoApiProviders:() => providers.filter(provider => provider.video_models.length),
    resolveChatProviderId:id => id || 'openrouter',
    resolveImageProviderId:id => id || 'openrouter',
    resolveVideoProviderId:id => id || 'openrouter'
};
const executableNames = [
    'canvasProviderMode', 'chatProviderModeOptions', 'imageProviderModeOptions', 'videoProviderModeOptions',
    'syncCanvasNodeProvider', 'syncFollowingDefaultCanvasNodes', 'syncDefaultCanvasNodeProvider',
    'serializableCanvasNode', 'followDefaultOption',
    'applyCanvasProviderSelection', 'applyChatProviderSelection',
    'applyImageProviderSelection', 'applyVideoProviderSelection',
    'chatProviderOptions', 'providerOptions', 'videoProviderOptions'
];
vm.runInNewContext(
    `${executableNames.map(productionFunction).join('\n')}\nthis.controls = {${executableNames.join(',')}};`,
    sandbox
);
const controls = sandbox.controls;

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

[
    {type:'generator', apiProvider:mode.DEFAULT_VALUE, model:'image-c'},
    {type:'llm', llmProvider:mode.DEFAULT_VALUE, model:'chat-c'},
    {type:'video', apiProvider:mode.DEFAULT_VALUE, model:'video-c'}
].forEach(node => {
    const serialized = controls.serializableCanvasNode(node);
    assert.notEqual(serialized.apiProvider, mode.DEFAULT_VALUE);
    assert.notEqual(serialized.llmProvider, mode.DEFAULT_VALUE);
});

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

console.log('canvas-follow-default-provider: passed');
