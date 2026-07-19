const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const source = fs.readFileSync(path.resolve(__dirname, '..', 'static', 'online.html'), 'utf8');

assert.match(
    source,
    /ProviderDefaults\.pickProvider\(apiProviders,\s*\{\s*capability:\s*['"]image_models['"],\s*requestedId:\s*provider\s*\}\)\?\.id\s*\|\|\s*provider\s*\|\|\s*['"]comfly['"]/,
    'Online Image should choose its default through the shared image-capable provider selector'
);
assert.match(
    source,
    /if\(!providers\.some\(p\s*=>\s*p\.id\s*===\s*provider\)\)\s*provider\s*=\s*providers\[0\]\?\.id/,
    'Unavailable OpenRouter should retain the existing provider fallback'
);

console.log('online-default-openrouter: passed');
