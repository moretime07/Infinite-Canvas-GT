const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '..', 'static', 'js', 'canvas.js'), 'utf8');

function productionFunction(name){
    const start = source.indexOf(`function ${name}(`);
    assert.notEqual(start, -1, `canvas.js should define ${name}`);
    const nextFunction = source.indexOf('\nfunction ', start + 1);
    const next = nextFunction >= 0 ? nextFunction : source.length;
    return source.slice(start, next);
}

const helperContext = {};
vm.createContext(helperContext);
vm.runInContext([
    productionFunction('isMiddleMouseButton'),
    productionFunction('isMiddleMouseHeld'),
    'this.isMiddleMouseButton = isMiddleMouseButton;',
    'this.isMiddleMouseHeld = isMiddleMouseHeld;',
].join('\n'), helperContext);

assert.equal(helperContext.isMiddleMouseButton({button:1}), true, 'middle mouse down should be allowed to start canvas pan');
assert.equal(helperContext.isMiddleMouseButton({button:0}), false, 'left mouse down must not start canvas pan');
assert.equal(helperContext.isMiddleMouseHeld({buttons:4}), true, 'pan may continue while the middle button is held');
assert.equal(helperContext.isMiddleMouseHeld({buttons:1}), false, 'left-button drag must not continue canvas pan');
assert.equal(helperContext.isMiddleMouseHeld({buttons:0}), false, 'canvas pan must stop when the middle button is released');

const handlerStart = source.indexOf('board.onmousedown = e => {');
assert.notEqual(handlerStart, -1, 'canvas.js should define the board mousedown handler');
const handlerEnd = source.indexOf('\n};', handlerStart);
assert.notEqual(handlerEnd, -1, 'board mousedown handler should have a closing boundary');
const handlerSource = source.slice(handlerStart, handlerEnd);

assert.match(handlerSource, /if\s*\(isMiddleMouseButton\(e\)\)\s*\{\s*startBoardPan\(e\)/, 'board pan must start through the middle-button guard');
assert.doesNotMatch(handlerSource, /startBoardPan\(e,\s*\{clearSelectionOnClick:true\}\)/, 'plain left blank-space input must never start board pan');
assert.match(handlerSource, /selected\.clear\(\)/, 'plain left blank-space click should still clear the current selection');

const panSource = productionFunction('startBoardPan');
assert.match(panSource, /if\s*\(!isMiddleMouseHeld\(e2\)\)\s*\{\s*endDrag\(e2\)/, 'active pan must end defensively when the middle-button bit disappears');

console.log('Canvas middle-button pan tests passed');
