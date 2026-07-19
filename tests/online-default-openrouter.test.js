const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const source = fs.readFileSync(path.resolve(__dirname, '..', 'static', 'online.html'), 'utf8');

assert.match(
    source,
    /let\s+provider\s*=\s*['"]custom-api['"]\s*;/,
    'Online Image should initialize with OpenRouter'
);
assert.match(
    source,
    /if\(!providers\.some\(p\s*=>\s*p\.id\s*===\s*provider\)\)\s*provider\s*=\s*providers\[0\]\?\.id/,
    'Unavailable OpenRouter should retain the existing provider fallback'
);

console.log('online-default-openrouter: passed');
