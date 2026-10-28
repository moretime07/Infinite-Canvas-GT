"""Tencent VOD Cloud API transport and image/video adapters.

Uses TC3 signing (API version 2018-07-17), not TokenHub Bearer keys.
Model metadata is a documented catalog, not an account entitlement list.
"""

import asyncio
import base64
import datetime
import hashlib
import hmac
import json
import re
import time
import threading
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from tencent_vod_catalog import CATALOG, IMAGE_MODELS, VIDEO_MODELS


PROTOCOL = "tencent-vod"
DEFAULT_BASE = "https://vod.tencentcloudapi.com"
HOSTS = {"vod.tencentcloudapi.com", "vod.intl.tencentcloudapi.com"}
MODEL_ID = "wand-vega-image-lite"
VERIFICATION_FILE = Path(__file__).resolve().parent / "data" / "tencent_vod_verifications.json"
_verification_lock = threading.Lock()


def _verification_scope(config, sub_app_id):
    # Bind evidence to the exact credentials/application/endpoint without saving secrets.
    identity = [config.get(key, "") for key in ("base_url", "region", "secret_id", "secret_key", "token")]
    return hashlib.sha256(json.dumps([identity, sub_app_id]).encode()).hexdigest()


def _verification_records():
    try:
        records = json.loads(VERIFICATION_FILE.read_text(encoding="utf-8"))
        return records if isinstance(records, dict) else {}
    except (OSError, ValueError):
        return {}


def model_metadata(config, sub_app_id):
    with _verification_lock:
        records = _verification_records().get(_verification_scope(config, sub_app_id), {})
    return {key: {**value, "verification": "verified" if key in records else "unverified",
                  "verified_at": records.get(key), "source": "vod_documentation"}
            for key, value in CATALOG.items()}


def record_success(config, body, kind):
    model = next((key for key, value in CATALOG.items() if value["kind"] == kind
                  and value["model_name"] == body.get("ModelName")
                  and value["model_version"] == body.get("ModelVersion")), None)
    if not model:
        return
    # Cache failures must never turn a successful paid generation into a retry.
    try:
        with _verification_lock:
            records = _verification_records()
            records.setdefault(_verification_scope(config, body["SubAppId"]), {})[model] = int(time.time())
            VERIFICATION_FILE.parent.mkdir(parents=True, exist_ok=True)
            temporary = VERIFICATION_FILE.with_suffix(".tmp")
            temporary.write_text(json.dumps(records), encoding="utf-8")
            temporary.replace(VERIFICATION_FILE)
    except OSError:
        pass


def model_spec(model, kind):
    if model in {"wand-vega-image-1.0-lite", "WAND-Vega-Image-1.0-Lite"}:
        model = MODEL_ID
    spec = CATALOG.get(model)
    if not spec or spec["kind"] != kind:
        raise HTTPException(400, "该模型尚未适配腾讯云 VOD 的此类节点，请在 API 设置中重新载入并选择对应模型。")
    return spec


def is_provider(provider):
    if provider is None:
        return False
    if not isinstance(provider, dict):
        provider = vars(provider)
    return provider.get("protocol") == PROTOCOL or urlsplit(str(provider.get("base_url") or "")).hostname in HOSTS


def endpoint(base_url):
    parsed = urlsplit(base_url or DEFAULT_BASE)
    if parsed.scheme != "https" or parsed.hostname not in HOSTS or parsed.port not in (None, 443):
        raise HTTPException(400, "腾讯云 VOD 地址应为 https://vod.tencentcloudapi.com 或 https://vod.intl.tencentcloudapi.com")
    return f"https://{parsed.hostname}"


