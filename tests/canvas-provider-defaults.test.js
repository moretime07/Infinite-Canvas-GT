const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(path.resolve(__dirname, '..', 'static', 'canvas.html'), 'utf8');
const source = fs.readFileSync(path.resolve(__dirname, '..', 'static', 'js', 'canvas.js'), 'utf8');
assert.match(html, /provider-defaults\.js[^]*canvas\.js/, 'Canvas should load provider defaults before canvas.js');
assert.match(source, /function\s+preferredProviderId\s*\(/, 'Canvas should expose one preferred-provider adapter');
assert.match(source, /addGeneratorNode[^]*preferredProviderId\(['"]image_models['"]/, 'New image nodes should use the image default');
assert.match(source, /addLLMNode[^]*preferredProviderId\(['"]chat_models['"]/, 'New LLM nodes should use the chat default');
assert.match(source, /addVideoNode[^]*preferredProviderId\(['"]video_models['"]/, 'New video nodes should use the video default');
console.log('canvas-provider-defaults: passed');
