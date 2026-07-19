const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function fakeElement(){
    return {style:{},classList:{add(){},remove(){},toggle(){},contains(){return false;}},addEventListener(){},querySelector(){return null;},querySelectorAll(){return [];},closest(){return null;},setAttribute(){},removeAttribute(){},disabled:false,hidden:false,value:'',textContent:'',innerHTML:'',placeholder:''};
}

function clone(value){
    return JSON.parse(JSON.stringify(value));
}

async function createState(providerRows, options={}){
    const calls = [];
    const messages = [];
    const alerts = [];
    const elements = new Map();
    const initialRows = clone(providerRows);
    const document = {body:fakeElement(),getElementById(id){if(!elements.has(id)) elements.set(id,fakeElement());return elements.get(id);},querySelector(){return null;},querySelectorAll(){return [];},addEventListener(){}};
    const window = {addEventListener(){},parent:{postMessage(message){messages.push(message);}},top:{postMessage(message){messages.push(message);}},location:{href:''}};
    const fetch = async (url, fetchOptions={}) => {
        calls.push({url, options:fetchOptions});
        if(url === '/api/providers' && fetchOptions.method === 'PUT') return {ok:true,json:async()=>({providers:clone(initialRows)})};
        if(url === '/api/providers') return {ok:true,json:async()=>({providers:clone(initialRows)})};
        if(url.endsWith('/primary')){
            if(options.primaryHandler) return options.primaryHandler(url, fetchOptions, clone(initialRows));
            return {ok:true,json:async()=>({providers:initialRows.map(item=>({...item,primary:url.includes(item.id)}))})};
        }
        throw new Error(`unexpected fetch ${url}`);
    };
    const context = {document,window,fetch,URL,console,setTimeout,clearTimeout,alert(message){alerts.push(message);},confirm(){return true;}};
    context.globalThis = context;
    const sourcePath = path.resolve(__dirname,'..','static','js','api-settings.js');
    const source = fs.readFileSync(sourcePath,'utf8') + `\n globalThis.__primaryTest={loadProviders,setPrimaryProvider,providerPrimaryIssue,providerPrimaryControl,providerCapabilityBadges,deleteProvider,saveProviders,renderProviderList,providers:()=>providers,selected:()=>selectedId,pending:()=>primaryProviderPendingId};`;
    vm.runInNewContext(source,context,{filename:sourcePath});
    await context.__primaryTest.loadProviders();
    return {api:context.__primaryTest,calls,messages,alerts,elements};
}

function assertPrimaryButtonsAreCardSiblings(markup, expectedCount){
    const stack = [];
    let shellCount = 0;
    let primaryCount = 0;
    for(const match of markup.matchAll(/<\/?(?:div|button)\b[^>]*>/g)){
        const token = match[0];
        const closing = token.startsWith('</');
        const tag = /^<\/?(div|button)/.exec(token)[1];
        if(closing){
            const index = stack.map(entry=>entry.tag).lastIndexOf(tag);
            assert.notEqual(index, -1, `unexpected closing ${tag}`);
            stack.splice(index, 1);
            continue;
        }
        const classes = /class="([^"]*)"/.exec(token)?.[1].split(/\s+/) || [];
        if(classes.includes('provider-card-shell')) shellCount += 1;
        if(classes.includes('provider-primary-btn')){
            primaryCount += 1;
            assert.ok(stack.some(entry=>entry.classes.includes('provider-card-shell')), 'primary control must belong to a card shell');
            assert.ok(!stack.some(entry=>entry.tag === 'button'), 'primary control must not be nested in the provider-card button');
        }
        stack.push({tag, classes});
    }
    assert.equal(shellCount, expectedCount);
    assert.equal(primaryCount, expectedCount);
}

