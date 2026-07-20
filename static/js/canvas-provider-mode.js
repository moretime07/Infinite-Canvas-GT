(function(root, factory){
    const api = factory(root?.ProviderDefaults || (typeof require === 'function' ? require('./provider-defaults.js') : null));
    if(typeof module === 'object' && module.exports) module.exports = api;
    if(root) root.CanvasProviderMode = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function(ProviderDefaults){
    const DEFAULT_VALUE = '__default__';
    const mode = node => node?.providerMode === 'default' ? 'default' : 'fixed';
    function select(node, selectedId){
        return selectedId === DEFAULT_VALUE
            ? {providerMode:'default', requestedId:''}
            : {providerMode:'fixed', requestedId:String(selectedId || '')};
    }
    function resolve(node, providers, options){
        const capability = options.capability;
        const providerField = options.providerField;
        const currentMode = mode(node);
        const requestedId = currentMode === 'default' ? '' : String(node?.[providerField] || '');
        const provider = ProviderDefaults.pickProvider(providers, {
            capability,
            requestedId,
            excludeIds:options.excludeIds || []
        });
        const models = Array.isArray(provider?.[capability]) ? provider[capability].filter(Boolean) : [];
        const model = models.includes(node?.model) ? node.model : (models[0] || '');
        const providerId = provider?.id || '';
        return {
            providerMode:currentMode,
            providerId,
            model,
            changed:providerId !== String(node?.[providerField] || '') || model !== String(node?.model || '')
        };
    }
    return {DEFAULT_VALUE, mode, select, resolve};
});
