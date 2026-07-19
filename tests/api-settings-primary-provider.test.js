const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function fakeElement(){
    return {style:{},classList:{add(){},remove(){},toggle(){},contains(){return false;}},addEventListener(){},querySelector(){return null;},querySelectorAll(){return [];},closest(){return null;},setAttribute(){},removeAttribute(){},disabled:false,hidden:false,value:'',textContent:'',innerHTML:'',placeholder:''};
}

async function createState(providerRows){
    const calls = [];
    const messages = [];
    const elements = new Map();
    const document = {body:fakeElement(),getElementById(id){if(!elements.has(id)) elements.set(id,fakeElement());return elements.get(id);},querySelector(){return null;},querySelectorAll(){return [];},addEventListener(){}};
    const window = {addEventListener(){},parent:{postMessage(message){messages.push(message);}},top:{postMessage(message){messages.push(message);}},location:{href:''}};
    const fetch = async (url, options={}) => {
        calls.push({url, options});
        if(url === '/api/providers') return {ok:true,json:async()=>({providers:providerRows})};
        if(url.endsWith('/primary')) return {ok:true,json:async()=>({providers:providerRows.map(item=>({...item,primary:url.includes(item.id)}))})};
        throw new Error(`unexpected fetch ${url}`);
    };
    const context = {document,window,fetch,URL,console,setTimeout,clearTimeout,alert(){},confirm(){return true;}};
    context.globalThis = context;
    const sourcePath = path.resolve(__dirname,'..','static','js','api-settings.js');
    const source = fs.readFileSync(sourcePath,'utf8') + `\n globalThis.__primaryTest={loadProviders,setPrimaryProvider,providerPrimaryIssue,providerPrimaryControl,providerCapabilityBadges,providers:()=>providers,selected:()=>selectedId};`;
    vm.runInNewContext(source,context,{filename:sourcePath});
    await context.__primaryTest.loadProviders();
    return {api:context.__primaryTest,calls,messages,elements};
}

(async()=>{
    const rows = [
        {id:'one',name:'One',enabled:true,primary:true,has_key:true,image_models:['img'],chat_models:[],video_models:[]},
        {id:'two',name:'Two',enabled:true,primary:false,has_key:true,image_models:[],chat_models:['chat'],video_models:['video']},
        {id:'off',name:'Off',enabled:false,primary:false,has_key:true,chat_models:['chat']},
        {id:'empty',name:'Empty',enabled:true,primary:false,has_key:false,chat_models:[]}
    ];
    const state = await createState(rows);
    assert.match(state.api.providerPrimaryControl(rows[0]), /默认/);
    assert.match(state.api.providerPrimaryControl(rows[1]), /设为默认/);
    assert.equal(state.api.providerPrimaryIssue(rows[2]), '平台已停用');
    assert.equal(state.api.providerPrimaryIssue(rows[3]), '未配置密钥');
    assert.match(state.api.providerCapabilityBadges(rows[1]), /对话/);
    assert.match(state.api.providerCapabilityBadges(rows[1]), /视频/);
    const providerMarkup = state.elements.get('providerList').innerHTML;
    assert.match(providerMarkup, /provider-card-shell/);
    assert.match(providerMarkup, /<\/button>\s*<span class="provider-capabilities">[\s\S]*<button class="provider-primary-btn/,
        'the primary control must be a sibling of the provider card button');

    let stopped = 0;
    const ok = await state.api.setPrimaryProvider({preventDefault(){},stopPropagation(){stopped += 1;}}, 'two');
    assert.equal(ok, true);
    assert.equal(stopped, 1);
    assert.equal(state.calls.filter(call=>call.url === '/api/providers/two/primary').length, 1);
    assert.equal(state.calls.find(call=>call.url.endsWith('/primary')).options.method, 'PUT');
    assert.equal(state.calls.find(call=>call.url.endsWith('/primary')).options.body, undefined);
    assert.equal(state.api.providers().filter(item=>item.primary).map(item=>item.id).join(','), 'two');
    assert.equal(state.api.selected(), 'one', 'switching primary must not select another editor card');
    assert.ok(state.messages.some(message=>message?.type === 'studio-api'));
    console.log('api-settings-primary-provider: passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