(async()=>{
    const rows = [
        {id:'modelscope',name:'ModelScope',enabled:true,primary:true,has_key:true,image_models:['img'],chat_models:[],video_models:[]},
        {id:'runninghub',name:'RunningHub',enabled:true,primary:false,has_key:false,has_wallet_key:true,image_models:[],chat_models:['chat'],video_models:[]},
        {id:'volcengine',name:'Volcengine',enabled:true,primary:false,has_key:true,image_models:['img'],chat_models:[],video_models:[]},
        {id:'lingjing',name:'Lingjing',enabled:true,primary:false,has_key:true,image_models:[],chat_models:['chat'],video_models:[]},
        {id:'custom-api',name:'Custom',enabled:true,primary:false,has_key:true,image_models:[],chat_models:['chat'],video_models:['video']},
        {id:'off',name:'Off',enabled:false,primary:false,has_key:true,image_models:[],chat_models:['chat'],video_models:[]},
        {id:'empty',name:'Empty',enabled:true,primary:false,has_key:false,image_models:[],chat_models:[],video_models:[]},
        {id:'modeless',name:'Modeless',enabled:true,primary:false,has_key:true,image_models:[],chat_models:[],video_models:[]}
    ];
    const state = await createState(rows);

    const markup = state.elements.get('providerList').innerHTML;
    assert.equal((markup.match(/provider-card-banner/g) || []).length, 4, 'all four built-in banners must render');
    assert.equal((markup.match(/provider-card-sortable/g) || []).length, 4, 'generic providers must retain sortable cards');
    assertPrimaryButtonsAreCardSiblings(markup, rows.length);

    assert.equal(state.api.providerPrimaryIssue(rows[1]), '', 'RunningHub wallet credentials are eligible');
    assert.equal(state.api.providerPrimaryIssue(rows[5]), '平台已停用');
    assert.equal(state.api.providerPrimaryIssue(rows[6]), '未配置密钥');
    assert.equal(state.api.providerPrimaryIssue(rows[7]), '未配置模型');
    assert.match(state.api.providerCapabilityBadges(rows[4]), /对话/);
    assert.match(state.api.providerCapabilityBadges(rows[4]), /视频/);

    const currentControl = state.api.providerPrimaryControl(rows[0]);
    assert.match(currentControl, /disabled/);
    assert.match(currentControl, /title="当前默认供应商"/);
    assert.match(currentControl, /aria-label="当前默认供应商"/);
    const disabledControl = state.api.providerPrimaryControl(rows[5]);
    assert.match(disabledControl, /disabled/);
    assert.match(disabledControl, /title="平台已停用"/);
    assert.match(disabledControl, /aria-label="平台已停用"/);
    const eligibleControl = state.api.providerPrimaryControl(rows[4]);
    assert.doesNotMatch(eligibleControl, / disabled/);
    assert.match(eligibleControl, /title="设为默认"/);
    assert.match(eligibleControl, /aria-label="将 Custom 设为默认供应商"/);

    let releasePrimary;
    const pendingState = await createState(rows, {primaryHandler:(url, fetchOptions, serverRows)=>new Promise(resolve=>{
        releasePrimary = ()=>resolve({ok:true,json:async()=>({providers:serverRows.map(item=>({...item,primary:url.includes(item.id)}))})});
    })});
    const firstSwitch = pendingState.api.setPrimaryProvider({preventDefault(){},stopPropagation(){}}, 'custom-api');
    assert.equal(pendingState.api.pending(), 'custom-api');
    assert.match(pendingState.api.providerPrimaryControl(rows[4]), /is-pending/);
    assert.match(pendingState.api.providerPrimaryControl(rows[1]), /disabled/);
    assert.equal(await pendingState.api.setPrimaryProvider(null, 'runninghub'), false, 'pending requests suppress duplicate switches');
    assert.equal(pendingState.calls.filter(call=>call.url.endsWith('/primary')).length, 1);
    releasePrimary();
    assert.equal(await firstSwitch, true);
    assert.equal(pendingState.api.pending(), '', 'pending state clears after success');
    assert.equal(pendingState.api.providers().filter(item=>item.primary).map(item=>item.id).join(','), 'custom-api');
    assert.equal(pendingState.api.selected(), 'modelscope', 'switching primary must not select another editor card');
    const primaryCall = pendingState.calls.find(call=>call.url.endsWith('/primary'));
    assert.equal(primaryCall.options.method, 'PUT');
    assert.equal(primaryCall.options.body, undefined);
    assert.ok(pendingState.messages.some(message=>message?.type === 'providers-changed'));
    assert.ok(!pendingState.messages.some(message=>message?.type === 'studio-api'), 'window messages use the established providers-changed contract');

    const errorState = await createState(rows, {primaryHandler:async()=>({ok:false,json:async()=>({detail:'switch rejected'})})});
    assert.equal(await errorState.api.setPrimaryProvider(null, 'custom-api'), false);
    assert.equal(errorState.api.pending(), '', 'pending state clears after errors');
    assert.equal(errorState.api.providers().filter(item=>item.primary).map(item=>item.id).join(','), 'modelscope', 'an error must leave primary state unchanged');
    assert.equal(errorState.elements.get('status').textContent, 'switch rejected');
    assert.ok(!errorState.messages.some(message=>message?.type === 'providers-changed'));

    const deleteState = await createState(rows);
    deleteState.api.deleteProvider();
    assert.equal(deleteState.alerts.at(-1), '请先设置另一个默认供应商');
    assert.equal(deleteState.api.providers().length, rows.length);
    assert.equal(deleteState.calls.filter(call=>call.url === '/api/providers' && call.options.method === 'PUT').length, 0);

    const saveState = await createState(rows);
    assert.equal(await saveState.api.saveProviders(), true);
    const saveCall = saveState.calls.find(call=>call.url === '/api/providers' && call.options.method === 'PUT');
    const savedRows = JSON.parse(saveCall.options.body);
    assert.equal(savedRows.find(item=>item.id === 'modelscope').primary, true);
    assert.equal(savedRows.filter(item=>item.primary).length, 1);

    const css = fs.readFileSync(path.resolve(__dirname,'..','static','css','api-settings.css'),'utf8');
    assert.match(css, /\.provider-primary-btn:focus-visible\s*\{/);
    assert.doesNotMatch(css, /\.provider-card-shell\s*>\s*\.provider-card\s*\{[^}]*padding-right\s*:\s*104px/s,
        'card content must not be squeezed to make room for overlaid controls');
    assert.match(css, /\.provider-card-shell\s*\{[^}]*position\s*:\s*relative/s);
    assert.doesNotMatch(css, /\.provider-card-shell\s*\{[^}]*display\s*:\s*grid/s, 'controls must not consume a dedicated second row');
    assert.match(css, /\.provider-primary-btn\s*\{[^}]*(?:position\s*:\s*absolute[^}]*top\s*:\s*\d+px[^}]*right\s*:\s*\d+px|top\s*:\s*\d+px[^}]*right\s*:\s*\d+px[^}]*position\s*:\s*absolute)/s,
        'the compact primary control must stay at the top-right');
    assert.doesNotMatch(css, /\.provider-primary-btn\s*\{[^}]*grid-row\s*:\s*2/s);
    assert.match(css, /\.provider-primary-btn\s*>\s*span\s*\{[^}]*clip/s,
        'the compact icon control must retain accessible label text');
    console.log('api-settings-primary-provider: passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
