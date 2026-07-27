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
    let persistedRows = clone(providerRows);
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
    const window = {addEventListener(){},parent:{postMessage(){}},top:{postMessage(){}},location:{href:''}};
    const fetch = async (url, options={}) => {
        calls.push({url, options});
        if(url === '/api/providers' && options.method === 'PUT'){
            const priorById = new Map(persistedRows.map(row => [row.id, row]));
            persistedRows = JSON.parse(options.body).map(row => {
                const prior = priorById.get(row.id) || {};
                return {
                    ...row,
                    has_key:prior.has_key === true,
                    key_preview:prior.key_preview || ''
                };
            });
            return {ok:true,json:async()=>({providers:clone(persistedRows)})};
        }
        if(url === '/api/providers') return {ok:true,json:async()=>({providers:clone(persistedRows)})};
        if(url === '/api/providers/test-connection' || url === '/api/providers/fetch-models' || url === '/api/providers/probe-async'){
            return {ok:true,json:async()=>({
                ok:true,
                protocol:'openai',
                status_code:200,
                raw:{},
                total:19,
                model_count:19,
                all:['gemini-omni-flash-preview', 'gemini-omni-flash-preview', 'chat-only', 'video-only'],
                image_models:[],
                chat_models:['gemini-omni-flash-preview', 'chat-only'],
                video_models:['gemini-omni-flash-preview', 'video-only'],
                catalog_fallback:true,
                connection_verified:false
            })};
        }
        throw new Error(`unexpected fetch ${url}`);
    };
    const context = {document,window,fetch,URL,console,setTimeout,clearTimeout,alert(){},confirm(){return true;}};
    context.globalThis = context;
    const sourcePath = path.resolve(__dirname, '..', 'static', 'js', 'api-settings.js');
    const source = fs.readFileSync(sourcePath, 'utf8') + '\n globalThis.__ominiLinkTest={loadProviders,syncEditor,renderEditor,saveProviders,testConnection,probeAsync,fetchModels,openModelPicker,togglePickerRow,applyModelPicker,providers:()=>providers,isOminiLinkApiUrl,defaultOminiLinkVideoBaseUrl};';
    vm.runInNewContext(source, context, {filename:sourcePath});
    await context.__ominiLinkTest.loadProviders();
    return {api:context.__ominiLinkTest,calls,elements};
}

