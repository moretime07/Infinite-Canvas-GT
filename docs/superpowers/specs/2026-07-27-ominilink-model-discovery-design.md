# OminiLink / LittleOrange 模型识别与 Omni Flash 视频适配设计

## 目标

让 API 设置能够正确识别 OminiLink / LittleOrange（橙域）的模型，并让
`gemini-omni-flash-preview` 不只是出现在无限画布的模型下拉框中，而是能够通过
OminiLink 的专用视频协议真正完成文本生视频、图片生视频和视频编辑。

本次解决以下问题：

- `vg-api.aig-ai.com` 被错误识别为方舟 / Ark。
- 视频服务未公开标准 `/v1/models` 时，模型列表为空。
- `gemini-omni-flash-preview` 没有被识别为视频模型。
- 单一 API 地址无法同时正确承载 OpenAI 兼容聊天接口与 OminiLink 视频接口。
- 无限画布现有通用视频提交路径不符合 OminiLink 的 `POST /v1/{model_id}` 协议。
- 模型虽然可选，但参考图片、参考视频、任务查询和视频结果解析无法工作。

## 已确认的接口事实

- OpenAI 兼容聊天入口：`https://api.aig-ai.com/v1`。
- 视频入口：`https://vg-api.aig-ai.com/v1`。
- 通用视频提交：`POST /v1/{model_id}`。
- 通用视频查询：`POST /v1/query/{model_id}/{id}`。
- Omni Flash 异步查询：`GET /v1/query/{model_id}/{id}`。
- Gemini Omni Flash 模型 ID：`gemini-omni-flash-preview`。
- Omni Flash 支持文本任务和视频输出；视频任务包括 `text_to_video`、
  `image_to_video`、`reference_to_video` 与 `edit`。
- Omni Flash 视频结果可能直接返回，也可能先返回任务 ID 后异步查询。
- 视频大于 4 MB 时应使用 URI 交付；参考视频只允许一个，官方建议不超过 3 秒；
  不支持音频参考。

参考文档：

