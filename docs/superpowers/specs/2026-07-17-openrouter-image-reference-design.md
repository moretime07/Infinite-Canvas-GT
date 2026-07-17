# OpenRouter 参考图传递修复设计

## 背景与根因

画布前端已经把提示词和参考图正确汇总到 `reference_images`。当前 OpenRouter 配置使用 `https://openrouter.ai/api/v1`，但图片生成仍进入通用 OpenAI Images 分支：先请求 `/images/edits`，失败后降级到 `/images/generations`，并用非 OpenRouter 规范的顶层 `image` 数组传递参考图。

OpenRouter 当前专用图片 API 是 `POST /api/v1/images`，参考图字段是 `input_references`（参见 [OpenRouter Image Generation](https://openrouter.ai/docs/guides/overview/multimodal/image-generation)）。旧降级请求虽然可能返回生成图片，却可能忽略参考图字段，导致模型只收到依赖“图1/图2”语义的提示词而没有对应图像。

## 目标

- OpenRouter 图片生成始终调用其专用 `/api/v1/images` 端点。
- 画布连接的参考图按现有顺序传入 `input_references`。
- 文生图继续支持，不要求必须存在参考图。
- OpenRouter 以外的 ModelScope、APIMart、Gemini、火山引擎、RunningHub 和通用 OpenAI 兼容供应商行为保持不变。
- OpenRouter 请求失败时返回真实错误，不降级到可能丢失参考图且可能重复计费的旧端点。

## 方案

在 `generate_ai_image` 的供应商分派阶段增加 OpenRouter 专用分支，并将 OpenRouter 请求构造与发送封装为独立函数。

请求端点：

```text
{openrouter_api_root(provider.base_url)}/images
```

请求体：

```json
{
  "model": "google/gemini-3-pro-image",
  "prompt": "用户提示词",
  "n": 1,
  "size": "1280x720",
  "quality": "high",
  "input_references": [
    {
      "type": "image_url",
      "image_url": {
        "url": "data:image/png;base64,..."
      }
    }
  ]
}
```

`input_references` 仅在存在有效参考图时发送。每张本地图片复用现有 `reference_to_data_url`，继续遵守 `ONLINE_IMAGE_REFERENCE_MAX` 数量上限和 1536 像素预处理限制。参考图数组顺序与画布 API 节点的 `IMAGES` 顺序一致。

响应继续复用现有 `extract_image`。该解析器已经支持 OpenRouter 图片 API 的 `data[].b64_json` 响应，无需改动输出保存流程。

## 数据流

1. `runGenerator` 汇总提示词和已连接图片。
2. `/api/canvas-image-tasks` 保存任务，并把 `reference_images` 传入 `generate_ai_image`。
3. `generate_ai_image` 识别 OpenRouter host，调用 OpenRouter 专用生成函数。
4. 专用函数将参考图转换成 data URL，构造 `input_references`，请求 `/api/v1/images`。
5. `extract_image` 提取 base64 或 URL，现有任务流程将图片保存到 `assets/output` 并回填 OUTPUT 节点。

## 错误处理

- 缺少 Base URL 时沿用现有配置错误。
- OpenRouter 返回非 2xx 时调用 `raise_for_status`，保留上游错误处理链路。
- 不再针对 OpenRouter 尝试 `/images/edits` 或 `/images/generations`。
- 无有效参考图时不发送空的 `input_references`，仍执行正常文生图。

## 测试设计

新增 Python 回归测试，使用受控 HTTP 客户端替身阻止真实网络请求和计费：

1. OpenRouter + 两张参考图时，请求 URL 必须是 `https://openrouter.ai/api/v1/images`。
2. 请求体必须包含顺序一致的两个 `input_references`，且不包含旧字段 `image`。
3. 无参考图时不发送 `input_references`。
4. OpenRouter 的 `data[].b64_json` 响应能通过现有返回链路成功提取。
5. 运行现有 Python 和 JavaScript 测试，确认其他供应商及设置行为没有回归。

## 验收标准

- 新回归测试在修改生产代码前因缺少 OpenRouter 专用行为而失败。
- 实现后新回归测试与现有测试全部通过。
- 重新启动服务后，`http://127.0.0.1:3000/` 返回 HTTP 200。
- 不在自动验证中发起真实付费图片生成；用户可在界面中自行进行最终效果验证。

## 不在本次范围内

- 不新增通用 `openrouter-images` 配置模式。
- 不修改前端画布交互或提示词编辑体验。
- 不探测每个 OpenRouter 图片模型的动态能力参数。
- 不修改非 OpenRouter 供应商的请求协议。
