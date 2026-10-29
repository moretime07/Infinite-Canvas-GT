const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const html = fs.readFileSync(path.join(__dirname, '../static/index.html'), 'utf8');

function section(start, end) {
    const offset = html.indexOf(start);
    assert.ok(offset >= 0);
    const stop = html.indexOf(end, offset + start.length);
    assert.ok(stop > offset);
    return html.slice(offset, stop);
}

test('the update button submits only the GT source with local update protection', async () => {
    const requests = [], summaries = [], elements = new Map();
    const context = {
        window:{StudioI18n:{lang:()=> 'zh'}},
        document:{getElementById(id){
            if(!elements.has(id)) elements.set(id, {textContent:'', disabled:false, dataset:{}, classList:{remove(){}}});
            return elements.get(id);
        }},
        projectUpdateRunning:false, updateSource:'modelscope', appInfo:{version:'old'},
        updateSourceLabel:()=> 'GT GitHub', setUpdateSummary:(...args)=>summaries.push(args),
        setProjectVersionBadge(){}, setProjectUpdateNotes(){}, refreshUpdateButtonText(){},
        describeNetworkError:e=>e.message,
        AbortController, setTimeout:()=>1, clearTimeout(){},
        fetch:async (url, options)=>{
            requests.push({url,options});
            return {ok:true, json:async()=>({count:100, version:'new', restart_scheduled:false})};
        }
    };
    vm.createContext(context);
    vm.runInContext(section('        async function confirmProjectUpdate()', '        async function waitForBackendAndReload('), context);
    await context.confirmProjectUpdate();
    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, '/api/update-from-github');
    assert.equal(requests[0].options.headers['X-Infinite-Canvas-Restart'], '1');
    assert.deepEqual(JSON.parse(requests[0].options.body), {auto_restart:true, restart_delay:3, source:'github', fallback:false});
    assert.equal(summaries.at(-1)[0], 'ok');
    assert.equal(context.projectUpdateRunning, false);
    assert.equal(elements.get('project-update-rollback-btn').disabled, false);
});

test('page reload waits for a new backend instance, not merely a 200 response', async () => {
    let polls = 0, reloads = 0;
    const context = {
        Date, setTimeout:fn=>{fn();return 1;},
        fetch:async()=>({ok:true,json:async()=>({ok:true,instance_id:++polls === 1 ? 'old' : 'new'})}),
        location:{reload(){reloads++;}}, setUpdateSummary(){assert.fail('unexpected timeout');}
    };
    vm.createContext(context);
    vm.runInContext(section('        async function waitForBackendAndReload(', '        async function rollbackProjectUpdate('), context);
    await context.waitForBackendAndReload('old');
    assert.equal(polls, 2);
    assert.equal(reloads, 1);
});

test('legacy sources are not advertised and rollback is reachable', () => {
    assert.doesNotMatch(html, /hero8152\/Infinite-Canvas/);
    assert.doesNotMatch(html, /id="update-source-modelscope"/);
    assert.match(html, /id="project-update-rollback-btn"[^>]*onclick="rollbackProjectUpdate\(\)"/);
    const context = {updateSource:'modelscope', localStorage:{setItem(){}}, refreshProjectUpdateModalText(){}};
    vm.createContext(context);
    vm.runInContext(section('        function setUpdateSource(', '        function sourceConnectivityStats('), context);
    context.setUpdateSource('modelscope');
    assert.equal(context.updateSource, 'github');
});
