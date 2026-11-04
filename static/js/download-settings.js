(() => {
    const form = document.getElementById('proxyForm');
    const fields = document.getElementById('proxyFields');
    const enabled = document.getElementById('proxyEnabled');
    const uploadEnabled = document.getElementById('proxyUploadEnabled');
    const protocol = document.getElementById('proxyProtocol');
    const host = document.getElementById('proxyHost');
    const port = document.getElementById('proxyPort');
    const status = document.getElementById('proxyStatus');
    const test = document.getElementById('proxyTest');
    let editable = false;
    let busy = false;
    let supportsUploadProxy = false;

    function show(message, kind = '') {
        status.textContent = message;
        status.dataset.kind = kind;
    }
    function values() {
        return {enabled:enabled.checked, ...(supportsUploadProxy ? {upload_enabled:uploadEnabled.checked} : {}),
            protocol:protocol.value, host:host.value.trim(), port:Number(port.value)};
    }
    async function request(path, method = 'GET', payload) {
        const response = await fetch('/api/settings/download-proxy' + path, {
            method, cache:'no-store',
            headers: {'Content-Type':'application/json', 'X-Infinite-Canvas-Download-Proxy':'1'},
            ...(payload ? {body:JSON.stringify(payload)} : {}),
        });
        const data = await response.json().catch(() => ({}));
        if(!response.ok) {
            const message = response.status === 404 ? '后台尚未加载传输代理功能，请重启后台后重新打开设置。'
                : typeof data.detail === 'string' ? data.detail
                : response.status === 422 ? '请检查代理协议、主机名和端口（1–65535），不要在主机名里填写完整 URL。'
                : '请求失败，请检查后台是否正在运行。';
            throw new Error(message);
        }
        return data;
    }
    async function run(path, method) {
        if(!editable || busy || !form.reportValidity()) return;
        const payload = values();
        busy = true;
        fields.disabled = true;
        show(path ? '正在通过代理测试 HTTPS 连接（最长约 10 秒）…' : '正在保存…');
        try {
            const data = await request(path, method, payload);
            show((data.ok ? `连接成功，${data.latency_ms} ms。` : '') + data.message, 'success');
        } catch(error) { show(error.message, 'error'); }
        finally { busy = false; fields.disabled = !editable; }
    }
    form.addEventListener('submit', event => { event.preventDefault(); run('', 'PUT'); });
    test.addEventListener('click', () => run('/test', 'POST'));
    form.addEventListener('input', () => { if(!busy && editable) show('设置已修改，点击保存后才生效。'); });
    request('').then(data => {
        editable = data.can_edit === true;
        fields.disabled = !editable;
        if(!editable) {
            show('请在运行后台的电脑上，用 http://127.0.0.1:3000 打开此设置。局域网客户端不能修改服务器代理。');
            return;
        }
        enabled.checked = data.settings.enabled;
        supportsUploadProxy = typeof data.settings.upload_enabled === 'boolean';
        uploadEnabled.checked = data.settings.upload_enabled === true;
        uploadEnabled.disabled = !supportsUploadProxy;
        protocol.value = data.settings.protocol;
        host.value = data.settings.host;
        port.value = data.settings.port;
        const warning = data.warning || (!supportsUploadProxy ? '后台尚未加载素材上传代理功能，请重启后台后刷新此页；仅关闭网页不会重启后台。' : '');
        show(warning || `视频下载代理：${enabled.checked ? '已启用' : '未启用'}；素材上传代理：${uploadEnabled.checked ? '已启用' : '未启用'}。`, warning ? 'error' : '');
    }).catch(error => show(error.message, 'error'));
})();
