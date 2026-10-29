const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.resolve(__dirname, '../static/js/canvas.js'), 'utf8');
function productionFunction(name){
    const start = source.search(new RegExp(`(?:async )?function ${name}\\(`));
    assert.notEqual(start, -1, name);
    const next = source.slice(start + 1).search(/\n(?:async )?function /);
    return source.slice(start, next < 0 ? undefined : start + 1 + next);
}
let clock = 1000;
const context = vm.createContext({
    nowMs:() => clock, langIsEn:() => false,
    escapeHtml:value => String(value).replaceAll('<', '&lt;'),
    canvas:{logs:[]}, uid:() => 'log',
    runPlatformLabel:() => 'fixture', runTaskLabel:() => 'model',
    normalizeCanvasProviderSentinels:value => value,
});
vm.runInContext([
    'normalizedRunTimings', 'formatTimingDuration', 'logTimingRows', 'renderLogTimings',
    'serializableCanvasLogs', 'addGenerationLog', 'canvasVideoRunMeta', 'canvasVideoTimingTitle',
    'markRunningHubTiming', 'runningHubRunMeta',
].map(productionFunction).join('\n'), context);

const backend = {total_ms:12000, upstream_ms:9000, download_ms:3000,
    stages_ms:{prepare:2000, submit:1000, generate:6000, download:3000}};
const pending = {run:{nodeType:'video'}, startedAt:1000, prepareMs:500};
clock = 999999;
const meta = context.canvasVideoRunMeta(pending, {timings:backend});
assert.equal(meta.runMs, 12500, 'recovery uses backend elapsed time, not browser absence');
assert.equal(meta.run.timings.stages_ms.prepare, 2500);
assert.equal(backend.stages_ms.prepare, 2000, 'never mutate the response / double-add client preparation');
context.canvasVideoRunMeta(pending, {timings:backend});
assert.equal(pending.run.timings.stages_ms.prepare, 2500);
context.addGenerationLog({...meta, outputs:['/output/fixture.mp4']});
const log = context.canvas.logs[0];
assert.equal(log.timings.stages_ms.download, 3000);
assert.equal(JSON.parse(JSON.stringify(context.serializableCanvasLogs()))[0].timings.stages_ms.generate, 6000);
const rows = context.logTimingRows(log);
assert.equal(rows.length, 5);
assert.equal(rows.filter(row => !row.total).reduce((sum, row) => sum + row.value, 0), log.runMs);
const html = context.renderLogTimings(log);
assert.match(html, /素材准备 \/ 上传/);
assert.match(html, /素材生成（含排队 \/ 轮询）/);
assert.match(html, /素材下载 \/ 保存/);
assert.match(html, /12.5 秒/);
assert.match(html, /不含文件夹导出/);

const cleaned = context.normalizedRunTimings({total_ms:1, api_key:'private', raw:{token:'private'},
    stages_ms:{prepare:-1, submit:NaN, generate:Infinity, download:0, malicious:23}});
assert.deepEqual(JSON.parse(JSON.stringify(cleaned)), {stages_ms:{download:0}, total_ms:1});
assert.match(context.renderLogTimings({runMs:1000}), /未采集分阶段耗时/);
assert.match(context.renderLogTimings({}), /未记录/);
assert.equal(context.formatTimingDuration(123), '123 ms');
assert.equal(context.formatTimingDuration(61050), '1 分 1.1 秒');
assert.equal(context.formatTimingDuration(59999), '1 分 0.0 秒');
assert.match(context.renderLogTimings({runMs:22000, timings:{stages_ms:{prepare:1000, submit:21000}}}), /提交 \/ 生成（合计）/);
assert.doesNotMatch(context.renderLogTimings({runMs:22000, timings:{stages_ms:{prepare:1000, submit:21000}}}), /素材生成（含排队/);
const failed = context.canvasVideoRunMeta({startedAt:1000, prepareMs:100, run:{}}, {timings:{total_ms:1000, stages_ms:{prepare:200, submit:800}}});
context.addGenerationLog({...failed, error:'fixture failure'});
assert.equal(context.canvas.logs[0].status, 'failed');
assert.equal(context.canvas.logs[0].runMs, 1100);
assert.equal(context.canvas.logs[0].timings.stages_ms.download, undefined);

clock = 1000;
const rh = {startedAt:clock, run:{}};
context.markRunningHubTiming(rh, 'prepare');
clock += 2000;
context.markRunningHubTiming(rh, 'submit');
clock += 1000;
context.markRunningHubTiming(rh, 'generate');
clock += 9000;
const rhMeta = context.runningHubRunMeta(rh, {timings:{download_ms:3000}}, true);
assert.deepEqual(JSON.parse(JSON.stringify(rhMeta.run.timings.stages_ms)), {prepare:2000, submit:1000, generate:6000, download:3000});
assert.equal(rhMeta.runMs, 12000);
assert.equal(rhMeta.run.timings.recovered, true);
assert.match(context.renderLogTimings({runMs:rhMeta.runMs, timings:rhMeta.run.timings}), /包含离线 \/ 暂停/);
context.runningHubRunMeta(rh, {timings:{download_ms:3000}});
assert.equal(rh.run.timings.stages_ms.download, 3000, 'finishing twice must not subtract downloads twice');
assert.equal(context.runningHubRunMeta({startedAt:1000, run:{}}, {}, true).run.timings, undefined, 'old pending tasks must not invent stages');
assert.equal(context.canvasVideoRunMeta({startedAt:1000, run:{}, canvasTaskId:'old'}, {}).run.timings.stages_ms.prepare, undefined);

assert.match(productionFunction('renderCanvasLog'), /renderLogTimings\(log\)/);
assert.match(productionFunction('pollCanvasVideoTask'), /failCanvasVideoTask\(taskId,.*err\.taskData\)/);
assert.match(productionFunction('runVideoNode'), /canvasVideoRunMeta\(pending, err\.taskData\)/);
console.log('canvas-generation-timings: passed');
