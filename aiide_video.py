"""Aiide video request/response contract (official Apifox 464722026/027/029/032).

Seedance uses /v1/video/generations and metadata.content, not the generic
/v1/videos/generations fallback. Do not follow POST redirects to website routes.
"""
import ipaddress
import json
import re
from urllib.parse import quote, urlsplit

from fastapi import HTTPException

HOSTS = {"aiide.com", "www.aiide.com"}
FAILED = {"FAILURE", "FAILED", "FAIL", "ERROR", "CANCELED", "CANCELLED", "EXPIRED", "TIMEOUT"}
SUCCESS = {"SUCCESS", "SUCCEEDED", "COMPLETED", "COMPLETE", "FINISHED", "FINISH"}


def is_provider(provider):
    try:
        return urlsplit(str((provider or {}).get("base_url") or "")).hostname in HOSTS
    except ValueError:
        return False


def model_format(model):
    if str(model).startswith(("doubao-seedance-", "dreamina-seedance-")):
        return "seedance"
    if str(model).startswith("gemini-veo-"):
        return "veo"
    if str(model) in {"sora-v3-fast", "sora-v3-pro"}:
        return "sora"
    raise HTTPException(400, "此 Aiide 视频模型尚未适配，请选择 Seedance、gemini-veo 或 sora-v3 模型。")


