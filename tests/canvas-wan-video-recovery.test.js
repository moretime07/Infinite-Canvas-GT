const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '..', 'static/js/canvas.js'), 'utf8');

function productionFunction(name){
    const asyncStart = source.indexOf(`async function ${name}(`);
    const start = asyncStart === -1 ? source.indexOf(`function ${name}(`) : asyncStart;
    assert.notEqual(start, -1, `canvas.js should define ${name}`);
    const nextSync = source.indexOf('\nfunction ', start + 1);
    const nextAsync = source.indexOf('\nasync function ', start + 1);
    const next = [nextSync, nextAsync].filter(index => index !== -1).sort((a, b) => a - b)[0] ?? -1;
    return source.slice(start, next === -1 ? source.length : next);
}

function runVideoNodePolicy(){
    const video = {
        id:'video-1', type:'video', apiProvider:'wan-test', model:'wan3.0-video',
        duration:5, aspectRatio:'16:9', resolution:'720p', enableUpsample:false
    };
    const output = {id:'output-1', type:'output', images:[], generatedOutputs:[], _pending:[]};
    const requests = [];
    const alerts = [];
    const queryBodies = [];
    const completions = [];
    const sandbox = {
        nodes:[video, output], apiProviders:[{id:'wan-test', base_url:'https://wan.example.test/v1'}],
        syncDefaultCanvasNodeProvider:() => {}, stopUnresolvedDefaultCanvasRun:() => false,
        preflightCanvasNodeRequest:() => ({providerId:video.apiProvider, model:video.model}),
        videoProviderModeOptions:() => ({}), cascadeTargetIdFromOptions:() => '',
        generatorSources:() => [{prompt:'A moving landscape', refs:[{kind:'image', url:'/test.png'}]}],
        orderedSources:(_node, sources) => sources, mediaKindForRef:ref => ref.kind,
        applyUploadedUrlToRefs:refs => refs, imageRefsOnly:refs => refs, videoRefsOnly:() => [],
        audioRefsOnly:() => [], openRouterVideoReferenceState:() => ({conflict:false}),
        manualVideoUrlForNode:() => '', isOminiLinkProvider:() => false, outputForNode:() => output,
        uid:() => 'pending-1', runSnapshot:() => ({node:{id:video.id, model:video.model}, request:{}}),
        makePendingForRun:(id, run) => ({id, run, startedAt:1000}),
        refreshRunNodes:() => {}, refreshNodes:() => {}, scheduleSave:() => {}, nowMs:() => 2000,
        canvasVideoRunMeta:(pending, taskData) => ({runMs:1000, run:pending.run}),
        createCanvasVideoTask:async payload => {
            requests.push(payload);
            return {task_id:'canvas-task-1', status:'queued'};
        },
        waitCanvasVideoTaskResult:async () => {
            const error = new Error('wan save failed: proxy port refused');
            error.taskData = {
                status:'failed', recoverable:true, upstream_task_id:'wan-task-1', provider_id:'wan-test'
            };
            throw error;
        },
        prepareAiideVideoReferences:async () => {}, addGenerationLog:() => {},
        isCascadeAbortError:() => false, alert:message => alerts.push(message), tr:key => key,
        resultMediaUrls:result => result.videos || [], outputUrlValue:value => value,
        requestMetaFromResult:() => ({}), appendOutputImages:() => {}, mergeGeneratedOutputs:() => {},
        fetch:async (url, options) => {
            queryBodies.push({url, body:JSON.parse(options.body)});
            return {
                ok:true,
                json:async () => ({status:'succeeded', videos:['/output/recovered.mp4'], task_id:'wan-task-1'})
            };
        },
        completeCanvasVideoTask:(taskId, result) => completions.push({taskId, result}),
        showErrorModal:message => assert.fail(message), setStatus:() => {}, apiErrorMessage:() => 'failed',
        findPendingTask:findPendingTaskProduction([video, output]),
        findOutputByPendingId:pendingId => {
            return [video, output].find(node => node.type === 'output'
                && (node._pending || []).some(pending => pending.id === pendingId));
        },
        pendingById:(out, id) => {
            return (out?._pending || []).find(pending => pending.id === id) || null;
        },
        providerIdForPending:pending => pending.providerId || pending.run?.request?.provider_id || 'comfly'
    };
    vm.createContext(sandbox);
    vm.runInContext(productionFunction('runVideoNode'), sandbox);
    vm.runInContext(productionFunction('recoverCanvasVideoResult'), sandbox);
    return {sandbox, video, output, requests, alerts, queryBodies, completions};
}

function findPendingTaskProduction(nodes){
    return taskId => {
        for(const out of nodes.filter(node => node.type === 'output')){
            const pending = (out._pending || []).find(item => item.canvasTaskId === taskId);
            if(pending) return {out, pending};
        }
        return null;
    };
}

test('recoverable Wan save failures keep a result card and query only the existing task', async () => {
    const state = runVideoNodePolicy();
    await state.sandbox.runVideoNode('video-1');

    assert.equal(state.requests.length, 1, 'generation should be submitted exactly once');
    assert.equal(state.output._pending.length, 1, 'a recoverable save failure must remain visible');
    const pending = state.output._pending[0];
    assert.equal(pending.failed, true);
    assert.equal(pending.upstreamRecoverable, true);
    assert.equal(pending.recoverTaskId, 'wan-task-1');
    assert.match(pending.error, /proxy port refused/);
    assert.match(state.alerts[0], /proxy port refused/);

    await state.sandbox.recoverCanvasVideoResult('pending-1');
    assert.equal(state.requests.length, 1, 'recovery must not submit a second generation task');
    assert.equal(state.queryBodies.length, 1);
    assert.equal(state.queryBodies[0].url, '/api/canvas-video-task-query');
    assert.deepEqual(state.queryBodies[0].body, {
        provider_id:'wan-test', task_id:'wan-task-1', model:'wan3.0-video'
    });
    assert.equal(state.completions.length, 1);
    assert.equal(state.completions[0].taskId, 'canvas-task-1');
});

test('ordinary video failures still remove their pending card', () => {
    const output = {id:'output-1', type:'output', _pending:[{id:'pending-1', canvasTaskId:'canvas-task-1', run:{}}]};
    const sandbox = {
        nodes:[output],
        findPendingTask:findPendingTaskProduction([output]),
        canvasVideoRunMeta:pending => ({run:pending.run, runMs:1}),
        addGenerationLog:() => {}, refreshRunNodes:() => {}, scheduleSave:() => {}, tr:key => key,
        providerIdForPending:pending => pending.providerId || 'comfly'
    };
    vm.createContext(sandbox);
    vm.runInContext(productionFunction('failCanvasVideoTask'), sandbox);
    sandbox.failCanvasVideoTask('canvas-task-1', 'ordinary generation failure', {
        status:'failed', recoverable:false, upstream_task_id:'wan-task-1'
    });
    assert.equal(output._pending.length, 0);
});