def signed_request(action, payload, secret_id, secret_key, base_url=DEFAULT_BASE, region="ap-guangzhou", token="", timestamp=None):
    if not secret_id or not secret_key:
        raise HTTPException(400, "请配置腾讯云 SecretId 和 SecretKey；TokenHub API Key 不能用于 VOD 签名。")
    base_url = endpoint(base_url)
    host = urlsplit(base_url).hostname
    timestamp = int(time.time() if timestamp is None else timestamp)
    date = datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc).strftime("%Y-%m-%d")
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > 10 * 1024 * 1024:
        raise HTTPException(400, "腾讯云请求体超过 10 MB。请压缩参考图片，或改用公网图片直链。")
    content_type = "application/json; charset=utf-8"
    canonical = "\n".join(["POST", "/", "", f"content-type:{content_type}\nhost:{host}\n", "content-type;host", hashlib.sha256(body).hexdigest()])
    scope = f"{date}/vod/tc3_request"
    to_sign = "\n".join(["TC3-HMAC-SHA256", str(timestamp), scope, hashlib.sha256(canonical.encode()).hexdigest()])
    key = ("TC3" + secret_key).encode()
    for value in (date, "vod", "tc3_request"):
        key = hmac.new(key, value.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    headers = {
        "Authorization": f"TC3-HMAC-SHA256 Credential={secret_id}/{scope}, SignedHeaders=content-type;host, Signature={signature}",
        "Content-Type": content_type, "Host": host,
        "X-TC-Action": action, "X-TC-Version": "2018-07-17", "X-TC-Timestamp": str(timestamp),
        "X-TC-Region": region or "ap-guangzhou",
    }
    if token:
        headers["X-TC-Token"] = token
    return base_url, headers, body


async def call(client, action, payload, config):
    url, headers, body = signed_request(action, payload, **config)
    try:
        response = await client.post(url, headers=headers, content=body)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"连接腾讯云 VOD 失败（{type(exc).__name__}），请检查网络后重试。") from exc
    try:
        data = response.json().get("Response")
    except (ValueError, AttributeError):
        data = None
    if not isinstance(data, dict):
        raise HTTPException(502, f"腾讯云 VOD 返回非标准响应（HTTP {response.status_code}）")
    error = data.get("Error")
    if error:
        code = str(error.get("Code") or "UnknownError")
        message = str(error.get("Message") or "请求失败")
        for secret in (config.get("secret_id"), config.get("secret_key"), config.get("token")):
            if secret:
                message = message.replace(secret, "[已隐藏]")
        status = 403 if code.startswith(("AuthFailure", "UnauthorizedOperation")) else 400
        raise HTTPException(status, f"腾讯云 {action} 失败：{code} · {message}（RequestId: {data.get('RequestId', '')}）")
    if response.status_code >= 400:
        raise HTTPException(502, f"腾讯云 VOD 请求失败（HTTP {response.status_code}）")
    return data


async def list_apps(client, config):
    apps = []
    offset = 0
    while True:
        data = await call(client, "DescribeSubAppIds", {"Offset": offset, "Limit": 200}, config)
        batch = data.get("SubAppIdInfoSet") or []
        apps.extend({"id": item["SubAppId"], "name": item.get("SubAppIdName") or item.get("Name") or str(item["SubAppId"])}
                    for item in batch if item.get("Status") == "On")
        offset += len(batch)
        if not batch or offset >= int(data.get("TotalCount") or offset):
            return apps


async def discover(config, sub_app_id=0):
    async with httpx.AsyncClient(timeout=60) as client:
        apps = []
        if not sub_app_id:
            apps = await list_apps(client, config)
            if len(apps) == 1:
                sub_app_id = apps[0]["id"]
            else:
                return {"ok": False, "status": 200, "protocol": PROTOCOL, "connection_verified": True,
                        "apps": apps, "needs_sub_app": True, "all": [], "total": 0,
                        "message": "鉴权成功，请选择点播应用 SubAppId 后再拉取模型。"}
        await call(client, "DescribeAigcQuotas", {"SubAppId": int(sub_app_id), "QuotaType": "Image", "Limit": 100}, config)
    return {"ok": True, "status": 200, "status_code": 200, "protocol": PROTOCOL,
            "connection_verified": True, "generation_verified": False, "model_source": "vod_documentation",
            "message": "腾讯云签名及点播应用连接验证通过。已载入官方文档模型目录（不是账号授权清单）；未验证的模型需实际生成成功后才标记为已验证。",
            "tencent_sub_app_id": int(sub_app_id), "apps": apps,
            "total": len(CATALOG), "model_count": len(CATALOG), "all": list(CATALOG), "image_models": list(IMAGE_MODELS),
            "video_models": list(VIDEO_MODELS), "chat_models": [], "model_metadata": model_metadata(config, sub_app_id)}


