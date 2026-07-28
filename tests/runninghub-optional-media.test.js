const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '..', 'static', 'js', 'canvas.js'), 'utf8');

function productionFunction(name){
    const asyncStart = source.indexOf(`async function ${name}(`);
    const start = asyncStart >= 0 ? asyncStart : source.indexOf(`function ${name}(`);
    assert.notEqual(start, -1, `canvas.js should define ${name}`);
    const nextFunction = source.indexOf('\nfunction ', start + 1);
    const nextAsyncFunction = source.indexOf('\nasync function ', start + 1);
    const candidates = [nextFunction, nextAsyncFunction].filter(index => index >= 0);
    const next = candidates.length ? Math.min(...candidates) : source.length;
    return source.slice(start, next);
}

const fields = [
    {
        nodeId:'1', fieldName:'image', fieldType:'IMAGE', fieldValue:'template-required.png',
        descriptionEn:'Upload image 1 (required)', fieldData:'[["template-required.png", "None"], {"image_upload": true}]',
    },
    {
        nodeId:'2', fieldName:'image', fieldType:'IMAGE', fieldValue:'template-optional-a.png',
        descriptionEn:'Upload image 2 (Optional)', fieldData:'[["template-optional-a.png", "None"], {"image_upload": true}]',
    },
    {
        nodeId:'3', fieldName:'image', fieldType:'IMAGE', fieldValue:'template-optional-b.png',
        descriptionEn:'Upload image 3 (optional)', fieldData:'[["template-optional-b.png", "None"], {"image_upload": true}]',
    },
    {
        nodeId:'4', fieldName:'image', fieldType:'IMAGE', fieldValue:'template-unsupported.png',
        descriptionEn:'Upload image 4 (optional)', fieldData:'[["template-unsupported.png"], {"image_upload": true}]',
    },
    {
        nodeId:'5', fieldName:'prompt', fieldType:'STRING', fieldValue:'template prompt',
        descriptionEn:'prompt', fieldData:'["STRING", {"default": "", "multiline": true}]',
    },
];

const fieldIndexes = {'1::image':0, '2::image':1, '3::image':2, '4::image':3};
const context = {
    rhActiveFields:() => fields,
    rhFieldIndexes:() => fieldIndexes,
    rhCurrentKind:() => 'app',
    rhFieldKind:field => String(field.fieldType || '').toLowerCase(),
    rhFieldRole:field => field.fieldName === 'prompt' ? 'prompt' : String(field.fieldType || '').toLowerCase(),
    rhParamKey:(nodeId, fieldName) => `${nodeId}::${fieldName}`,
    rhFieldValue:(_node, field, media) => field.fieldName === 'prompt'
        ? media.prompt
        : media.image[fieldIndexes[`${field.nodeId}::${field.fieldName}`]]?.url || field.fieldValue,
    rhUploadValueIfNeeded:async value => value,
};
vm.createContext(context);
vm.runInContext([
    productionFunction('rhExtractFieldOptions'),
    productionFunction('rhEmptyMediaValue'),
    productionFunction('rhRequiredLabel'),
    productionFunction('rhFieldIsRequired'),
    productionFunction('rhPromptLimitForNode'),
    productionFunction('rhLimitPromptValue'),
    productionFunction('rhBuildNodeInfoList'),
    'this.build = rhBuildNodeInfoList;',
].join('\n'), context);

(async () => {
    const longPrompt = '角'.repeat(2895);
    const result = JSON.parse(JSON.stringify(await context.build({webappId:'2058790334674587649'}, {
        image:[{url:'canvas-image.png'}], video:[], audio:[], prompt:longPrompt, refs:[],
    })));
    assert.deepEqual(result, [
        {nodeId:'1', fieldName:'image', fieldValue:'canvas-image.png'},
        {nodeId:'2', fieldName:'image', fieldValue:'None'},
        {nodeId:'3', fieldName:'image', fieldValue:'None'},
        {nodeId:'5', fieldName:'prompt', fieldValue:'角'.repeat(2048)},
    ], 'unused optional media slots must use the provider-supported empty sentinel, while unsupported slots stay omitted');

    const budgetResult = JSON.parse(JSON.stringify(await context.build({webappId:'2059985306476179457'}, {
        image:[{url:'canvas-image.png'}], video:[], audio:[], prompt:longPrompt, refs:[],
    })));
    assert.equal(
        budgetResult.find(item => item.fieldName === 'prompt').fieldValue.length,
        2048,
        'the budget Omni video app must enforce the same prompt limit as the original app',
    );

    await assert.rejects(
        () => context.build({}, {image:[], video:[], audio:[], prompt:'', refs:[]}),
        /required|必填/i,
        'a missing required app image must fail before a paid submission',
    );

    console.log('RunningHub optional media tests passed');
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
