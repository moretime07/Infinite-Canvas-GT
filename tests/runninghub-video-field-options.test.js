const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function sourceFile(name){
    return fs.readFileSync(path.resolve(__dirname, '..', 'static', 'js', name), 'utf8');
}

function productionFunction(source, name){
    const start = source.indexOf(`function ${name}(`);
    assert.notEqual(start, -1, `${name} should exist in production code`);
    const next = source.indexOf('\nfunction ', start + 1);
    return source.slice(start, next === -1 ? source.length : next);
}

const encodedResolution = {
    fieldName:'resolution',
    fieldType:'LIST',
    fieldValue:'720p',
    fieldData:'[["720p", "1080p", "4k"], {"default": "720p"}]',
};

const canvasSource = sourceFile('canvas.js');
const canvasContext = {
    RH_KNOWN_FIELD_OPTIONS:{resolution:['1k', '2k', '4k', '8k']},
};
vm.createContext(canvasContext);
vm.runInContext(
    `${productionFunction(canvasSource, 'rhExtractFieldOptions')}\nthis.extract = rhExtractFieldOptions;`,
    canvasContext,
);
assert.deepEqual(
    JSON.parse(JSON.stringify(canvasContext.extract(encodedResolution))),
    ['720p', '1080p', '4k'],
    'canvas must use the RunningHub app schema instead of generic image resolutions',
);

vm.runInContext(
    [
        productionFunction(canvasSource, 'rhAppFieldsWithRawSchema'),
        productionFunction(canvasSource, 'rhDefaultValue'),
        productionFunction(canvasSource, 'rhCoerceFieldOption'),
        'this.merge = rhAppFieldsWithRawSchema;',
        'this.coerce = rhCoerceFieldOption;',
    ].join('\n'),
    canvasContext,
);
const mergedFields = JSON.parse(JSON.stringify(canvasContext.merge({
    fields:[
        {...encodedResolution, options:['512', '768', '1024']},
        {nodeId:'25', fieldName:'duration', fieldType:'LIST', fieldValue:'10', options:[]},
    ],
    raw:{nodeInfoList:[
        {nodeId:'25', ...encodedResolution},
        {
            nodeId:'25',
            fieldName:'duration',
            fieldType:'LIST',
            fieldValue:'10',
            fieldData:'[["4", "6", "8", "10"], {"default": "6"}]',
        },
    ]},
})));
assert.deepEqual(JSON.parse(JSON.stringify(canvasContext.extract(mergedFields[0]))), ['720p', '1080p', '4k']);
assert.deepEqual(JSON.parse(JSON.stringify(canvasContext.extract(mergedFields[1]))), ['4', '6', '8', '10']);
assert.equal(canvasContext.coerce(mergedFields[0], '768'), '720p', 'stale image resolution should migrate to the app default');
assert.equal(canvasContext.coerce(mergedFields[0], '1080p'), '1080p', 'valid saved choices must be preserved');

const settingsSource = sourceFile('api-settings.js');
const settingsContext = {
    rhKnownOptionsForField:() => ['512', '768', '1024'],
};
vm.createContext(settingsContext);
vm.runInContext(
    `${productionFunction(settingsSource, 'extractRhEditorFieldOptions')}\nthis.extract = extractRhEditorFieldOptions;`,
    settingsContext,
);
assert.deepEqual(
    JSON.parse(JSON.stringify(settingsContext.extract(encodedResolution))),
    ['720p', '1080p', '4k'],
    'API settings must preserve the exact options returned by RunningHub',
);

vm.runInContext(
    [
        productionFunction(settingsSource, 'rhWorkflowFieldKey'),
        productionFunction(settingsSource, 'rhWorkflowFieldKind'),
        productionFunction(settingsSource, 'normalizeRhWorkflowField'),
        productionFunction(settingsSource, 'rhAppFieldSourceList'),
        productionFunction(settingsSource, 'normalizeFetchedRhAppField'),
        productionFunction(settingsSource, 'normalizeRhAppConfig'),
        'this.normalizeApp = normalizeRhAppConfig;',
    ].join('\n'),
    settingsContext,
);
const normalizedApp = JSON.parse(JSON.stringify(settingsContext.normalizeApp({
    appId:'video-app',
    fields:[{...encodedResolution, options:['512', '768', '1024']}],
    raw:{nodeInfoList:[{nodeId:'25', ...encodedResolution}]},
})));
assert.deepEqual(normalizedApp.fields[0].options, ['720p', '1080p', '4k']);

console.log('RunningHub video field option tests passed');
