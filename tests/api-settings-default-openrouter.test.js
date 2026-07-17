const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function fakeElement() {
    return {
        style: {},
        classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
        addEventListener() {},
        querySelector() { return null; },
        querySelectorAll() { return []; },
        closest() { return null; },
        setAttribute() {},
        removeAttribute() {},
        disabled: false,
        hidden: false,
        value: '',
        textContent: '',
        innerHTML: '',
        placeholder: ''
    };
}

async function loadApiSettingsWith(providers) {
    const elements = new Map();
    const document = {
        body: fakeElement(),
        getElementById(id) {
            if (!elements.has(id)) elements.set(id, fakeElement());
            return elements.get(id);
        },
        querySelector() { return null; },
        querySelectorAll() { return []; },
        addEventListener() {}
    };
    const window = {
        addEventListener() {},
        parent: { postMessage() {} },
        top: { postMessage() {} },
        location: { href: '' }
    };
    const context = {
        document,
        window,
        fetch: async () => ({ json: async () => ({ providers }) }),
        URL,
        console,
        setTimeout,
        clearTimeout,
        alert() {},
        confirm() { return true; }
    };
    context.globalThis = context;
    const sourcePath = path.resolve(__dirname, '..', 'static', 'js', 'api-settings.js');
    const source = fs.readFileSync(sourcePath, 'utf8') + `
        globalThis.__apiSettingsTest = {
            loadProviders,
            selectedId: () => selectedId,
            recommendInlineOpen: () => recommendInlineOpen
        };
    `;
    vm.runInNewContext(source, context, { filename: sourcePath });
    await context.__apiSettingsTest.loadProviders();
    return context.__apiSettingsTest;
}

async function main() {
    const state = await loadApiSettingsWith([
        { id: 'modelscope', name: 'ModelScope', base_url: 'https://api-inference.modelscope.cn/v1', protocol: 'openai', enabled: true },
        { id: 'lingjing', name: '灵境API', base_url: 'https://apistudio.vip', protocol: 'openai', enabled: true },
        { id: 'custom-api', name: 'openrouter', base_url: 'https://openrouter.ai/api/v1', protocol: 'openai', enabled: true }
    ]);

    assert.equal(state.selectedId(), 'custom-api', 'OpenRouter should be selected when API settings loads');
    assert.equal(state.recommendInlineOpen(), false, 'recommended APIs should not open automatically');
    console.log('api-settings-default-openrouter: passed');
}

main().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
