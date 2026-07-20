const assert = require('node:assert/strict');

global.ProviderDefaults = require('../static/js/provider-defaults.js');
const mode = require('../static/js/canvas-provider-mode.js');

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
