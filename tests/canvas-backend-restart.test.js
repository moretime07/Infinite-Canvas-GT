const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const jsSource = fs.readFileSync(path.resolve(__dirname, '..', 'static', 'js', 'canvas.js'), 'utf8');
const htmlSource = fs.readFileSync(path.resolve(__dirname, '..', 'static', 'canvas.html'), 'utf8');

function productionFunction(name){
    const asyncStart = jsSource.indexOf(`async function ${name}(`);
    const syncStart = jsSource.indexOf(`function ${name}(`);
    const start = asyncStart === -1 ? syncStart : asyncStart;
    assert.notEqual(start, -1, `canvas.js should define ${name}`);
    const next = jsSource.slice(start + 1).search(/\n(?:async )?function /);
    return jsSource.slice(start, next === -1 ? jsSource.length : start + 1 + next);
}

assert.match(htmlSource, /id="backendRestartMenuSection"[^>]*hidden/);
assert.match(htmlSource, /id="backendRestartMenuBtn"/);
assert.match(htmlSource, /onclick="restartCanvasBackend\(\)"/);

const hostContext = {};
vm.createContext(hostContext);
vm.runInContext(
    `${productionFunction('isLocalRestartHost')}; this.isLocalRestartHost = isLocalRestartHost;`,
    hostContext
);

assert.equal(hostContext.isLocalRestartHost('localhost'), true);
assert.equal(hostContext.isLocalRestartHost('127.0.0.1'), true);
assert.equal(hostContext.isLocalRestartHost('::1'), true);
assert.equal(hostContext.isLocalRestartHost('[::1]'), true);
assert.equal(hostContext.isLocalRestartHost('::ffff:127.0.0.1'), true);
assert.equal(hostContext.isLocalRestartHost('192.168.2.88'), false);

let statusCalls = 0;
const pollingContext = {
    fetch: async () => {
        statusCalls += 1;
        const instance_id = statusCalls === 1 ? 'old-instance' : 'new-instance';
        return {ok:true, json:async () => ({ok:true, instance_id})};
    },
    setTimeout: callback => callback(),
    Date,
    Error,
};
vm.createContext(pollingContext);
vm.runInContext(
    `${productionFunction('waitForBackendInstance')}; this.waitForBackendInstance = waitForBackendInstance;`,
    pollingContext
);

(async () => {
    const result = await pollingContext.waitForBackendInstance(
        'old-instance',
        {timeoutMs:1000, intervalMs:0}
    );
    assert.equal(result.instance_id, 'new-instance');
    assert.equal(statusCalls, 2);
    console.log('Canvas backend restart tests passed');
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
