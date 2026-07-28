const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '..', 'static', 'js', 'canvas.js'), 'utf8');

function productionFunction(name){
    const start = source.indexOf(`function ${name}(`);
    assert.notEqual(start, -1, `canvas.js should define ${name}`);
    const next = source.indexOf('\nfunction ', start + 1);
    return source.slice(start, next === -1 ? source.length : next);
}

function helperContext(){
    const context = {};
    vm.createContext(context);
    vm.runInContext([
        productionFunction('nodeOutputPorts'),
        productionFunction('normalizedFromPort'),
        productionFunction('connectionKey'),
        'this.nodeOutputPorts = nodeOutputPorts;',
        'this.normalizedFromPort = normalizedFromPort;',
        'this.connectionKey = connectionKey;',
    ].join('\n'), context);
    return context;
}

// Break caught: treating a legacy connection as a named one would change its saved shape and dedupe identity.
const helpers = helperContext();
assert.equal(helpers.normalizedFromPort({id:'legacy', from:'source', to:'target'}), '');
assert.equal(helpers.connectionKey('source', 'target'), 'source\u0000target\u0000');
assert.equal(helpers.connectionKey('source', 'target', 'depth'), 'source\u0000target\u0000depth');
assert.notEqual(helpers.connectionKey('source', 'target', 'depth'), helpers.connectionKey('source', 'target', 'pose'));

// Break caught: motion nodes must expose both named outputs while disabled branches cannot be dragged.
assert.deepEqual(
    JSON.parse(JSON.stringify(helpers.nodeOutputPorts({type:'motionExtract', depthEnabled:true, poseEnabled:false}))),
    [
        {name:'depth', label:'DEPTH', disabled:false},
        {name:'pose', label:'POSE', disabled:true},
    ]
);
assert.deepEqual(JSON.parse(JSON.stringify(helpers.nodeOutputPorts({type:'image'}))), []);

// Break caught: persistence must retain the selected named source output and omit the legacy field.
const saved = JSON.stringify({
    connections:[
        {id:'legacy', from:'image', to:'video'},
        {id:'depth-link', from:'motion', to:'video', fromPort:'depth'},
    ],
});
const reloaded = JSON.parse(saved).connections;
assert.equal(Object.hasOwn(reloaded[0], 'fromPort'), false);
assert.equal(reloaded[1].fromPort, 'depth');

function connectionValidationContext(){
    const context = {
        nodes:[
            {id:'motion', type:'motionExtract', depthEnabled:true, poseEnabled:false},
            {id:'video', type:'video'},
            {id:'output', type:'output'},
        ],
        connections:[{id:'depth-link', from:'motion', to:'video', fromPort:'depth'}],
        CANVAS_GENERATOR_TYPES:['video'],
        CANVAS_MEDIA_OUTPUT_TYPES:['video'],
        wouldCreateGeneratorCycle:() => false,
    };
    vm.createContext(context);
    vm.runInContext([
        productionFunction('canConnect'),
        productionFunction('sanitizeConnections'),
        'this.canConnect = canConnect;',
        'this.sanitizeConnections = sanitizeConnections;',
    ].join('\n'), context);
    return context;
}

// Break caught: generic connection validation must not discard a saved named motion-output link.
const validation = connectionValidationContext();
assert.equal(validation.canConnect('motion', 'video'), true);
assert.equal(validation.canConnect('video', 'motion'), true);
assert.equal(validation.canConnect('motion', 'output'), true);
validation.sanitizeConnections();
assert.deepEqual(JSON.parse(JSON.stringify(validation.connections)), [
    {id:'depth-link', from:'motion', to:'video', fromPort:'depth'},
]);

function portPointContext(){
    const context = {
        nodes:[{id:'motion', type:'motionExtract', x:10, y:20, w:200, h:120}],
        CSS:{escape:value => value},
        screenToWorld:(x, y) => ({x, y}),
        nodesEl:{
            querySelector:() => ({
                offsetWidth:200,
                offsetHeight:120,
                querySelector:selector => {
                    const top = selector.includes('data-port-name="depth"') ? 40
                        : selector.includes('data-port-name="pose"') ? 80 : 60;
                    return {getBoundingClientRect:() => ({left:200, top, width:20, height:20})};
                },
            }),
        },
    };
    vm.createContext(context);
    vm.runInContext(`${productionFunction('portPoint')}\nthis.portPoint = portPoint;`, context);
    return context;
}

// Break caught: two visible named outputs must start their links from different physical anchors.
const anchors = portPointContext();
assert.notDeepEqual(
    JSON.parse(JSON.stringify(anchors.portPoint('motion', 'out', 'depth'))),
    JSON.parse(JSON.stringify(anchors.portPoint('motion', 'out', 'pose')))
);

