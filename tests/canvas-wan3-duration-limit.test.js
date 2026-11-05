const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const source = fs.readFileSync(path.resolve(__dirname, '..', 'static/js/canvas.js'), 'utf8');
const start = source.indexOf('function isMaasWan3ProviderModel(');
const end = source.indexOf('\nfunction omniFlashSafePublicHttpsUri(', start + 1);
assert.ok(start >= 0 && end > start);

function load(providers) {
    const sandbox = { apiProviders: providers, window: { location: { href: 'http://localhost/canvas.html' } }, URL };
    vm.createContext(sandbox);
    vm.runInContext(source.slice(start, end), sandbox);
    return sandbox;
}

test('MaaS Wan 3 nodes are recognized by exact model and workspace host', () => {
    const sandbox = load([
        { id: 'official', base_url: 'https://workspace.cn-hongkong.maas.aliyuncs.com/compatible-mode/v1' },
        { id: 'proxy', base_url: 'http://47.114.42.170/v1' },
    ]);
    assert.equal(sandbox.isMaasWan3VideoNode({ apiProvider: 'official', model: 'wan3.0-video' }), true);
    assert.equal(sandbox.isMaasWan3VideoNode({ apiProvider: 'official', model: 'wan3.0-video-prime' }), true);
    assert.equal(sandbox.isMaasWan3VideoNode({ apiProvider: 'official', model: 'wan2.7-i2v' }), false);
    assert.equal(sandbox.isMaasWan3VideoNode({ apiProvider: 'proxy', model: 'wan3.0-video' }), false);
    assert.equal(sandbox.isMaasWan3ProviderModel({base_url:'https://evilmaas.aliyuncs.com'}, 'wan3.0-video'), false);
});

test('the Wan 3 duration input is capped at 30 seconds without changing legacy providers', () => {
    const start = source.indexOf('<input class="setting-input video-duration"');
    const marker = source.indexOf('durationSelect.oninput', start);
    assert.ok(start >= 0 && marker > start);
    const markup = source.slice(start, marker + 600);
    assert.match(markup, /max="\$\{isMaasWan3VideoNode\(node\) \? 30 : 60\}"/);
    assert.match(markup, /const videoDurationLimit = isMaasWan3VideoNode\(node\) \? 30 : 60/);
});
