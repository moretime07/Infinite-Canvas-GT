const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '../static/js/download-settings.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '../static/download-settings.html'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));

async function harness({settings, canEdit = true, saveError = ''} = {}) {
    const elements = new Map();
    const requests = [];
    const get = id => {
        if (!elements.has(id)) elements.set(id, {
            checked:false, disabled:false, value:'', textContent:'', dataset:{}, handlers:{},
            addEventListener(name, callback) { this.handlers[name] = callback; },
            reportValidity() { return true; },
        });
        return elements.get(id);
    };
    vm.runInNewContext(source, {
        document:{getElementById:get},
        fetch:async (url, options) => {
            const payload = options.body ? JSON.parse(options.body) : null;
            requests.push({url, options, payload});
            if (options.method === 'GET') return {ok:true, json:async () => ({can_edit:canEdit,
                settings:settings || {enabled:true, upload_enabled:false, protocol:'socks5', host:'127.0.0.1', port:7890}})};
            if (saveError) return {ok:false, status:502, json:async () => ({detail:saveError})};
            return {ok:true, json:async () => ({message:'已保存', settings:payload})};
        },
    });
    await tick();
    return {get, requests, submit:async () => {
        get('proxyForm').handlers.submit({preventDefault(){}});
        await tick();
    }};
}

test('legacy settings preserve download proxy and default new upload switch off', async () => {
    const h = await harness();
    assert.equal(h.get('proxyEnabled').checked, true);
    assert.equal(h.get('proxyUploadEnabled').checked, false);
    assert.equal(h.get('proxyProtocol').value, 'socks5');
    assert.equal(h.get('proxyPort').value, 7890);
    assert.match(h.get('proxyStatus').textContent, /视频下载代理：已启用；素材上传代理：未启用/);
});

test('old running backend gets a restart hint and still accepts download settings', async () => {
    const h = await harness({settings:{enabled:true, protocol:'socks5', host:'127.0.0.1', port:7890}});
    assert.equal(h.get('proxyUploadEnabled').disabled, true);
    assert.match(h.get('proxyStatus').textContent, /重启后台/);
    await h.submit();
    assert.equal(Object.hasOwn(h.requests.at(-1).payload, 'upload_enabled'), false);
    assert.equal(h.requests.at(-1).payload.enabled, true);
});

test('upload can be saved independently without changing configured endpoint', async () => {
    const h = await harness();
    h.get('proxyEnabled').checked = false;
    h.get('proxyUploadEnabled').checked = true;
    await h.submit();
    const save = h.requests.at(-1);
    assert.equal(save.options.method, 'PUT');
    assert.equal(save.options.headers['X-Infinite-Canvas-Download-Proxy'], '1');
    assert.deepEqual(save.payload, {enabled:false, upload_enabled:true, protocol:'socks5', host:'127.0.0.1', port:7890});
    assert.equal(h.get('proxyFields').disabled, false);
});

test('saved upload-only configuration reloads and is used for connectivity test', async () => {
    const h = await harness({settings:{enabled:false, upload_enabled:true, protocol:'http', host:'localhost', port:10808}});
    assert.equal(h.get('proxyEnabled').checked, false);
    assert.equal(h.get('proxyUploadEnabled').checked, true);
    h.get('proxyTest').handlers.click();
    await tick();
    assert.match(h.requests.at(-1).url, /\/test$/);
    assert.equal(h.requests.at(-1).payload.upload_enabled, true);
    assert.equal(h.requests.at(-1).payload.enabled, false);
    assert.equal(h.requests.at(-1).options.method, 'POST');
});

test('LAN client cannot save or test server proxy', async () => {
    const h = await harness({canEdit:false});
    await h.submit();
    h.get('proxyTest').handlers.click();
    await tick();
    assert.equal(h.requests.length, 1);
    assert.equal(h.get('proxyFields').disabled, true);
    assert.match(h.get('proxyStatus').textContent, /局域网客户端不能修改/);
});

test('failed save reports error and re-enables controls', async () => {
    const h = await harness({saveError:'代理设置保存失败'});
    await h.submit();
    assert.equal(h.get('proxyStatus').dataset.kind, 'error');
    assert.equal(h.get('proxyStatus').textContent, '代理设置保存失败');
    assert.equal(h.get('proxyFields').disabled, false);
});

test('settings page documents exact scope and privacy of uploads', () => {
    assert.match(html, /id="proxyUploadEnabled"/);
    assert.match(html, /Litterbox \/ Temp.sh/);
    assert.match(html, /独立开关/);
    assert.match(html, /不会静默切换直连/);
    assert.match(html, /知道素材直链的人可访问/);
    assert.match(html, /后台运行在 5080/);
    assert.match(html, /不保证上传服务可用/);
});
