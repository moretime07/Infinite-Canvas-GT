const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.resolve(__dirname, '..', 'static', 'smart-canvas.html'), 'utf8');
const source = fs.readFileSync(path.resolve(__dirname, '..', 'static', 'js', 'smart-canvas.js'), 'utf8');
assert.match(html, /provider-defaults\.js[^]*smart-canvas\.js/, 'Smart Canvas should load provider defaults first');
assert.match(source, /function\s+preferredSmartProviderId\s*\(/, 'Smart Canvas should use one provider adapter');
assert.match(source, /preferredSmartProviderId\(['"]image_models['"]/, 'Smart Canvas should default generic image API');
assert.match(source, /preferredSmartProviderId\(['"]chat_models['"]/, 'Smart Canvas should default chat API');
assert.match(source, /preferredSmartProviderId\(['"]video_models['"]/, 'Smart Canvas should default video API');

const providers = [
    {id:'modelscope', name:'ModelScope', primary:true, enabled:true, image_models:['ms-image'], video_models:['ms-video']},
    {id:'volcengine', name:'Volcengine', primary:true, enabled:true, image_models:['volc-image'], video_models:['volc-video']},
    {id:'openrouter', name:'OpenRouter', enabled:true, image_models:['or-image'], chat_models:['or-chat'], video_models:['or-video']},
    {id:'saved-image', name:'Saved Image', enabled:true, image_models:['saved-image-model']},
    {id:'saved-chat', name:'Saved Chat', enabled:true, chat_models:['saved-chat-model']},
    {id:'saved-video', name:'Saved Video', enabled:true, video_models:['saved-video-model']},
];
const context = {
    apiProviders:providers,
    ProviderDefaults:require('../static/js/provider-defaults.js'),
    clearVolcengineSelectionOutsideVolcengine(){},
    volcengineVideoModels:() => ['volc-video'],
    providerImageModels:id => providers.find(provider => provider.id === id)?.image_models || [],
    providerVideoModels:id => providers.find(provider => provider.id === id)?.video_models || [],
    videoApiProviders:() => providers.filter(provider => provider.enabled !== false && provider.id !== 'volcengine' && provider.video_models?.length),
    isGptImageAutoSizeModel:() => false,
};
context.globalThis = context;
const adapterStart = source.indexOf('function imageProviders');
const adapterEnd = source.indexOf('function volcengineProvider');
const chatStart = source.indexOf('function chatApiProviders');
const chatEnd = source.indexOf('function providerChatModels');
const sanitizeStart = source.indexOf('function sanitizeSmartApiSelection');
const sanitizeEnd = source.indexOf('function modelscopeProvider');
vm.runInNewContext([
    source.slice(adapterStart, adapterEnd),
    source.slice(chatStart, chatEnd),
    source.slice(sanitizeStart, sanitizeEnd),
    'globalThis.smartDefaultsTest = {resolveChatProviderId, sanitizeSmartApiSelection};',
].join('\n'), context);

const fresh = {engine:'api', apiKind:'video', provider_id:'', model:'', videoProvider:'', videoModel:''};
context.smartDefaultsTest.sanitizeSmartApiSelection(fresh);
assert.equal(fresh.provider_id, 'openrouter', 'generic image default should use the image-capable OpenRouter provider');
assert.equal(fresh.videoProvider, 'openrouter', 'generic video default must not fall through to dedicated Volcengine');
assert.equal(context.smartDefaultsTest.resolveChatProviderId(), 'openrouter', 'chat default should be selected independently by chat capability');

const saved = {engine:'api', apiKind:'image', provider_id:'saved-image', model:'saved-image-model', videoProvider:'saved-video', videoModel:'saved-video-model'};
context.smartDefaultsTest.sanitizeSmartApiSelection(saved);
assert.equal(saved.provider_id, 'saved-image', 'valid saved image provider should remain authoritative');
assert.equal(saved.videoProvider, 'saved-video', 'valid saved video provider should remain authoritative');
assert.equal(context.smartDefaultsTest.resolveChatProviderId('saved-chat'), 'saved-chat', 'valid saved chat provider should remain authoritative');

const dedicatedVolcengine = {engine:'volcengine', apiKind:'video', videoProvider:'', videoModel:''};
context.smartDefaultsTest.sanitizeSmartApiSelection(dedicatedVolcengine);
assert.equal(dedicatedVolcengine.engine, 'volcengine', 'dedicated Volcengine mode should remain explicit');
assert.equal(dedicatedVolcengine.videoProvider, 'volcengine', 'dedicated Volcengine mode should keep its provider');
for(const engine of ['modelscope','runninghub','comfy']){
    const dedicated = {engine, apiKind:'video', provider_id:'', videoProvider:''};
    context.smartDefaultsTest.sanitizeSmartApiSelection(dedicated);
    assert.equal(dedicated.engine, engine, `dedicated ${engine} mode should remain unchanged`);
    assert.equal(dedicated.videoProvider, '', `dedicated ${engine} mode should not receive a generic video provider`);
}
console.log('smart-canvas-provider-defaults: passed');