function renderContext(){
    const portCalls = [];
    const appended = [];
    const context = {
        connections:[{id:'depth-link', from:'motion', to:'video', fromPort:'depth'}],
        linksEl:{innerHTML:'', appendChild:item => appended.push(['link', item])},
        linkControlsEl:{innerHTML:'', appendChild:item => appended.push(['control', item])},
        tempLink:null,
        canResolvePort:() => true,
        normalizedFromPort:helpers.normalizedFromPort,
        portPoint:(id, kind, portName='') => {
            portCalls.push([id, kind, portName]);
            return kind === 'out' ? {x:10, y:20} : {x:100, y:200};
        },
        pathEl:(x1, y1, x2, y2, cls) => ({x1, y1, x2, y2, cls}),
        linkDeleteButton:(connection, a, b) => ({connection, a, b}),
        linkHitEl:(x1, y1, x2, y2, id) => ({x1, y1, x2, y2, id}),
        renderKnifeTrail:() => {},
    };
    vm.createContext(context);
    vm.runInContext(`${productionFunction('renderLinks')}\nthis.renderLinks = renderLinks;`, context);
    context.renderLinks();
    return {portCalls, appended};
}

// Break caught: renderer using the default output anchor would visually merge depth and pose links.
const rendered = renderContext();
assert.deepEqual(rendered.portCalls[0], ['motion', 'out', 'depth']);
assert.equal(rendered.appended.length, 3);

function dragContext(poseEnabled=true, connectToNode=true){
    const context = {
        nodes:[
            {id:'motion', type:'motionExtract', depthEnabled:true, poseEnabled},
            {id:'legacy', type:'image'},
            {id:'video', type:'video'},
        ],
        connections:[],
        syncCalls:[],
        CANVAS_GENERATOR_TYPES:['video'],
        portPoint:() => ({x:0, y:0}),
        screenToWorld:(x, y) => ({x, y}),
        nearestPort:() => connectToNode ? ({dataset:{}, closest:() => ({dataset:{id:'video'}})}) : null,
        canConnect:() => true,
        nodeOutputPorts:helpers.nodeOutputPorts,
        normalizedFromPort:helpers.normalizedFromPort,
        connectionKey:helpers.connectionKey,
        uid:prefix => `${prefix}-${context.connections.length + 1}`,
        pushUndo:() => {},
        syncLatestGeneratedOutputToConnection:(...args) => { context.syncCalls.push(args); },
        syncGeneratorInputs:() => {},
        scheduleSave:() => {},
        render:() => {},
        openLinkCreateMenu:() => {},
        renderLinks:() => {},
        window:{onmousemove:null, onmouseup:null},
    };
    vm.createContext(context);
    vm.runInContext(`${productionFunction('startLink')}\nthis.startLink = startLink;`, context);
    return context;
}

function finishLink(context, originId, originPort=''){
    context.startLink({stopPropagation:() => {}}, originId, 'out', originPort);
    context.window.onmouseup({clientX:100, clientY:100});
}

// Break caught: pair-only duplicate checks reject the other named result, and exact duplicates create duplicate edges.
const drag = dragContext();
finishLink(drag, 'motion', 'depth');
finishLink(drag, 'motion', 'pose');
finishLink(drag, 'motion', 'depth');
assert.deepEqual(
    JSON.parse(JSON.stringify(drag.connections)),
    [
        {id:'c-1', from:'motion', to:'video', fromPort:'depth'},
        {id:'c-2', from:'motion', to:'video', fromPort:'pose'},
    ]
);
assert.deepEqual(JSON.parse(JSON.stringify(drag.syncCalls.slice(0, 2))), [
    ['motion', 'video', 'depth'],
    ['motion', 'video', 'pose'],
]);

// Break caught: starting from a legacy output must never add an empty fromPort field.
finishLink(drag, 'legacy');
assert.deepEqual(
    JSON.parse(JSON.stringify(drag.connections[2])),
    {id:'c-3', from:'legacy', to:'video'}
);

// Break caught: disabled named outputs must not create a drag or connection.
const disabled = dragContext(false);
disabled.startLink({stopPropagation:() => {}}, 'motion', 'out', 'pose');
assert.equal(disabled.window.onmouseup, null);
assert.equal(disabled.connections.length, 0);

// Break caught: dropping a named motion result onto empty canvas must create an Output node without losing its branch identity.
const blank = dragContext(true, false);
finishLink(blank, 'motion', 'depth');
assert.deepEqual(JSON.parse(JSON.stringify(blank.connections)), [
    {id:'c-1', from:'motion', to:'out-1', fromPort:'depth'},
]);
assert.deepEqual(JSON.parse(JSON.stringify(blank.syncCalls)), [
    ['motion', 'out-1', 'depth'],
]);
assert.equal(blank.nodes.find(node => node.id === 'out-1')?.type, 'output');

console.log('Canvas motion output-port tests passed');
