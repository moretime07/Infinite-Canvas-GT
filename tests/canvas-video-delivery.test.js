const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.resolve(__dirname, '../static/js/canvas.js'), 'utf8');

function productionFunction(name){
    const start = source.search(new RegExp(`(?:async )?function ${name}\\(`));
    assert.notEqual(start, -1);
    const next = source.slice(start + 1).search(/\n(?:async )?function /);
    return source.slice(start, next < 0 ? undefined : start + 1 + next);
}

async function main(){
    const context = vm.createContext({
        nowMs:() => 40000, langIsEn:() => false, formatRunDuration:ms => `${ms}ms`,
    });
    vm.runInContext(['normalizedRunTimings', 'logTimingRows', 'formatTimingDuration', 'canvasVideoRunMeta', 'canvasVideoTimingTitle', 'canvasVideoStageText'].map(productionFunction).join('\n'), context);
    const pending = {startedAt:1000, prepareMs:500, run:{}, canvasTaskId:'task', videoPhase:'downloading', downloadedBytes:2097152};
    const meta = context.canvasVideoRunMeta(pending, {timings:{total_ms:3000, upstream_ms:2000, download_ms:1000}});
    assert.equal(meta.runMs, 3500, 'paused/recovered waiting must not inflate final processing time');
    assert.equal(context.canvasVideoRunMeta({startedAt:1000}, {}).runMs, 39000, 'old servers retain legacy timing');
    assert.match(context.canvasVideoTimingTitle(meta), /素材下载 \/ 保存: 1.0 秒/);
    assert.match(context.canvasVideoTimingTitle(meta), /不含文件夹导出/);
    assert.match(context.canvasVideoStageText(pending), /正在下载生成视频 · 2.0 MB/);
    assert.match(context.canvasVideoStageText({...pending, downloadMode:'parallel', downloadTotalBytes:8388608, downloadSpeedBps:1048576}), /并行下载视频 · 2.0 \/ 8.0 MB · 25% · 1.0 MB\/s 平均/);

    const sleeps = [];
    const replies = [
        {status:'running', phase:'downloading', downloaded_bytes:1024},
        {status:'succeeded', result:{videos:['/output/a.mp4']}, timings:{total_ms:3000}},
    ];
    Object.assign(context, {
        cascadeTargetIdFromOptions:() => '',
        cascadeFetch:async () => ({ok:true, json:async () => replies.shift()}),
        findPendingTask:() => ({pending}), sleep:async ms => sleeps.push(ms), tr:key => key,
    });
    vm.runInContext(productionFunction('waitCanvasVideoTaskResult'), context);
    const result = await context.waitCanvasVideoTaskResult('task');
    assert.deepEqual(sleeps, [1000], 'download completion should be collected promptly from local backend');
    assert.equal(result.timings.total_ms, 3000);

    for(const failFirst of [false, true]){
        let release;
        const gate = new Promise(resolve => {release = resolve;});
        const callbacks = [];
        const bodies = [];
        const output = {id:'out', type:'output', autoExport:true, exportedOutputUrls:[], images:[]};
        const exportContext = vm.createContext({
            nodes:[output], canvas:{title:'test'},
            outputExportRequests:new Set(), outputExportQueues:new Map(),
            ensureOutputExportSettings:() => {}, outputExportMediaItems:items => items,
            setOutputExportStatus:(_node, text) => {output.status = text;}, scheduleSave:() => {},
            window:{setTimeout:callback => callbacks.push(callback)},
            fetch:async (_url, options) => {
                const body = JSON.parse(options.body);
                bodies.push(body);
                const first = bodies.length === 1;
                if(first) await gate;
                return {ok:!(first && failFirst), json:async () => ({
                    detail:'test failure', elapsed_ms:30,
                    exported:body.items.map(item => ({...item, mode:'copy'})),
                })};
            },
        });
        vm.runInContext(productionFunction('exportOutputNodeMedia'), exportContext);
        const first = exportContext.exportOutputNodeMedia('out', {items:[{url:'/output/a.mp4'}]});
        await exportContext.exportOutputNodeMedia('out', {items:[{url:'/output/b.mp4'}]});
        await exportContext.exportOutputNodeMedia('out', {items:[{url:'/output/b.mp4'}]});
        release();
        await first;
        while(callbacks.length) await callbacks.shift()();
        assert.equal(bodies.length, 2, 'new results must drain after the ongoing export, including after failure');
        assert.deepEqual(bodies[1].items, [{url:'/output/b.mp4'}], 'queued URLs must be deduplicated');
        assert.ok(output.exportedOutputUrls.includes('/output/b.mp4'));
        assert.match(output.status, /视频无损复制/);
        assert.equal(exportContext.outputExportRequests.size, 0);
        assert.equal(exportContext.outputExportQueues.size, 0);
    }
    console.log('canvas-video-delivery: passed');
}
main().catch(error => {console.error(error); process.exitCode = 1;});
