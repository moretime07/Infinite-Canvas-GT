const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const source = fs.readFileSync(path.resolve(__dirname, '..', 'static/js/canvas.js'), 'utf8');
const start = source.indexOf('async function runVideoNode(');
const end = source.indexOf('\nasync function ', start + 1);
assert.ok(start >= 0 && end > start);

test('Wan video node sends its selected ratio and resolution without enabling upsampling', async () => {
    const requests = [];
    const node = {
        id:'video-1', type:'video', apiProvider:'wan-test', model:'wan3.0-video',
        duration:5, aspectRatio:'9:16', resolution:'720p', enableUpsample:false
    };
    const sandbox = {
        nodes:[node], apiProviders:[{id:'wan-test', base_url:'https://wan.example.test/v1'}],
        syncDefaultCanvasNodeProvider:() => {}, stopUnresolvedDefaultCanvasRun:() => false,
        preflightCanvasNodeRequest:() => ({providerId:node.apiProvider, model:node.model}),
        videoProviderModeOptions:() => ({}), cascadeTargetIdFromOptions:() => '',
        generatorSources:() => [{prompt:'A moving landscape', refs:[{kind:'image', url:'/test.png'}]}],
        orderedSources:(_node, sources) => sources, mediaKindForRef:ref => ref.kind,
        applyUploadedUrlToRefs:refs => refs, imageRefsOnly:refs => refs,
        videoRefsOnly:() => [], audioRefsOnly:() => [],
        openRouterVideoReferenceState:() => ({conflict:false}), manualVideoUrlForNode:() => '',
        isOminiLinkProvider:() => false, outputForNode:() => null, uid:() => 'pending-1',
        runSnapshot:() => ({}), makePendingForRun:() => ({}), refreshRunNodes:() => {}, scheduleSave:() => {},
        prepareAiideVideoReferences:async () => {},
        createCanvasVideoTask:async payload => {
            requests.push(JSON.parse(JSON.stringify(payload)));
            return {task_id:'mock-task', status:'queued'};
        },
        waitCanvasVideoTaskResult:async () => ({videos:['/test.mp4']}),
        collectRunMeta:() => ({runMs:0}), resultMediaUrls:result => result.videos,
        outputUrlValue:value => value, requestMetaFromResult:() => ({}),
        appendOutputImages:() => {}, mergeGeneratedOutputs:() => {}, addGenerationLog:() => {},
        isCascadeAbortError:() => false, alert:message => assert.fail(message), tr:key => key
    };
    vm.createContext(sandbox);
    vm.runInContext(source.slice(start, end), sandbox);
    for (const ratio of ['9:16', '16:9', '1:1', '4:3', '3:4', '21:9', '9:21', 'adaptive', 'keep_ratio']) {
      for (const resolution of ['480p', '720p', '1080p', '']) {
        node.aspectRatio = ratio;
        node.resolution = resolution;
        await sandbox.runVideoNode(node.id);
        assert.equal(node.runStatus, 'done');
        assert.equal(requests.at(-1).aspect_ratio, ratio);
        assert.equal(requests.at(-1).resolution, resolution);
        assert.equal(requests.at(-1).enable_upsample, false);
        assert.equal(requests.at(-1).model, 'wan3.0-video');
        assert.equal(requests.at(-1).images.length, 1);
        assert.equal(node.resolution, resolution);
        assert.equal(node.aspectRatio, ratio);
      }
    }
    assert.equal(requests.length, 36);
});
