const assert = require('node:assert/strict');
const ProviderDefaults = require('../static/js/provider-defaults.js');

const providers = [
    {id:'modelscope', name:'ModelScope', enabled:true, primary:false, image_models:['ms-image'], chat_models:['ms-chat'], video_models:[]},
    {id:'custom-api', name:'openrouter', base_url:'https://openrouter.ai/api/v1', enabled:true, primary:true, image_models:['or-image'], chat_models:['or-chat'], video_models:['or-video']},
    {id:'lingjing', name:'Lingjing', enabled:true, primary:false, image_models:['lj-image'], chat_models:['lj-chat'], video_models:['lj-video']}
];

assert.equal(ProviderDefaults.pickProvider(providers, {capability:'image_models'}).id, 'custom-api');
assert.equal(ProviderDefaults.pickProvider(providers, {capability:'chat_models'}).id, 'custom-api');
assert.equal(ProviderDefaults.pickProvider(providers, {capability:'video_models'}).id, 'custom-api');
assert.equal(ProviderDefaults.pickProvider(providers, {capability:'chat_models', requestedId:'lingjing'}).id, 'lingjing');
assert.equal(ProviderDefaults.pickModel(providers[1], 'image_models', '').id, 'or-image');

const noOpenRouterVideo = providers.map(item => item.id === 'custom-api' ? {...item, video_models:[]} : item);
assert.equal(ProviderDefaults.pickProvider(noOpenRouterVideo, {capability:'video_models'}).id, 'lingjing');
assert.equal(ProviderDefaults.pickProvider(providers, {capability:'image_models', excludeIds:['custom-api','modelscope']}).id, 'lingjing');

assert.equal(ProviderDefaults.isOpenRouter({name:'Not OpenRouter Proxy'}), false, 'OpenRouter-like names must not be treated as OpenRouter');
assert.equal(ProviderDefaults.isOpenRouter({base_url:'https://notopenrouter.ai/api/v1'}), false, 'lookalike hosts must not be treated as OpenRouter');
assert.equal(ProviderDefaults.isOpenRouter({base_url:'https://openrouter.ai/api/v1'}), true, 'the OpenRouter host should be recognized');
assert.equal(ProviderDefaults.isOpenRouter({name:'  OpenRouter  '}), true, 'the exact normalized OpenRouter name should be recognized');
const lookalikeProviders = [
    {id:'ordinary', name:'Ordinary', enabled:true, image_models:['ordinary-image']},
    {id:'lookalike', name:'Not OpenRouter Proxy', base_url:'https://notopenrouter.ai/api/v1', enabled:true, image_models:['lookalike-image']},
];
assert.equal(ProviderDefaults.pickProvider(lookalikeProviders, {capability:'image_models'}).id, 'ordinary', 'lookalikes must not gain OpenRouter precedence');
console.log('provider-defaults: passed');
