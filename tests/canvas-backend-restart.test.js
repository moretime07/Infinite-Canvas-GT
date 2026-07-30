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
assert.equal(hostContext.isLocalRestartHost(new URL('http://[::ffff:127.0.0.1]:3000/').hostname), true);
assert.equal(hostContext.isLocalRestartHost('192.168.2.88'), false);

let statusCalls = 0;
const pollingContext = {
    fetch: async () => {
        statusCalls += 1;
        const instance_id = statusCalls === 1 ? 'old-instance' : 'new-instance';
        return {ok:true, json:async () => ({ok:true, instance_id})};
    },
    setTimeout: (callback, delay) => delay === 0 ? callback() : setTimeout(callback, delay),
    clearTimeout,
    AbortController,
    Date,
    Error,
};
vm.createContext(pollingContext);
vm.runInContext(
    `${productionFunction('fetchWithTimeout')}; ${productionFunction('waitForBackendInstance')}; this.waitForBackendInstance = waitForBackendInstance;`,
    pollingContext
);

async function rejectsWithin(promise, timeoutMs){
    let timer;
    try {
        return await Promise.race([
            promise.then(
                () => { throw new Error('expected promise to reject'); },
                error => error
            ),
            new Promise((_, reject) => {
                timer = setTimeout(() => reject(new Error('promise did not settle')), timeoutMs);
            }),
        ]);
    } finally {
        clearTimeout(timer);
    }
}

(async () => {
    const timeoutContext = {
        fetch: () => new Promise(() => {}),
        AbortController,
        setTimeout,
        clearTimeout,
        Date,
        Error,
    };
    vm.createContext(timeoutContext);
    vm.runInContext(
        `${productionFunction('fetchWithTimeout')}; this.fetchWithTimeout = fetchWithTimeout;`,
        timeoutContext
    );
    const requestTimeoutError = await rejectsWithin(
        timeoutContext.fetchWithTimeout('/api/system/status', {}, 15), 100
    );
    assert.match(requestTimeoutError.message, /timed out/i);

    const waitTimeoutContext = {
        fetch: () => new Promise(() => {}),
        AbortController,
        setTimeout,
        clearTimeout,
        Date,
        Error,
        tr: key => key === 'canvas.restartBackendTimeout' ? 'Restart timed out' : key,
    };
    vm.createContext(waitTimeoutContext);
    vm.runInContext(
        `${productionFunction('fetchWithTimeout')}; ${productionFunction('waitForBackendInstance')}; this.waitForBackendInstance = waitForBackendInstance;`,
        waitTimeoutContext
    );
    const waitTimeoutError = await rejectsWithin(
        waitTimeoutContext.waitForBackendInstance('old-instance', {timeoutMs:20, intervalMs:0}), 150
    );
    assert.equal(waitTimeoutError.message, 'Restart timed out');

    const result = await pollingContext.waitForBackendInstance(
        'old-instance',
        {timeoutMs:1000, intervalMs:0}
    );
    assert.equal(result.instance_id, 'new-instance');
    assert.equal(statusCalls, 2);

    const restartRequests = [];
    const restartContext = {
        backendRestartInFlight: false,
        backendRestartMenuBtn: null,
        isLocalRestartHost: () => true,
        closeCreateMenu: () => {},
        setStatus: () => {},
        showErrorModal: () => {},
        responseErrorMessage: async () => 'failed',
        waitForBackendInstance: async () => ({instance_id:'new-instance'}),
        tr: key => key,
        fetchWithTimeout: async (...args) => {
            restartRequests.push(args);
            return {ok:true, json:async () => ({instance_id:'old-instance'})};
        },
        window: {confirm: () => true, location: {reload: () => {}}},
        Error,
    };
    vm.createContext(restartContext);
    vm.runInContext(
        `${productionFunction('restartCanvasBackend')}; this.restartCanvasBackend = restartCanvasBackend;`,
        restartContext
    );
    await restartContext.restartCanvasBackend();
    assert.equal(restartRequests.length, 1);
    assert.equal(restartRequests[0][1].headers['X-Infinite-Canvas-Restart'], '1');
    console.log('Canvas backend restart tests passed');
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