def image_request(prompt, size, model, references, sub_app_id, resolve_reference):
    spec = model_spec(model, "image")
    if not sub_app_id or int(sub_app_id) <= 0:
        raise HTTPException(400, "请先在 API 设置中填写或检测点播应用 SubAppId。")
    refs = [r if isinstance(r, dict) else vars(r) for r in references or []]
    refs = [r for r in refs if str(r.get("url") or "").strip()]
    if spec["max_references"] is not None and len(refs) > spec["max_references"]:
        raise HTTPException(400, f"{spec['label']} 文档规定最多支持 {spec['max_references']} 张参考图片；不会静默丢弃多余图片。")
    if not str(prompt or "").strip() and not refs:
        raise HTTPException(400, "请输入提示词或连接参考图片。")
    body = {"SubAppId": int(sub_app_id), "ModelName": spec["model_name"], "ModelVersion": spec["model_version"],
            "Prompt": prompt, "OutputConfig": {"StorageMode": "Temporary"}}
    size = str(size or "2K").strip()
    match = re.fullmatch(r"(\d+)[xX*](\d+)", size)
    if spec["id"] != MODEL_ID:
        body.update(image_output(spec, size))
    elif match:
        width, height = map(int, match.groups())
        if width <= 0 or height <= 0:
            raise HTTPException(400, "图片宽高必须大于零。")
        body["ExtInfo"] = json.dumps({"AdditionalParameters": json.dumps({"size": f"{width}x{height}"})})
    elif size.upper() in {"1K", "2K", "4K"}:
        body["OutputConfig"].update(Resolution=size.upper(), AspectRatio="1:1")
    else:
        raise HTTPException(400, "WAND VOD 尺寸请使用 1K / 2K / 4K 或 宽x高。")
    files = []
    for ref in refs:
        value = str(ref["url"]).strip()
        if value.startswith(("/assets/", "/output/")):
            try:
                value = resolve_reference(ref)
            except OSError as exc:
                raise HTTPException(400, "本地参考图片不存在或无法读取，请重新上传。") from exc
        if value.startswith("data:image/") and ";base64," in value:
            encoded = value.split(",", 1)[1]
            try:
                base64.b64decode(encoded, validate=True)
            except ValueError as exc:
                raise HTTPException(400, "参考图片 Base64 数据无效。") from exc
            files.append({"Type": "Base64", "Base64": encoded})
        elif value.startswith(("http://", "https://")):
            files.append({"Type": "Url", "Url": value})
        else:
            raise HTTPException(400, "参考图片无法读取，请重新上传图片或使用公网图片直链。")
    if files:
        body["FileInfos"] = files
    return body


