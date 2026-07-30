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
    'this.findWorkflowCycle = findWorkflowCycle;',
    'this.workflowCycleLabels = workflowCycleLabels;',
    'this.computeCascadeOrder = computeCascadeOrder;',
].join('\n'), context);

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

// Break caught: a normal acyclic dependency path must not produce a feedback-cycle warning.
connections.splice(1, 1);
assert.deepEqual(Array.from(context.findWorkflowCycle('video')), []);

console.log('canvas-workflow-cycle-guard: passed');
