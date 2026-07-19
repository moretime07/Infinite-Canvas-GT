const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(path.resolve(__dirname, '..', 'static', 'smart-canvas.html'), 'utf8');
const source = fs.readFileSync(path.resolve(__dirname, '..', 'static', 'js', 'smart-canvas.js'), 'utf8');
assert.match(html, /provider-defaults\.js[^]*smart-canvas\.js/, 'Smart Canvas should load provider defaults first');
assert.match(source, /function\s+preferredSmartProviderId\s*\(/, 'Smart Canvas should use one provider adapter');
assert.match(source, /preferredSmartProviderId\(['"]image_models['"]/, 'Smart Canvas should default generic image API');
assert.match(source, /preferredSmartProviderId\(['"]chat_models['"]/, 'Smart Canvas should default chat API');
assert.match(source, /preferredSmartProviderId\(['"]video_models['"]/, 'Smart Canvas should default video API');
console.log('smart-canvas-provider-defaults: passed');
