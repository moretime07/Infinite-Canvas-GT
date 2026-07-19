const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

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
console.log('openrouter-page-defaults: passed');
