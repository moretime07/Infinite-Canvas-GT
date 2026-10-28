const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '../static/js/canvas.js'), 'utf8');
function fn(name){
    const start = source.indexOf(`async function ${name}(`) >= 0 ? source.indexOf(`async function ${name}(`) : source.indexOf(`function ${name}(`);
    assert.notEqual(start, -1);
    const next = source.slice(start + 1).search(/\n(?:async )?function /);
    return source.slice(start, next < 0 ? source.length : start + 1 + next);
}
const provider = {base_url:'https://www.aiide.com/v1'};
function harness({consent=true, publicBase=false, uploadFailure=false}={}){
    const ctx = vm.createContext({URL, Date, Map, events:[], refreshNodes:() => {}, scheduleSave:() => {},
        mediaKindForRef:ref => ref.kind || 'image', responseErrorMessage:async () => 'invalid upload URL'});
    ctx.confirm = message => {ctx.events.push({type:'consent', message}); return consent;};
    ctx.applyTempShUrlToCanvasRef = () => {throw new Error('Must not modify source nodes');};
    ctx.fetch = async (url, options) => {
        ctx.events.push({type:'upload', url, body:JSON.parse(options.body)});
        if(uploadFailure) throw new Error('upload failed');
        return {ok:true, json:async () => ({url:'https://files.example.com/' + JSON.parse(options.body).url.split('/').pop(), expires:'72h'})};
    };
    ctx.cascadeFetch = async (url, options) => {
        const body = JSON.parse(options.body);
        ctx.events.push({type:'preflight', url, body});
        const local = [];
        const resolve = (value, kind) => {
            if(value.startsWith('/assets/')){
                if(publicBase) return 'https://company.example.com' + value;
                if(!local.some(ref => ref.url === value)) local.push({url:value, kind});
            }
            return value;
        };
        const plan = {images:(body.images || []).map(ref => ({...ref,url:resolve(ref.url,'image')})),
            videos:(body.videos || []).map(url => resolve(url,'video')), audios:(body.audios || []).map(url => resolve(url,'audio')),
            upload_required:local};
        return {ok:true, json:async () => plan};
    };
    vm.runInContext(['isAiideVideoProvider','prepareAiideVideoReferences','cloudMediaLinkExpiresAt',
        'uploadCanvasMediaRefToCloud','tempShUploadedUrlForNode'].map(fn).join('\n'), ctx);
    return ctx;
}
function request(){
    return {model:'doubao-seedance-2-0-260128',prompt:'test',images:[{url:'/assets/input/one.png',role:'first_frame'}],
        videos:['/assets/input/ref.mp4'],audios:['/assets/input/ref.mp3']};
}
test('Aiide local references require consent before any third-party upload', async () => {
    const ctx = harness({consent:false});
    const payload = request();
    await assert.rejects(ctx.prepareAiideVideoReferences({id:'video'}, payload, provider), /已取消上传/);
    assert.deepEqual(ctx.events.map(e => e.type), ['preflight','consent']);
    assert.match(ctx.events[1].message, /Litterbox/);
    assert.match(ctx.events[1].message, /公开直链/);
    assert.equal(payload.images[0].url, '/assets/input/one.png');
});
test('confirmation uploads all media once and preserves roles and original node assets', async () => {
    const ctx = harness();
    const node = {id:'video'};
    const payload = request();
    payload.images.push({...payload.images[0]});
    await ctx.prepareAiideVideoReferences(node, payload, provider);
    assert.deepEqual(ctx.events.map(e => e.type), ['preflight','consent','upload','upload','upload','preflight']);
    assert(ctx.events.filter(e => e.type === 'upload').every(e => e.body.service === 'litterbox'));
    assert.equal(payload.images.length, 2);
    assert.equal(payload.images[0].role, 'first_frame');
    assert.equal(payload.images[0].url, 'https://files.example.com/one.png');
    assert.equal(payload.videos[0], 'https://files.example.com/ref.mp4');
    assert.equal(payload.audios[0], 'https://files.example.com/ref.mp3');
    assert.equal(node.tempShUploading, false);
    assert.equal(node.tempShLinks.length, 3);
    assert.equal(node.tempShLinks[0].source, '/assets/input/one.png');
});
test('own public storage requires neither public-host upload nor confirmation', async () => {
    const ctx = harness({publicBase:true});
    const payload = request();
    await ctx.prepareAiideVideoReferences({id:'video'}, payload, provider);
    assert.deepEqual(ctx.events.map(e => e.type), ['preflight']);
    assert.match(payload.images[0].url, /^https:\/\/company\.example\.com/);
});
test('upload failure stops before generation and does not fall back to another service', async () => {
    const ctx = harness({uploadFailure:true});
    const node = {id:'video'};
    await assert.rejects(ctx.prepareAiideVideoReferences(node, request(), provider), /upload failed/);
    assert.deepEqual(ctx.events.map(e => e.type), ['preflight','consent','upload']);
    assert.equal(node.tempShUploading, false);
});
test('remote links and asset IDs are not uploaded again', async () => {
    const ctx = harness();
    const payload = {images:[{url:'asset://mat_ready'},{url:'https://company.example.com/image.png'}],videos:[],audios:[]};
    await ctx.prepareAiideVideoReferences({id:'video'}, payload, provider);
    assert.deepEqual(ctx.events.map(e => e.type), ['preflight']);
});
test('other providers do not enter this preflight or upload path', async () => {
    const ctx = harness();
    for(const url of ['https://openrouter.ai/api/v1','https://www.aiide.com.evil.test/v1','https://api.ominilink.ai/v1']){
        await ctx.prepareAiideVideoReferences({id:'video'}, request(), {base_url:url});
    }
    assert.equal(ctx.events.length, 0);
});
test('expired temporary links are not reused', () => {
    const ctx = harness();
    const url = '/assets/input/one.png';
    const node = {tempShLinks:[{source:url,url:'https://files.example.com/expired.png',expiresAt:Date.now()-1}]};
    assert.equal(ctx.tempShUploadedUrlForNode(node,url), url);
    node.tempShLinks[0].expiresAt = Date.now()+60000;
    assert.equal(ctx.tempShUploadedUrlForNode(node,url), 'https://files.example.com/expired.png');
    assert(ctx.cloudMediaLinkExpiresAt('1h') < Date.now()+3600000);
    assert(ctx.cloudMediaLinkExpiresAt('3 days') > Date.now()+48*3600000);
});
test('generation happens after preflight and its error handler prevents submission', () => {
    const run = fn('runVideoNode');
    assert(run.indexOf('await prepareAiideVideoReferences(') < run.indexOf('await createCanvasVideoTask('));
    assert(run.indexOf('try {') < run.indexOf('await prepareAiideVideoReferences('));
});
