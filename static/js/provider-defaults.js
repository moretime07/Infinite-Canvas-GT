(function(root, factory){
    const api = factory();
    if(typeof module === 'object' && module.exports) module.exports = api;
    if(root) root.ProviderDefaults = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function(){
    const CAPABILITIES = new Set(['image_models', 'chat_models', 'video_models']);
    function compatibleProviders(providers, capability, excludeIds=[]){
        const excluded = new Set((excludeIds || []).map(String));
        if(!CAPABILITIES.has(capability)) return [];
        return (providers || []).filter(provider => provider && provider.enabled !== false
            && !excluded.has(String(provider.id || ''))
            && Array.isArray(provider[capability]) && provider[capability].length > 0);
    }
    function isOpenRouter(provider){
        const name = String(provider?.name || '').toLowerCase();
        const base = String(provider?.base_url || '').toLowerCase();
        return name.includes('openrouter') || base.includes('openrouter.ai');
    }
    function pickProvider(providers, options={}){
        const capability = options.capability || 'image_models';
        const compatible = compatibleProviders(providers, capability, options.excludeIds || []);
        const requestedId = String(options.requestedId || '');
        return compatible.find(provider => String(provider.id || '') === requestedId)
            || compatible.find(provider => provider.primary === true)
            || compatible.find(isOpenRouter)
            || compatible[0]
            || null;
    }
    function pickModel(provider, capability, requestedModel=''){
        const models = Array.isArray(provider?.[capability]) ? provider[capability].filter(Boolean) : [];
        const requested = String(requestedModel || '');
        const id = models.includes(requested) ? requested : (models[0] || '');
        return {id, models};
    }
    return {compatibleProviders, isOpenRouter, pickProvider, pickModel};
});
