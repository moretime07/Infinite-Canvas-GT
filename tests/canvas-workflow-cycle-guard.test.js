const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '..', 'static', 'js', 'canvas.js'), 'utf8');

function productionFunction(name){
    const match = new RegExp(`(?:async )?function ${name}\\(`).exec(source);
    assert.ok(match, `canvas.js should define ${name}`);
    const start = match.index;
    const next = /\n(?:async )?function \w+\(/g;
    next.lastIndex = start + match[0].length;
    const following = next.exec(source);
    return source.slice(start, following ? following.index : source.length);
}

const nodes = [
    {id:'llm', type:'llm'},
    {id:'video', type:'video', model:'seedance-2.0'},
    {id:'output', type:'output'},
];
const connections = [
    {id:'forward', from:'llm', to:'video'},
    {id:'feedback', from:'video', to:'llm'},
    {id:'video-output', from:'video', to:'output'},
];
const context = {
    nodes, connections,
    Set, Map, Array, Object,
    tr:key => ({
        'canvas.videoGenerateNode':'视频生成',
        'canvas.apiGenerate':'API生成',
        'canvas.modelscopeGenerate':'Modelscope生成',
        'canvas.motionExtract':'动作提取',
    }[key] || key),
};
vm.createContext(context);
vm.runInContext([
    productionFunction('canvasRunTypes'),
    productionFunction('canvasWorkflowEdges'),
    productionFunction('findWorkflowCycle'),
    productionFunction('workflowCycleLabels'),
    productionFunction('computeCascadeOrder'),
    productionFunction('upstreamNodeIds'),
    productionFunction('resolveCascadeLoop'),
    productionFunction('runNodeCascade'),
    productionFunction('cascadeBtnHtml'),
    'this.findWorkflowCycle = findWorkflowCycle;',
    'this.workflowCycleLabels = workflowCycleLabels;',
    'this.computeCascadeOrder = computeCascadeOrder;',
    'this.resolveCascadeLoop = resolveCascadeLoop;',
    'this.runNodeCascade = runNodeCascade;',
    'this.cascadeBtnHtml = cascadeBtnHtml;',
].join('\n'), context);

assert.match(productionFunction('runNodeCascade'), /await confirmWorkflowCycleBeforeRun\(nodeId\)/);
assert.match(productionFunction('cascadeBtnHtml'), /findWorkflowCycle\(node\.id\)/);

// Break caught: a feedback edge must be reported deterministically while cascade ordering remains finite and unique.
assert.deepEqual(
    Array.from(context.findWorkflowCycle('video')),
    ['llm','video','llm'],
);
assert.deepEqual(
    Array.from(context.workflowCycleLabels(['llm','video','llm'])),
    ['LLM','视频生成','LLM'],
);
const order = Array.from(context.computeCascadeOrder('video'));
assert.equal(new Set(order).size, order.length);
assert.deepEqual(new Set(order), new Set(['llm','video']));

// Break caught: cyclic workflows must still expose the one-click action without a terminal generator.
context.isTerminalGenerator = () => false;
context.isCascadeActive = () => false;
assert.match(context.cascadeBtnHtml(nodes[1]), /data-cascade="video"/);

vm.runInContext([
    productionFunction('confirmWorkflowCycleBeforeRun'),
    'this.confirmWorkflowCycleBeforeRun = confirmWorkflowCycleBeforeRun;',
].join('\n'), context);

(async () => {
    let confirmCalls = 0;
    context.confirmWorkflowCycleRun = async cycle => {
        confirmCalls += 1;
        assert.deepEqual(Array.from(cycle), ['llm','video','llm']);
        return false;
    };
    assert.equal(await context.confirmWorkflowCycleBeforeRun('video'), false);
    assert.equal(confirmCalls, 1);

    connections.splice(1, 1);
    assert.equal(await context.confirmWorkflowCycleBeforeRun('video'), true);
    assert.equal(confirmCalls, 1);
    assert.deepEqual(Array.from(context.findWorkflowCycle('video')), []);
    connections.splice(1, 0, {id:'feedback', from:'video', to:'llm'});

    const started = [];
    let cascadeBegins = 0;
    context.beginCascade = () => {
        cascadeBegins += 1;
        return {message:'', currentNodeId:'', currentRoundLabel:''};
    };
    context.refreshNodes = () => {};
    context.cascadeUiNodeIds = () => [];
    context.ensureCascadeActive = () => {};
    context.finalizeCascade = () => {};
    context.runCascadeNodeWithLoopContext = async node => { started.push(node.id); };

    // Break caught: cancelling the cycle confirmation must not initialize or run any node.
    await context.runNodeCascade('video');
    assert.deepEqual(started, []);
    assert.equal(cascadeBegins, 0);

    // Break caught: confirmation must retain the existing finite, unique cascade order.
    context.confirmWorkflowCycleRun = async () => true;
    await context.runNodeCascade('video');
    assert.deepEqual(started, ['llm','video']);
    assert.equal(new Set(started).size, started.length);
    assert.equal(cascadeBegins, 1);

    const loop = {id:'loop', type:'loop', count:3, mode:'serial', loopStart:1};
    nodes.push(loop);
    connections.push({id:'loop-upstream', from:'loop', to:'llm'});
    context.loopCount = node => node.count;
    context.loopCascadeContextForRound = (_loop, round, total) => ({index:round, total, nodeId:'loop'});
    context.loopInputVideoRefs = () => [];
    context.cascadeParallelLimit = () => 3;
    context.runLimitedCascadeRounds = async (rounds, _limit, runner) => Promise.allSettled(rounds.map(runner));

    const startsByMode = {};
    const cascadeBeginsByMode = {};
    for(const mode of ['serial', 'parallel']){
        loop.mode = mode;
        started.length = 0;
        cascadeBegins = 0;

        await context.runNodeCascade('video');
        startsByMode[mode] = [...started];
        cascadeBeginsByMode[mode] = cascadeBegins;
    }

    // Break caught: confirmed serial and parallel feedback cycles must each override an upstream loop to one pass.
    assert.deepEqual(startsByMode, {
        serial:['llm','video'],
        parallel:['llm','video'],
    });
    assert.deepEqual(cascadeBeginsByMode, {serial:1, parallel:1});

    // Break caught: cyclic one-click copy must not advertise loop rounds that confirmation will suppress.
    assert.doesNotMatch(context.cascadeBtnHtml(nodes[1]), /×\s*3/);
    assert.doesNotMatch(productionFunction('runNodeCascade'), /typeof confirmWorkflowCycleBeforeRun/);

    const modalClassList = {
        open:false,
        add(name){ if(name === 'open') this.open = true; },
        remove(name){ if(name === 'open') this.open = false; },
    };
    const modalContext = {
        workflowCycleModalElement:{classList:modalClassList},
        workflowCyclePathElement:{textContent:''},
        workflowCycleLabels:cycle => cycle,
        refreshIcons:() => {},
        window:{confirm:() => false},
        Promise, Boolean,
    };
    vm.createContext(modalContext);
    vm.runInContext([
        'const workflowCycleModal = workflowCycleModalElement;',
        'const workflowCyclePath = workflowCyclePathElement;',
        'let workflowCycleResolver = null;',
        productionFunction('closeWorkflowCycleModal'),
        productionFunction('confirmWorkflowCycleRun'),
        'this.closeWorkflowCycleModal = closeWorkflowCycleModal;',
        'this.confirmWorkflowCycleRun = confirmWorkflowCycleRun;',
        'this.workflowCycleResolverValue = () => workflowCycleResolver;',
    ].join('\n'), modalContext);

    // Break caught: opening a replacement modal settles the prior promise and closing clears its resolver exactly once.
    const firstConfirmation = modalContext.confirmWorkflowCycleRun(['llm','video','llm']);
    const secondConfirmation = modalContext.confirmWorkflowCycleRun(['video','llm','video']);
    assert.equal(await firstConfirmation, false);
    modalContext.closeWorkflowCycleModal(true);
    modalContext.closeWorkflowCycleModal(false);
    assert.equal(await secondConfirmation, true);
    assert.equal(modalContext.workflowCycleResolverValue(), null);
    assert.equal(modalClassList.open, false);

    console.log('canvas-workflow-cycle-guard: passed');
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