(async()=>{
    let state;
    let videoOnlyState;
    for(const legacyId of ['volcengine', 'runninghub']){
        for(const entryPoints of [
            {label:'base-only', base_url:'https://api.aig-ai.com/v1', video_base_url:''},
            {label:'video-only', base_url:'', video_base_url:'https://vg-api.aig-ai.com/v1'}
        ]){
            const legacyState = await createState([{
                id:legacyId,
                name:'OminiLink',
                ...entryPoints,
                protocol:legacyId,
                enabled:true,
                primary:true,
                has_key:true,
                image_models:[],
                chat_models:[],
                video_models:[]
            }]);
            legacyState.elements.get('baseInput').value = entryPoints.base_url;
            legacyState.elements.get('videoBaseInput').value = entryPoints.video_base_url;
            assert.equal(await legacyState.api.saveProviders(), true);
            const saved = JSON.parse(legacyState.calls.find(call => call.url === '/api/providers' && call.options.method === 'PUT').options.body)[0];
            assert.equal(saved.protocol, 'openai', `${legacyId} ${entryPoints.label} must remain OpenAI-compatible`);
            assert.equal(saved.base_url, entryPoints.base_url);
            assert.equal(saved.video_base_url, 'https://vg-api.aig-ai.com/v1');
            assert.ok(!Object.hasOwn(saved, 'api_key'));
            await legacyState.api.loadProviders();
            assert.equal(legacyState.api.providers()[0].protocol, 'openai');
            assert.equal(legacyState.elements.get('protocolInput').value, 'openai');
            const providerListMarkup = legacyState.elements.get('providerList').innerHTML;
            assert.match(providerListMarkup, /provider-protocol-pill">OPENAI</);
            assert.doesNotMatch(providerListMarkup, /provider-protocol-pill">(?:RH|Ark)</);
            if(legacyId === 'volcengine' && entryPoints.label === 'base-only') state = legacyState;
            if(legacyId === 'runninghub' && entryPoints.label === 'video-only') videoOnlyState = legacyState;
        }
    }

    let validationCalls = videoOnlyState.calls.filter(call => call.url === '/api/providers/test-connection').length;
    await videoOnlyState.api.testConnection();
    const newValidationCalls = videoOnlyState.calls.filter(call => call.url === '/api/providers/test-connection');
    assert.equal(newValidationCalls.length, validationCalls + 1, 'video-only config must reach address validation');
    const videoOnlyValidation = JSON.parse(newValidationCalls.at(-1).options.body);
    assert.equal(videoOnlyValidation.base_url, '');
    assert.equal(videoOnlyValidation.video_base_url, 'https://vg-api.aig-ai.com/v1');
    assert.equal(videoOnlyValidation.protocol, 'openai');
    assert.ok(!videoOnlyValidation.api_key);

    const probeCalls = videoOnlyState.calls.filter(call => call.url === '/api/providers/probe-async').length;
    await videoOnlyState.api.probeAsync();
    const newProbeCalls = videoOnlyState.calls.filter(call => call.url === '/api/providers/probe-async');
    assert.equal(newProbeCalls.length, probeCalls + 1, 'video-only config must reach protocol probing');
    const videoOnlyProbe = JSON.parse(newProbeCalls.at(-1).options.body);
    assert.equal(videoOnlyProbe.base_url, '');
    assert.equal(videoOnlyProbe.video_base_url, 'https://vg-api.aig-ai.com/v1');
    assert.equal(videoOnlyProbe.protocol, 'openai');
    assert.ok(!videoOnlyProbe.api_key);

    assert.equal(state.api.isOminiLinkApiUrl('https://api.aig-ai.com/v1'), true);
    assert.equal(state.api.isOminiLinkApiUrl('https://portal.ominilink.ai/'), false);
    assert.equal(
        state.api.defaultOminiLinkVideoBaseUrl('https://api.aig-ai.com/v1'),
        'https://vg-api.aig-ai.com/v1'
    );

    assert.equal(state.elements.get('videoBaseInput').value, 'https://vg-api.aig-ai.com/v1');

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
    // This fails if the picker reduces a backend-reported chat+video model to one category.
    state.api.togglePickerRow('gemini-omni-flash-preview');
    state.api.togglePickerRow('chat-only');
    state.api.togglePickerRow('video-only');
    state.api.applyModelPicker();
    assert.equal(await state.api.saveProviders(), true);
    await state.api.loadProviders();
    let persistedProvider = state.api.providers()[0];
    assert.deepEqual(Array.from(persistedProvider.chat_models), ['gemini-omni-flash-preview', 'chat-only']);
    assert.deepEqual(Array.from(persistedProvider.video_models), ['gemini-omni-flash-preview', 'video-only']);
    assert.equal(persistedProvider.chat_models.filter(id => id === 'gemini-omni-flash-preview').length, 1);
    assert.equal(persistedProvider.video_models.filter(id => id === 'gemini-omni-flash-preview').length, 1);

    state.api.openModelPicker();
    state.api.togglePickerRow('gemini-omni-flash-preview');
    state.api.applyModelPicker();
    assert.equal(await state.api.saveProviders(), true);
    await state.api.loadProviders();
    persistedProvider = state.api.providers()[0];
    assert.ok(!persistedProvider.chat_models.includes('gemini-omni-flash-preview'));
    assert.ok(!persistedProvider.video_models.includes('gemini-omni-flash-preview'));

    state.api.openModelPicker();
    state.api.togglePickerRow('gemini-omni-flash-preview');
    state.api.applyModelPicker();
    assert.equal(await state.api.saveProviders(), true);
    await state.api.loadProviders();
    const pickerSaveBody = JSON.parse(state.calls.filter(call => call.url === '/api/providers' && call.options.method === 'PUT').at(-1).options.body)[0];
    persistedProvider = state.api.providers()[0];
    assert.deepEqual(Array.from(pickerSaveBody.chat_models), ['gemini-omni-flash-preview', 'chat-only']);
    assert.deepEqual(Array.from(pickerSaveBody.video_models), ['gemini-omni-flash-preview', 'video-only']);
    assert.equal(pickerSaveBody.chat_models.filter(id => id === 'gemini-omni-flash-preview').length, 1);
    assert.equal(pickerSaveBody.video_models.filter(id => id === 'gemini-omni-flash-preview').length, 1);
    assert.deepEqual(Array.from(persistedProvider.chat_models), ['gemini-omni-flash-preview', 'chat-only']);
    assert.deepEqual(Array.from(persistedProvider.video_models), ['gemini-omni-flash-preview', 'video-only']);
    state.calls.filter(call => call.url === '/api/providers' && call.options.method === 'PUT').forEach(call => {
        JSON.parse(call.options.body).forEach(row => assert.ok(!Object.hasOwn(row, 'api_key')));
    });
    console.log('api-settings-ominilink tests passed');
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
