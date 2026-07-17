const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const source = fs.readFileSync(path.resolve(__dirname, '..', 'static', 'js', 'canvas.js'), 'utf8');

assert.match(source, /function ensureOutputExportSettings\(node\)/, 'Output nodes should receive export defaults');
assert.match(source, /D:\\\\桌面\\\\1\\\\全能画布图片输出/, 'Image export should default to the requested folder');
assert.match(source, /D:\\\\桌面\\\\1\\\\全能画布视频输出/, 'Video export should default to the requested folder');
assert.match(source, /imageExportFormat.*jpg/, 'Image exports should default to JPG');
assert.match(source, /videoExportFormat.*mp4/, 'Video exports should default to MP4');
assert.match(source, /function renderOutputExportControls\(node\)/, 'Output nodes should render export controls');
assert.match(source, /function exportOutputNodeMedia\(nodeId/, 'Output nodes should be able to export to the local machine');
assert.match(source, /function scheduleOutputNodeAutoExport\(node, items\)/, 'New output media should schedule auto export');

console.log('canvas-output-auto-export: passed');