- [视频 API 鉴权与地址](https://video-ai.apifox.cn/8325057m0)
- [官方视频模型列表](https://video-ai.apifox.cn/8325162m0)
- [通用视频创建](https://video-ai.apifox.cn/428191615e0)
- [通用任务查询](https://video-ai.apifox.cn/428355733e0)
- [Omni Flash 文本生视频](https://video-ai.apifox.cn/481198693e0)
- [Omni Flash 图片生视频](https://video-ai.apifox.cn/481201290e0)
- [Omni Flash 视频编辑](https://video-ai.apifox.cn/481201735e0)
- [Omni Flash 异步查询](https://video-ai.apifox.cn/481200398e0)
- [Omni Flash 限制与最佳实践](https://video-ai.apifox.cn/9111830m0)

## 根因

### 1. Ark 探测把 404 当成成功

现有 `probe_volcengine_task_endpoint` 把所有小于 500 的 HTTP 状态都当成任务端点
可达，其中包括普通 `404`。OminiLink 的 `/v1/models` 不存在时，后续 Ark 探测
也可能得到 `404`，最终却被错误保存为 `volcengine` 协议。

### 2. 模型发现只依赖标准列表接口

API 设置默认请求标准 `/v1/models` 或 Ark `/api/v3/models`。OminiLink 视频接口
以模型 ID 作为 URL 路径，没有文档化的视频模型列表 API，因此标准探测失败后
模型数组保持为空。

### 3. Omni Flash 是多能力模型

通用名称分类会把不包含 `video`、`veo`、`seedance` 等关键词的模型归入聊天模型。
`gemini-omni-flash-preview` 实际支持视频输出，不能依赖单类别关键词推断。

### 4. 聊天与视频使用不同主机

`https://api.aig-ai.com/v1` 用于 OpenAI 兼容聊天，
`https://vg-api.aig-ai.com/v1` 用于视频。现有提供商只有一个 `base_url`，用户无论
填哪个地址，另一种能力都会走错主机。

### 5. 通用视频适配器路径不匹配

现有画布通用视频适配器尝试 `/v1/videos/generations`、`/v2/videos/generations`
等路径；Omni Flash 要求 `POST /v1/gemini-omni-flash-preview`，请求体、轮询方法
和嵌套结果结构也不同。

## 设计方案

### 提供商地址模型

在提供商配置中增加可选的 `video_base_url`：

- `base_url`：聊天、模型列表及 OpenAI 兼容能力的基础地址。
- `video_base_url`：视频任务的基础地址。

OminiLink 已知主机使用以下安全、精确的映射：

| 输入地址 | 保存后的 `base_url` | 默认 `video_base_url` |
| --- | --- | --- |
| `https://api.aig-ai.com/v1` | 原值 | `https://vg-api.aig-ai.com/v1` |
| `https://vg-api.aig-ai.com/v1` | `https://api.aig-ai.com/v1` | 原值 |
| `https://api.ominilink.ai/v1` | 原值 | `https://vg-api.ominilink.ai/v1` |
| `https://vg-api.ominilink.ai/v1` | `https://api.ominilink.ai/v1` | 原值 |

仅识别上述精确 API 主机，不把 `portal.*` 管理后台地址当作 API。用户显式填写
`video_base_url` 时以用户值为准。旧配置在读取和保存时进行内存归一化，不直接
覆盖 API Key 或无关配置。

API 设置页增加“视频 API 地址（可选）”字段。识别为 OminiLink 时自动填充并显示
“聊天与视频使用不同入口”的说明。

### 模型发现顺序

1. 对聊天入口先请求标准 `/v1/models`。
2. 返回有效列表时保留真实上游模型。
3. 识别为 OminiLink / LittleOrange 后，合并内置的官方视频目录。
4. `/v1/models` 返回 `404`、HTML、未实现或网络超时时，仍可显示官方目录，但返回
   `connection_verified: false` 和“官方目录兜底”的明确提示。
5. `401/403` 必须保留为鉴权失败，不能用目录兜底伪装成验证成功。
6. 未知提供商保持现有失败行为，不能套用 OminiLink 目录。

### 内置视频模型目录

目录包含当前官方文档中的：

- `gemini-omni-flash-preview`
- `sora-2`
- `veo-3.1-generate-001`
- `veo-3.1-fast-generate-001`
- `veo-3.1-lite-generate-001`
- `seedance-2.0`
- `seedance-2.0-fast`
- `viduq3`
- `viduq3-pro`
- `viduq3-pro-fast`
- `viduq3-turbo`
- `viduq3-mix`
- `kling-v3-omni`
- `kling-v3`
- `kling-video-o1`
- `kling-v2-6`
- `kling-v2-5-turbo`
- `kling-v2-1-master`
- `kling-v2-master`

目录与上游列表合并时去重并稳定排序。目录表示“服务文档中存在”，不代表当前
API Key 一定拥有权限。

`gemini-omni-flash-preview` 显式加入：

- `chat_models`
- `video_models`

不加入 `image_models`，因为当前文档没有确认其图片输出能力。其他目录条目显式加入
`video_models`，不再依赖模型名称猜测。

### 协议纠正

- Ark 探测遇到 `404`、HTML、跳转或鉴权失败时不得判定为 Ark。
- OpenAI 兼容探测遇到 `404` 时不得判定端点存在。
- 已识别的 OminiLink 主机不再自动切换为 Ark。
- 提供商的聊天协议保存为 `openai`；视频执行由主机识别和
  `video_base_url` 路由到 OminiLink 专用适配器。

这样不需要把聊天提供商改造成一个不兼容的新协议，同时视频执行仍有清晰的专用
分支。

## Omni Flash 视频适配器

### 路由条件

当以下条件同时成立时使用 Omni Flash 专用适配器：

- 提供商为已识别的 OminiLink 主机。
- 节点选择 `gemini-omni-flash-preview`。
- 节点执行视频生成。

其他提供商同名模型不自动套用该协议，防止 OpenRouter 等供应商被错误路由。

### 请求构建

提交地址：

```text
POST {video_base_url}/gemini-omni-flash-preview
Authorization: Bearer <API_KEY>
Content-Type: application/json
```

公共请求结构：

```json
{
  "model": "gemini-omni-flash-preview",
  "background": true,
  "input": [
    {
      "type": "user_input",
      "content": []
    }
  ],
  "generation_config": {
    "thinking_level": "low",
    "thinking_summaries": "auto",
    "video_config": {
      "task": "text_to_video"
    }
  },
  "response_format": {
    "type": "video",
    "delivery": "uri",
    "aspect_ratio": "16:9",
    "duration": "6s"
  }
}
```

节点参数映射：

- 提示词追加为 `{ "type": "text", "text": "..." }`。
- 无素材：任务为 `text_to_video`。
- 一张参考图：转为带 MIME 类型的 base64 `image` 内容，任务为
  `image_to_video`。
- 多张参考图：保持画布连接顺序并逐张转为 `image` 内容，任务为
  `reference_to_video`；应用不截断图片数量，由上游模型返回实际能力限制。
- 一个参考视频：转为带 MIME 类型的 base64 `video` 内容，任务为 `edit`。
- 比例映射到 `response_format.aspect_ratio`。
- 时长标准化为带 `s` 后缀的字符串，并限制在文档允许的 `3~10s`。
- 交付方式固定为 `uri`。

本阶段不把图片与视频同时提交；混合输入、超过一个视频、音频输入、超过 3 秒的参考
视频都在发起付费请求前给出中文校验错误。提示词和参考图数量不设应用层上限，也不做
静默截断；无法可靠读取本地视频时长时允许提交，并由上游返回实际限制。

### 查询与结果解析

1. 提交响应若已包含 `steps[].content[].uri` 中的视频 URI，直接进入下载与输出。
2. 若返回任务 ID 且状态未完成，轮询：
   `GET {video_base_url}/query/gemini-omni-flash-preview/{id}`。
3. 轮询复用现有画布任务生命周期：可取消、有最大等待时间、有稳定的状态文案，
   页面刷新或用户停止后不得无限保留转圈占位。
4. 完成响应从嵌套 `steps[].content[]` 中提取 `type == "video"` 的 `uri`。
5. 视频 URI 交给现有远程视频保存流程，最终输出本地可播放的视频卡片。
6. 上游明确失败、敏感内容、鉴权失败、超时或完成但无视频 URI 时，终止轮询并显示
   可读错误，不留下永久加载卡片。

### 其他 OminiLink 视频模型

官方目录中的其他视频模型继续显示并可保存。它们使用 OminiLink 通用
`POST /v1/{model_id}` 请求路径和 `{prompt, size, seconds}` 基础参数；查询优先使用
文档化的 `POST /v1/query/{model_id}/{id}`。本阶段只保证这些模型的基础文本生视频，
不推断每个模型族未统一文档化的参考素材字段。

若节点给这些模型传入参考素材，前端和后端应显示“该模型的 OminiLink 参考素材协议
尚未适配”，而不是静默丢弃素材。

## API 设置反馈

验证或识别完成时显示：

- 已识别 OminiLink / LittleOrange。
- 当前聊天 API 地址与视频 API 地址。
- 拉取到的实时模型数量。
- 合并后的官方目录数量。
- 是否验证了当前 API Key 的真实模型权限。
- `/v1/models` 不可用时明确显示“官方目录兜底”，不再显示 Ark 路径错误。

保存后 `/api/config` 返回非敏感的 `video_base_url` 和模型数组，API Key 仍只使用
已有的安全存储方式，不写入前端配置、日志、测试快照或 Git。

## 错误处理

- `401/403`：明确显示鉴权或权限失败，不能伪装成连接成功。
- `404/405`：显示实际请求方法和非敏感路径，提示接口协议不匹配。
- HTML 响应：显示“上游返回网页而非 JSON”，不再抛出模糊的 JSON 解析异常。
- `429`：显示限流并停止快速轮询。
- 敏感内容：透传安全错误摘要，但不回显完整素材或 API Key。
- 网络超时：进入有上限的重试；到达上限后清理加载占位。
- 完成但无 URI：显示“任务完成但没有返回视频地址”，保留任务 ID 便于排查。
- 参考素材不符合限制：在本地校验阶段拒绝，不发送付费请求。

## 测试设计

所有自动化测试使用 mock HTTP 客户端，不发起真实付费生成请求。

### 后端单元测试

- 精确识别 OminiLink API 主机，管理后台和相似恶意域名不被识别。
- 已有 `vg-api` 配置被归一化为聊天与视频双地址。
- Ark 探测 `404` 不再返回成功。
- OminiLink `/v1/models` 不存在时返回官方视频目录。
- 上游真实模型与内置目录合并、去重、排序。
- `gemini-omni-flash-preview` 同时进入聊天和视频列表。
- `401/403` 不触发成功兜底。
- Omni 文本生视频请求体和 URL 正确。
- Omni 图片生视频包含正确 MIME 与 base64 内容。
- Omni 视频编辑只接收一个视频并使用 `edit`。
- 混合输入、过多素材与已知超长参考视频在提交前失败。
- Omni 立即完成响应能解析视频 URI。
- Omni 异步响应使用 GET 查询并最终解析 URI。
- 超时、取消、安全错误和无视频 URI 会清理任务状态。
- 其他 OminiLink 视频模型使用通用提交与 POST 查询路径。

### 前端与画布回归测试

- API 设置能显示并保存 `video_base_url`。
- 官方目录兜底提示不声称 API Key 已验证。
- 无限画布 LLM 节点能选择 `gemini-omni-flash-preview`。
- 无限画布视频节点能选择并执行 `gemini-omni-flash-preview`。
- 图片、视频输入会选择正确 Omni 任务类型。
- 不受支持的素材组合显示中文校验错误。
- 任务取消、超时、失败后不保留永久转圈卡片。
- API Key 不出现在 DOM、日志、错误文本或测试快照中。

## 范围外

- 不自动抓取需要登录的模型广场页面。
- 不自动验证或购买当前账号没有权限的模型。
- 不保证每个 OminiLink 视频模型族的高级参考素材字段；本次完整适配
  `gemini-omni-flash-preview`，其他目录模型只保证文档统一的基础文本生视频。
- 不实现 Omni Flash 未支持的视频延长、多视频推理、音频参考、插帧或语音编辑。
- 不在测试中提交任何真实付费生成任务。
