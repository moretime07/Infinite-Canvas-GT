const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const test = require('node:test');
const settings = fs.readFileSync(path.join(__dirname, '../static/js/api-settings.js'), 'utf8');
const canvas = fs.readFileSync(path.join(__dirname, '../static/js/canvas.js'), 'utf8');
function fn(source, name){
    const start = source.indexOf(`function ${name}(`);
    assert.notEqual(start, -1);
    const next = source.slice(start + 1).search(/\n(?:async )?function /);
    return source.slice(start, next < 0 ? source.indexOf('\nwindow.addEventListener', start) : start + 1 + next);
}
test('VOD status is honest and does not affect other providers', () => {
    const context = vm.createContext({effectiveProviderProtocol:p => p?.protocol});
    vm.runInContext(fn(settings, 'vodModelStatusHtml'), context);
    assert.equal(context.vodModelStatusHtml({protocol:'openai'}, 'x'), '');
    const provider = {protocol:'tencent-vod',model_metadata:{x:{verification:'unverified'}}};
    assert.match(context.vodModelStatusHtml(provider, 'x'), /权限未验证/);
    provider.model_metadata.x.verification = 'verified';
    assert.match(context.vodModelStatusHtml(provider, 'x'), /生成已验证/);
    assert.match(context.vodModelStatusHtml(provider, 'unknown'), /未适配/);
});
test('model discovery keeps video and image categories', () => {
    const body = settings.slice(settings.indexOf('async function probeTencentVod('), settings.indexOf('function vodModelStatusHtml('));
    assert.match(body, /video:new Set\(data.video_models/);
    assert.match(body, /item.model_metadata = data.model_metadata/);
    assert.match(body, /if\(pickModels\) openModelPicker/);
});
test('video UI uses selected provider metadata and preserves old workflow selections', () => {
    const elements = Object.fromEntries(['.video-duration','.video-resolution','.video-aspect'].map(k => [k, {}]));
    let notice;
    elements['.gen-settings'] = {appendChild:x => {notice=x.textContent;}};
    const spec = {resolutions:['768P','1080P'],aspect_ratios:['16:9','9:16'],durations:[6,10],verification:'unverified'};
    const context = vm.createContext({apiProviders:[{id:'vod',protocol:'tencent-vod',model_metadata:{test:spec}}],
        escapeHtml:String, document:{createElement:() => ({})}});
    vm.runInContext(fn(canvas, 'tencentVodVideoSpec') + fn(canvas, 'tencentVodVideoControls'), context);
    const node = {apiProvider:'vod',model:'test',resolution:'720p',duration:5,aspectRatio:'1:1'};
    context.tencentVodVideoControls({querySelector:key => elements[key]}, node);
    assert.match(elements['.video-resolution'].innerHTML, /768P/);
    assert.equal(elements['.video-resolution'].value, '720p');
    assert.equal(elements['.video-duration'].min, 6);
    assert.equal(node.duration, 5);
    assert.match(notice, /账号权限未验证/);
    assert.equal(context.tencentVodVideoSpec({...node,apiProvider:'other'}), null);
});