def image_output(spec, size):
    match = re.fullmatch(r"(\d+)[xX*](\d+)", size)
    output = {"StorageMode": "Temporary"}
    if spec["model_name"] == "Hunyuan":
        width, height = map(int, match.groups()) if match else (1024, 1024)
        if not match and size.upper() != "1K":
            raise HTTPException(400, "混元 3.0 请使用 1K 或自定义宽高；最大总像素为 1024×1024。")
        if min(width, height) < 512 or max(width, height) > 2048 or width * height > 1024 * 1024:
            raise HTTPException(400, "混元 3.0 宽高需为 512–2048，且总像素不超过 1024×1024；请减小尺寸。")
        return {"ExtInfo": json.dumps({"AdditionalParameters": json.dumps({"size": f"{width}x{height}"})})}
    ratio = "1:1"
    resolution = size.upper()
    if match:
        width, height = map(int, match.groups())
        if min(width, height) <= 0:
            raise HTTPException(400, "图片宽高必须大于零。")
        ratio_value = width / height
        ratio = min(spec["aspect_ratios"], key=lambda r: abs(int(r.split(':')[0]) / int(r.split(':')[1]) - ratio_value))
        if abs(int(ratio.split(':')[0]) / int(ratio.split(':')[1]) / ratio_value - 1) > 0.035:
            raise HTTPException(400, f"{spec['label']} 不支持此宽高比例，可选：{', '.join(spec['aspect_ratios'])}")
        # The canvas presets encode the resolution tier in the long side.
        # Using area would incorrectly downgrade ultrawide 2K/4K images.
        edge = max(width, height)
        resolution = "4K" if edge >= 3000 else "2K" if edge >= 1800 else "1K"
    if spec["model_name"] == "Vidu" and resolution == "1K":
        resolution = "1080P"
    canonical = next((value for value in spec["resolutions"] if value.upper() == resolution), None)
    if not canonical:
        raise HTTPException(400, f"{spec['label']} 分辨率可选：{', '.join(spec['resolutions'])}")
    output.update(Resolution=canonical, AspectRatio=ratio)
    return {"OutputConfig": output}


def video_request(payload, sub_app_id, resolve_reference):
    values = payload if isinstance(payload, dict) else vars(payload)
    spec = model_spec(values.get("model"), "video")
    if not sub_app_id or int(sub_app_id) <= 0:
        raise HTTPException(400, "请先检测或填写点播应用 SubAppId。")
    output = {"StorageMode": "Temporary"}
    duration = values.get("duration")
    if spec["durations"] and duration not in spec["durations"]:
        raise HTTPException(400, f"{spec['label']} 时长可选：{', '.join(map(str, spec['durations']))} 秒；请调整后重试。")
    if duration:
        output["Duration"] = duration
    resolution = str(values.get("resolution") or "").strip()
    if resolution and resolution.lower() != "auto":
        canonical = next((r for r in spec["resolutions"] if r.lower() == resolution.lower()), None)
        if spec["resolutions"] and not canonical:
            raise HTTPException(400, f"{spec['label']} 分辨率可选：{', '.join(spec['resolutions'])}")
        output["Resolution"] = canonical or resolution
    ratio = str(values.get("aspect_ratio") or "").strip()
    if ratio and ratio not in {"adaptive", "keep_ratio", "auto"}:
        if spec["aspect_ratios"] and ratio not in spec["aspect_ratios"]:
            raise HTTPException(400, f"{spec['label']} 比例可选：{', '.join(spec['aspect_ratios'])}")
        output["AspectRatio"] = ratio
    if values.get("generate_audio") and not spec["audio_generation"]:
        raise HTTPException(400, f"{spec['label']} 的 VOD 文档未声明音频生成开关，请关闭该开关后重试。")
    if spec["audio_generation"]:
        output["AudioGeneration"] = "Enabled" if values.get("generate_audio") else "Disabled"
    output["LogoAdd"] = "Enabled" if values.get("watermark") else "Disabled"
    if values.get("enable_upsample"):
        output["EnhanceSwitch"] = "Enabled"
    if values.get("camerafixed") or values.get("return_last_frame"):
        raise HTTPException(400, "腾讯 VOD 普通视频接口未配置固定镜头/返回尾帧参数，请关闭对应开关，或在提示词描述镜头。")
    body = {"SubAppId": int(sub_app_id), "ModelName": spec["model_name"], "ModelVersion": spec["model_version"],
            "Prompt": values.get("prompt", ""), "EnhancePrompt": "Enabled" if values.get("enhance_prompt") else "Disabled",
            "OutputConfig": output}
    if values.get("seed") is not None:
        body["Seed"] = values["seed"]
    images = [ref if isinstance(ref, dict) else vars(ref) for ref in values.get("images") or []]
    images = [ref for ref in images if ref.get("url")]
    files = []
    for ref in images:
        role = ref.get("role") or ""
        usage = {"first_frame": "FirstFrame", "last_frame": "LastFrame", "reference_image": "Reference"}.get(role)
        if not usage:
            usage = "Reference" if values.get("multimodal") or len(images) > 1 or values.get("videos") or values.get("audios") else "FirstFrame"
        files.append({**video_file(ref["url"], "Image", resolve_reference), "Usage": usage})
    for key, category in (("videos", "Video"), ("audios", "Audio")):
        for url in values.get(key) or []:
            if url:
                files.append({**video_file(url, category, resolve_reference), "Usage": "Reference"})
    first = sum(f["Usage"] == "FirstFrame" for f in files)
    last = sum(f["Usage"] == "LastFrame" for f in files)
    if first > 1 or last > 1 or (last and not first):
        raise HTTPException(400, "首尾帧模式各只能有一张图片，尾帧必须同时提供首帧。")
    if (first or last) and any(f["Usage"] == "Reference" for f in files):
        raise HTTPException(400, "不能混用首尾帧和全能参考模式，请统一参考素材的用途。")
    if not str(body["Prompt"]).strip() and not files:
        raise HTTPException(400, "请输入视频提示词或连接参考素材。")
    if files:
        body["FileInfos"] = files
    return body