def endpoints(provider, model):
    mode = model_format(model)
    configured = str(provider.get("video_base_url") or provider.get("base_url") or "").strip().rstrip("/")
    try:
        parsed = urlsplit(configured)
        port = parsed.port
    except ValueError as exc:
        raise HTTPException(400, "Aiide 视频地址格式不正确。") from exc
    if (parsed.scheme != "https" or parsed.hostname not in HOSTS or port not in (None, 443)
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise HTTPException(400, "Aiide 视频地址需使用 https://www.aiide.com/v1 或其官方视频端点，不可携带密钥参数。")
    expected = "/v1/video/generations" if mode == "seedance" else "/v1/videos"
    if parsed.path not in {"", "/v1", expected}:
        raise HTTPException(400, f"当前模型的 Aiide 视频接口路径应为 {expected}；请修改视频 API 地址，或留空自动选择。")
    origin = f"https://{parsed.netloc}"
    return origin + expected, origin + "/v1/video/generations"


def raise_for_redirect(response):
    if 300 <= response.status_code < 400:
        # Never echo arbitrary redirect URLs/query strings or forward credentials.
        raise HTTPException(502, f"上游视频接口发生 HTTP {response.status_code} 重定向，并非模型 JSON 响应。"
                            "请检查视频 API 地址及接口路径；未向重定向地址转发密钥或重复提交生成。")


def media_url(value, kind, resolve_reference=None):
    value = str(value or "").strip()
    if value.startswith("mat_"):
        return "asset://" + value
    if value.startswith("asset://"):
        return value
    if value.startswith(("/assets/", "/output/")):
        if resolve_reference:
            value = str(resolve_reference({"url": value, "kind": kind}) or "").strip()
        if not value or value.startswith(("/assets/", "/output/")):
            raise HTTPException(400, "Aiide 参考素材必须是公网链接或 asset:// 素材，不能直接使用本地文件。请在生成前确认上传素材，或配置自己的 PUBLIC_MEDIA_BASE_URL。")
    if value.startswith("data:"):
        raise HTTPException(400, "Aiide 不接受 Base64 素材地址。请将素材保存并重新上传到画布，再确认上传到公网存储；不能把 data: 地址作为素材 URL。")
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise HTTPException(400, "Aiide 参考素材地址格式不正确。") from exc
    if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password:
        try:
            local = not ipaddress.ip_address(parsed.hostname).is_global
        except ValueError:
            local = parsed.hostname.lower() in {"localhost", "localhost.localdomain"} or parsed.hostname.lower().endswith((".localhost", ".local"))
        if local:
            raise HTTPException(400, "Aiide 无法读取本机或局域网素材地址，请使用自己的公网直链或 asset:// 素材。")
        return value
    raise HTTPException(400, "Aiide 参考素材无法读取，请使用公网媒体直链或 asset:// 素材。")


def request_payload(payload, resolve_reference):
    mode = model_format(payload.model)
    images = [ref if isinstance(ref, dict) else vars(ref) for ref in payload.images or []]
    images = [ref for ref in images if ref.get("url")]
    image_urls = [media_url(ref["url"], "image", resolve_reference) for ref in images]
    videos = [media_url(url, "video", resolve_reference) for url in payload.videos or [] if url]
    audios = [media_url(url, "audio", resolve_reference) for url in payload.audios or [] if url]
    body = {"model": payload.model, "prompt": payload.prompt}
    if mode == "seedance":
        content = []
        reference_mode = payload.multimodal or len(images) > 1 or videos or audios
        for ref, url in zip(images, image_urls):
            role = ref.get("role") or ("reference_image" if reference_mode else "first_frame")
            if role not in {"first_frame", "last_frame", "reference_image"}:
                raise HTTPException(400, "Aiide 图片用途仅支持首帧、尾帧和参考图片。")
            content.append({"type": "image_url", "image_url": {"url": url}, "role": role})
        for kind, urls in (("video", videos), ("audio", audios)):
            content.extend({"type": f"{kind}_url", f"{kind}_url": {"url": url}, "role": f"reference_{kind}"} for url in urls)
        roles = [item["role"] for item in content]
        if roles.count("first_frame") > 1 or roles.count("last_frame") > 1 or ("last_frame" in roles and "first_frame" not in roles):
            raise HTTPException(400, "Aiide 首尾帧模式各只能一张图片，且尾帧必须搭配首帧。")
        if any(role in {"first_frame", "last_frame"} for role in roles) and any(role.startswith("reference_") for role in roles):
            raise HTTPException(400, "Aiide 不能混用首尾帧和全能参考素材，请选择一种模式。")
        metadata = {"content": content, "duration": payload.duration, "ratio": payload.aspect_ratio,
                    "generate_audio": payload.generate_audio, "watermark": payload.watermark}
        if payload.resolution and payload.resolution.lower() != "auto":
            metadata["resolution"] = payload.resolution.lower()
        if payload.seed is not None:
            metadata["seed"] = payload.seed
        if payload.camerafixed:
            metadata["camerafixed"] = True
        if payload.return_last_frame:
            metadata["return_last_frame"] = True
        if payload.enhance_prompt or payload.enable_upsample:
            raise HTTPException(400, "Aiide Seedance 文档未声明提示词优化/提升1080p开关，请关闭这两个开关并直接选择目标分辨率。")
        body["metadata"] = metadata
    elif mode == "veo":
        if videos or audios:
            raise HTTPException(400, "Aiide Veo 当前文档只支持图片参考，不支持视频或音频参考。")
        maximum = 3 if "-ref-" in payload.model else 2
        if len(images) > maximum:
            raise HTTPException(400, f"该 Aiide Veo 模型最多支持 {maximum} 张参考图，不会丢弃多余图片。")
        duration = re.search(r"-(\d+)s$", payload.model)
        if duration and int(duration[1]) != payload.duration:
            raise HTTPException(400, f"Aiide Veo 时长由模型名称决定：当前为 {duration[1]} 秒，请同步调整时长。")
        if payload.aspect_ratio not in {"16:9", "9:16"}:
            raise HTTPException(400, "Aiide Veo 请选择 16:9 或 9:16。")
        body["size"] = "720x1280" if payload.aspect_ratio == "9:16" else "1280x720"
        if image_urls:
            body["images"] = image_urls
    else:
        if audios:
            raise HTTPException(400, "Aiide sora-v3 文档未声明音频参考，请使用 Seedance 原生接口。")
        body.update(seconds=str(payload.duration), reference_images=image_urls, reference_videos=videos,
                    video_config={"aspect_ratio": payload.aspect_ratio, "resolution_name": payload.resolution or "720p"})
    return body


def response_json(response, secrets=()):
    raise_for_redirect(response)
    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(502, f"Aiide 视频接口返回非 JSON 响应（HTTP {response.status_code}），请检查接口路径。") from exc
    if not isinstance(data, dict):
        raise HTTPException(502, "Aiide 视频接口返回的 JSON 不是任务对象。")
    if response.status_code >= 400:
        error = data.get("error") or data.get("message") or "请求失败"
        message = str(error.get("message") or error.get("code") or "请求失败") if isinstance(error, dict) else str(error)
        # Some gateways serialize their error object into the outer message string.
        try:
            nested = json.loads(message)
            if isinstance(nested, dict) and nested.get("message"):
                message = str(nested["message"])
        except (ValueError, TypeError):
            pass
        for secret in secrets:
            if secret:
                message = message.replace(secret, "[已隐藏]")
        raise HTTPException(response.status_code, f"Aiide 视频接口错误：{message[:1000]}")
    return data


def result_chain(data):
    chain = [data]
    node = data
    for _ in range(4):
        node = node.get("data")
        if not isinstance(node, dict):
            break
        chain.append(node)
    return chain


def extract_task_id(data):
    return next((str(n.get("task_id") or n.get("id")) for n in result_chain(data)
                 if n.get("task_id") or isinstance(n.get("id"), str)), "")


def task_result(data, secrets=()):
    # Aiide wraps the original provider response in data.data. Only inspect the
    # result chain; never mistake echoed request/reference URLs for output videos.
    chain = result_chain(data)
    task_id = extract_task_id(data)
    for node in chain:
        status = str(node.get("status") or "").upper()
        code = node.get("code")
        numeric_failure = isinstance(code, (int, str)) and str(code).isdigit() and int(code) not in {0, 200}
        if status in FAILED or node.get("error") or node.get("success") is False or numeric_failure:
            error = node.get("error")
            message = node.get("fail_reason") or node.get("message") or (error.get("message") if isinstance(error, dict) else error) or status
            message = str(message)
            for secret in secrets:
                if secret:
                    message = message.replace(secret, "[已隐藏]")
            raise HTTPException(400, f"Aiide 视频生成失败：{message[:1000]}（任务 {task_id}）")
    urls = []
    for node in chain:
        content = node.get("content") if isinstance(node.get("content"), dict) else {}
        for value in (node.get("result_url"), node.get("video_url"), content.get("video_url")):
            if isinstance(value, str) and value.startswith(("https://", "http://")) and value not in urls:
                urls.append(value)
    completed = any(str(node.get("status") or "").upper() in SUCCESS for node in chain)
    if completed and not urls:
        raise HTTPException(502, f"Aiide 任务已结束但没有返回视频地址（任务 {task_id}），请到上游查看，不要重复提交。")
    return task_id, urls


def task_url(query_root, task_id):
    return f"{query_root}/{quote(str(task_id), safe='')}"
