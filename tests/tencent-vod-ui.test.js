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
    const spec = {model_name:'wand-vega-video',label:'WAND Vega Video Pro 1.5',resolutions:['720P','1080P'],aspect_ratios:['16:9','9:16'],durations:[4,5,15,30],verification:'unverified',reference_limits:{image:30,video:10,audio:10,total:50},benchmark:'效果对标 SD2 / SD2.5',pricing_note:'720P 及以上约为 80%'};
    const context = vm.createContext({apiProviders:[{id:'vod',protocol:'tencent-vod',model_metadata:{test:spec}}],
        escapeHtml:String, document:{createElement:() => ({})}});
    vm.runInContext(fn(canvas, 'tencentVodVideoSpec') + fn(canvas, 'tencentVodVideoControls'), context);
    const node = {apiProvider:'vod',model:'test',resolution:'720p',duration:5,aspectRatio:'1:1'};
    context.tencentVodVideoControls({querySelector:key => elements[key]}, node);
    assert.match(elements['.video-resolution'].innerHTML, /720P/);
    assert.match(elements['.video-resolution'].innerHTML, /value="720P"/);
    assert.equal(elements['.video-duration'].min, 4);
    assert.equal(elements['.video-duration'].max, 30);
    assert.equal(node.duration, 5);
    assert.match(notice, /账号权限未验证/);
    assert.match(notice, /WAND Vega Video Pro 1\.5/);
    assert.match(notice, /图片 30 \/ 视频 10 \/ 音频 10，总计 50/);
    assert.match(notice, /SD2 \/ SD2\.5/);
    assert.match(notice, /80%/);
    assert.equal(context.tencentVodVideoSpec({...node,apiProvider:'other'}), null);
});
test('saved Vega Lite nodes migrate to canonical IDs before preflight', () => {
    const provider = {id:'vod',protocol:'tencent-vod',video_models:['vod-wand-vega-video-1.0-pro-mini'],video_model_aliases:{'WAND-Vega-Video-1.0-Lite':'vod-wand-vega-video-1.0-pro-mini'}};
    const context = vm.createContext({apiProviders:[provider]});
    vm.runInContext(fn(canvas, 'tencentVodCanonicalVideoModel') + fn(canvas, 'migrateTencentVodVideoNode'), context);
    const node = {type:'video',apiProvider:'vod',model:'WAND-Vega-Video-1.0-Lite'};
    assert.equal(context.migrateTencentVodVideoNode(node), true);
    assert.equal(node.model, 'vod-wand-vega-video-1.0-pro-mini');
    assert.equal(context.migrateTencentVodVideoNode(node), false);
});
test('saved Wan 3.0 nodes migrate beside Vega and stay available', () => {
    const provider = {id:'vod',protocol:'tencent-vod',
        video_models:['vod-wand-vega-video-1.5-pro','vod-wan-video-3.0'],
        video_model_aliases:{'Wan3.0':'vod-wan-video-3.0'},
        model_metadata:{'vod-wand-vega-video-1.5-pro':{label:'WAND Vega Video Pro 1.5'},'vod-wan-video-3.0':{label:'Wan 3.0 Video'}}};
    const context = vm.createContext({apiProviders:[provider],escapeHtml:String});
    vm.runInContext(fn(canvas, 'uniqueModels') + fn(canvas, 'providerVideoModels') + fn(canvas, 'videoModelOptions')
        + fn(canvas, 'tencentVodCanonicalVideoModel') + fn(canvas, 'migrateTencentVodVideoNode'), context);
    const node = {type:'video',apiProvider:'vod',model:'Wan3.0'};
    assert.equal(context.migrateTencentVodVideoNode(node), true);
    assert.equal(node.model, 'vod-wan-video-3.0');
    assert.equal(context.migrateTencentVodVideoNode(node), false);
    const options = context.videoModelOptions(node.model, 'vod');
    assert.match(options, /Wan 3\.0 Video/);
    assert.doesNotMatch(options, /Unavailable/);
    assert.match(options, /WAND Vega Video Pro 1\.5/);
});
