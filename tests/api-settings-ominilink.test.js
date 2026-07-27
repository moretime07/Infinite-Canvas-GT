const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function fakeElement(){
    return {
        style:{},
        classList:{add(){},remove(){},toggle(){},contains(){return false;}},
        addEventListener(){},
        dispatchEvent(){},
        querySelector(){return fakeElement();},
        querySelectorAll(){return [];},
        closest(){return null;},
        setAttribute(){},
        removeAttribute(){},
        disabled:false,
        hidden:false,
        value:'',
        textContent:'',
        innerHTML:'',
        placeholder:''
    };
}

function clone(value){
    return JSON.parse(JSON.stringify(value));
}

async function createState(providerRows){
    const calls = [];
    const elements = new Map();
    const initialRows = clone(providerRows);
    const document = {
        body:fakeElement(),
        getElementById(id){
            if(!elements.has(id)) elements.set(id, fakeElement());
            return elements.get(id);
        },
        querySelector(){return null;},
        querySelectorAll(){return [];},
        addEventListener(){}
    };
    document.getElementById('keyInput').value = 'secret-value';
    const window = {addEventListener(){},parent:{postMessage(){}},top:{postMessage(){}},location:{href:''}};
    const fetch = async (url, options={}) => {
        calls.push({url, options});
        if(url === '/api/providers' && options.method === 'PUT') return {ok:true,json:async()=>({providers:clone(initialRows)})};
        if(url === '/api/providers') return {ok:true,json:async()=>({providers:clone(initialRows)})};
        if(url === '/api/providers/test-connection' || url === '/api/providers/fetch-models'){
            return {ok:true,json:async()=>({
                ok:true,
                total:19,
                model_count:19,
                all:['gemini-omni-flash-preview'],
                image_models:[],
                chat_models:['gemini-omni-flash-preview'],
                video_models:['gemini-omni-flash-preview'],
                catalog_fallback:true,
                connection_verified:false
            })};
        }
        throw new Error(`unexpected fetch ${url}`);
    };
    const context = {document,window,fetch,URL,console,setTimeout,clearTimeout,alert(){},confirm(){return true;}};
    context.globalThis = context;
    const sourcePath = path.resolve(__dirname, '..', 'static', 'js', 'api-settings.js');
    const source = fs.readFileSync(sourcePath, 'utf8') + '\n globalThis.__ominiLinkTest={loadProviders,syncEditor,renderEditor,saveProviders,testConnection,fetchModels,isOminiLinkApiUrl,defaultOminiLinkVideoBaseUrl};';
    vm.runInNewContext(source, context, {filename:sourcePath});
    await context.__ominiLinkTest.loadProviders();
    return {api:context.__ominiLinkTest,calls,elements};
}

(async()=>{
    const state = await createState([{
        id:'ominilink',
        name:'OminiLink',
        base_url:'https://api.aig-ai.com/v1',
        video_base_url:'',
        protocol:'openai',
        enabled:true,
        primary:true,
        has_key:true,
        image_models:[],
        chat_models:['gemini-omni-flash-preview'],
        video_models:['gemini-omni-flash-preview']
    }]);

    assert.equal(state.api.isOminiLinkApiUrl('https://api.aig-ai.com/v1'), true);
    assert.equal(state.api.isOminiLinkApiUrl('https://portal.ominilink.ai/'), false);
    assert.equal(
        state.api.defaultOminiLinkVideoBaseUrl('https://api.aig-ai.com/v1'),
        'https://vg-api.aig-ai.com/v1'
    );

    assert.equal(state.elements.get('videoBaseInput').value, 'https://vg-api.aig-ai.com/v1');
    state.elements.get('baseInput').value = 'https://api.aig-ai.com/v1';
    state.elements.get('videoBaseInput').value = 'https://vg-api.aig-ai.com/v1';
    assert.equal(await state.api.saveProviders(), true);
    const putCall = state.calls.find(call => call.options.method === 'PUT');
    const body = JSON.parse(putCall.options.body);
    assert.equal(body[0].video_base_url, 'https://vg-api.aig-ai.com/v1');
    assert.ok(!Object.hasOwn(body[0], 'api_key'));
    assert.ok(!JSON.stringify(body).includes('secret-value'));

    await state.api.testConnection();
    const verificationRequest = state.calls.find(call => call.url === '/api/providers/test-connection');
    assert.equal(JSON.parse(verificationRequest.options.body).video_base_url, 'https://vg-api.aig-ai.com/v1');
    const verification = state.elements.get('verifyResult').innerHTML;
    assert.match(verification, /官方目录兜底/);
    assert.doesNotMatch(verification, /地址验证通过|API Key 验证通过|✓/);

    await state.api.fetchModels();
    const fetchRequest = state.calls.find(call => call.url === '/api/providers/fetch-models');
    assert.equal(JSON.parse(fetchRequest.options.body).video_base_url, 'https://vg-api.aig-ai.com/v1');
    const fetchStatus = state.elements.get('status').textContent;
    assert.match(fetchStatus, /官方目录兜底/);
    assert.match(fetchStatus, /未验证当前账号权限/);
    assert.doesNotMatch(fetchStatus, /地址验证通过|API Key 验证通过/);
    console.log('api-settings-ominilink tests passed');
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
