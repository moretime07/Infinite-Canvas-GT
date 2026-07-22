const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '..', 'static', 'js', 'canvas.js'), 'utf8');

function productionFunction(name){
    const start = source.indexOf(`function ${name}(`);
    assert.notEqual(start, -1, `canvas.js should define ${name}`);
    const next = source.indexOf('\nfunction ', start + 1);
    return source.slice(start, next === -1 ? source.length : next);
}

const context = {
    apiProviders:[
        {id:'openrouter', name:'openrouter', base_url:'https://openrouter.ai/api/v1', video_models:['bytedance/seedance-2.0']},
        {id:'other', name:'Other', base_url:'https://example.com/v1', video_models:['video-a']},
    ],
    defaultApiProviders:() => [],
    ProviderDefaults:require('../static/js/provider-defaults.js'),
    mediaKindForRef:ref => ref.kind,
};
vm.createContext(context);
vm.runInContext(`${productionFunction('openRouterVideoReferenceState')}\nthis.openRouterVideoReferenceState = openRouterVideoReferenceState;`, context);

const sources = [
    {refs:[{kind:'image'}, {kind:'video'}]},
    {refs:[{kind:'audio'}]},
];

assert.deepEqual(
    JSON.parse(JSON.stringify(context.openRouterVideoReferenceState({apiProvider:'openrouter'}, sources))),
    {enabled:true, image:1, video:1, audio:1, conflict:false}
);
assert.equal(context.openRouterVideoReferenceState({apiProvider:'openrouter', useFrameRoles:true}, sources).conflict, true);
assert.equal(context.openRouterVideoReferenceState({apiProvider:'other'}, sources).enabled, false);

assert.match(source, /将提交：图片 \$\{state\.image\} · 视频 \$\{state\.video\} · 音频 \$\{state\.audio\}/);
assert.match(source, /OpenRouter 的首尾帧模式会覆盖视频\/音频参考/);

console.log('canvas OpenRouter video reference tests passed');
