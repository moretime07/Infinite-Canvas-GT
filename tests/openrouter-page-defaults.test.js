const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const read = name => fs.readFileSync(path.resolve(__dirname, '..', 'static', name), 'utf8');
const online = read('online.html');
const chat = read('gpt-chat.html');

for(const [name, source] of [['online.html', online], ['gpt-chat.html', chat]]){
    assert.match(source, /\/static\/js\/provider-defaults\.js/, `${name} should load provider defaults`);
}
assert.match(online, /ProviderDefaults\.pickProvider\([^)]*capability:\s*['"]image_models['"]/, 'Online Image should select by image capability');
assert.match(chat, /hasSavedChatProvider/, 'GPT Chat should distinguish saved chat state');
assert.match(chat, /hasSavedImageProvider/, 'GPT Chat should distinguish saved image state');
assert.match(chat, /capability:\s*['"]chat_models['"]/, 'GPT Chat should select a chat-capable default');
assert.match(chat, /capability:\s*['"]image_models['"]/, 'GPT Chat should select an image-capable default');

const chatScript = [...chat.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].at(-1)[1];
const storage = new Map();
const channels = [];
const providers = [
    {id:'custom-api', name:'OpenRouter', primary:true, enabled:true, chat_models:['or-chat'], image_models:['or-image']},
    {id:'saved-chat', name:'Saved Chat', enabled:true, chat_models:['saved-chat-model'], image_models:[]},
    {id:'saved-image', name:'Saved Image', enabled:true, chat_models:[], image_models:['saved-image-model']},
];
const element = {
    addEventListener(){},
    classList:{add(){}, remove(){}, contains:() => false, toggle(){}},
    style:{setProperty(){}},
    querySelector:() => null,
    querySelectorAll:() => [],
    appendChild(){},
    value:'',
};
const context = {
    console,
    crypto:{randomUUID:() => 'test-user'},
    localStorage:{
        getItem:key => storage.get(key) || null,
        setItem:(key, value) => storage.set(key, String(value)),
        removeItem:key => storage.delete(key),
    },
    lucide:{createIcons(){}},
    document:{getElementById:() => element, addEventListener(){}, title:'', body:element},
    requestAnimationFrame:callback => callback(),
    fetch:async url => {
        assert.equal(url, '/api/config');
        return {json:async () => ({api_providers:providers, chat_models:['or-chat'], image_models:['or-image']})};
    },
    ProviderDefaults:require('../static/js/provider-defaults.js'),
    BroadcastChannel:class {
        constructor(){ channels.push(this); }
    },
};
context.window = context;
context.window.addEventListener = () => {};
vm.runInNewContext(`${chatScript}\nsetMode = () => {}; renderProviderControls = () => {}; updateModelLabel = () => {}; globalThis.chatPageTest = { loadConfig, setProvider, state:() => ({provider, activeImageProvider}) };`, context);

(async () => {
    await context.chatPageTest.loadConfig();
    assert.equal(context.chatPageTest.state().provider, 'custom-api', 'fresh chat state should default chat capability to OpenRouter');
    assert.equal(context.chatPageTest.state().activeImageProvider, 'custom-api', 'fresh chat state should default image capability to OpenRouter');

    context.chatPageTest.setProvider('saved-chat', 'chat');
    context.chatPageTest.setProvider('saved-image', 'image');
    assert.equal(context.chatPageTest.state().provider, 'saved-chat', 'explicit chat selection should take effect');
    assert.equal(context.chatPageTest.state().activeImageProvider, 'saved-image', 'explicit image selection should take effect');

    await channels[0].onmessage({data:{type:'providers-changed'}});
    assert.equal(context.chatPageTest.state().provider, 'saved-chat', 'provider refresh should preserve a valid chat selection');
    assert.equal(context.chatPageTest.state().activeImageProvider, 'saved-image', 'provider refresh should preserve a valid image selection');
    console.log('openrouter-page-defaults: passed');
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
