const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

global.ProviderDefaults = require('../static/js/provider-defaults.js');
const mode = require('../static/js/canvas-provider-mode.js');
const source = fs.readFileSync(path.resolve(__dirname, '..', 'static', 'js', 'canvas.js'), 'utf8');

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

assert.match(source, /addGeneratorNode[\s\S]*providerMode\s*:\s*['"]default['"]/);
assert.match(source, /addLLMNode[\s\S]*providerMode\s*:\s*['"]default['"]/);
assert.match(source, /addVideoNode[\s\S]*providerMode\s*:\s*['"]default['"]/);
assert.match(source, /鐠虹喖娈㈡妯款吇 API/);
assert.match(source, /CanvasProviderMode\.DEFAULT_VALUE/);
assert.match(source, /CanvasProviderMode\.select/);

const imageNode = {providerMode:'fixed', apiProvider:'fallback'};
const imageDefaultTransition = mode.select(imageNode, mode.DEFAULT_VALUE);
imageNode.providerMode = imageDefaultTransition.providerMode;
if(imageDefaultTransition.providerMode === 'fixed') imageNode.apiProvider = imageDefaultTransition.requestedId;
assert.equal(imageNode.providerMode, 'default');
assert.equal(imageNode.apiProvider, 'fallback');
assert.notEqual(imageNode.apiProvider, mode.DEFAULT_VALUE);

const llmNode = {providerMode:'default', llmProvider:'chat-only'};
const llmFixedTransition = mode.select(llmNode, 'fallback');
llmNode.providerMode = llmFixedTransition.providerMode;
if(llmFixedTransition.providerMode === 'fixed') llmNode.llmProvider = llmFixedTransition.requestedId;
assert.equal(llmNode.providerMode, 'fixed');
assert.equal(llmNode.llmProvider, 'fallback');
assert.notEqual(llmNode.llmProvider, mode.DEFAULT_VALUE);

console.log('canvas-follow-default-provider: passed');