def video_file(url, category, resolve_reference):
    value = str(url).strip()
    if value.startswith(("/assets/", "/output/")):
        try:
            value = resolve_reference({"url": value})
        except OSError as exc:
            raise HTTPException(400, "腾讯云 VOD 本地参考素材无法读取，请重新上传。") from exc
    if value.startswith("data:") and ";base64," in value:
        mime, encoded = value.split(",", 1)
        if not mime.startswith(f"data:{category.lower()}/"):
            raise HTTPException(400, "参考素材类型与节点不一致，请重新连接正确素材。")
        try:
            base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise HTTPException(400, "参考素材 Base64 数据无效。") from exc
        return {"Type": "Base64", "Base64": encoded, "Category": category}
    if value.startswith(("http://", "https://")):
        return {"Type": "Url", "Url": value, "Category": category}
    raise HTTPException(400, "腾讯云参考素材需为可读取的本地文件或公网直链。大视频请使用你自己的公开存储链接。")


async def generate(config, body, client=None, timeout=900, interval=8, kind="image"):
    label = "视频" if kind == "video" else "图片"
    action = "CreateAigcVideoTask" if kind == "video" else "CreateAigcImageTask"
    task_key = "AigcVideoTask" if kind == "video" else "AigcImageTask"
    async def execute(active):
        created = await call(active, action, body, config)
        task_id = created.get("TaskId")
        if not task_id:
            raise HTTPException(502, f"腾讯云未返回{label} TaskId。")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = await call(active, "DescribeTaskDetail", {"TaskId": task_id, "SubAppId": body["SubAppId"]}, config)
            task = result.get(task_key) or {}
            if result.get("Status") == "ABORTED" or task.get("ErrCodeExt") or task.get("ErrCode"):
                raise HTTPException(400, f"腾讯云{label}生成失败：{task.get('ErrCodeExt') or task.get('ErrCode') or 'ABORTED'} · {task.get('Message') or ''}（任务 {task_id}）")
            if result.get("Status") == "FINISH":
                urls = [f["FileUrl"] for f in (task.get("Output") or {}).get("FileInfos", []) if f.get("FileUrl")]
                if not urls:
                    raise HTTPException(502, f"腾讯云任务完成但未返回{label}（任务 {task_id}）")
                record_success(config, body, kind)
                return {"type": "url", "value": urls[0]}, {"id": task_id, "task_id": task_id, "data": [{"url": url} for url in urls]}
            await asyncio.sleep(interval)
        error = HTTPException(504, f"腾讯云{label}生成等待超时（任务 {task_id}）。任务可能仍在运行，请勿连续重复提交。")
        error.upstream_task_id = task_id
        raise error
    if client is not None:
        return await execute(client)
    async with httpx.AsyncClient(timeout=60) as active:
        return await execute(active)
