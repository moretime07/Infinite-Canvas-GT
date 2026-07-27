# OminiLink / LittleOrange 模型识别优化设计

## 目标

让 API 设置能够正确识别 OminiLink / LittleOrange（橙域）的模型，并在保存后把模型提供给无限画布节点选择。

本次重点解决：

- `vg-api.aig-ai.com` 被错误识别为方舟/Ark；
- 视频服务未公开标准 `/v1/models` 时，模型列表为空；
- `gemini-omni-flash-preview` 因名称分类规则不足而没有出现在视频节点；
- API 设置保存了模型后，无限画布仍因供应商模型数组为空而判定模型不可用。

## 已确认的接口事实

- 通用 OpenAI 兼容入口：`https://api.aig-ai.com/v1`。
- 视频入口：`https://vg-api.aig-ai.com/v1`。
- 视频提交：`POST /v1/{model_id}`。
- 视频查询：`POST` 或 `GET /v1/query/{model_id}/{id}`，由具体模型文档决定。
- Gemini Omni Flash 的模型 ID 为 `gemini-omni-flash-preview`。
- Gemini Omni Flash 同时支持非流式文本任务和视频输出。
- 视频模型清单由官方文档和模型广场提供，视频入口没有文档化的标准 `/v1/models` 保证。

参考：

- https://video-ai.apifox.cn/8325162m0
- https://video-ai.apifox.cn/8325057m0
- https://video-ai.apifox.cn/481198693e0
- https://video-ai.apifox.cn/428191615e0

## 根因

### 1. 错误的 Ark 探测条件

现有 `probe_volcengine_task_endpoint` 把所有小于 500 的 HTTP 状态都当成任务端点可达，其中包含普通 `404`。当 OminiLink 的 `/v1/models` 不存在时，后续 Ark 探测请求也可能得到 `404`，最终却被判断为 Ark。

### 2. 模型发现只依赖标准列表接口

API 设置默认请求标准 `/v1/models` 或 Ark `/api/v3/models`。OminiLink 视频接口以模型 ID 作为 URL 路径，没有文档化的视频模型列表 API，因此标准探测失败后模型数组保持为空。

### 3. Omni 模型属于多能力模型

通用名称分类会把未包含 `video`、`veo`、`seedance` 等关键字的模型归为聊天模型。`gemini-omni-flash-preview` 实际也支持视频输出，不能依赖单类别关键字推断。

### 4. 无限画布执行严格校验模型数组

无限画布只接受供应商 `image_models`、`chat_models` 或 `video_models` 中已存在的模型。供应商验证未导入模型时，即使用户知道正确模型 ID，节点也会显示供应商或模型不可用。

## 设计方案

### 域名识别

增加集中式 OminiLink / LittleOrange 主机识别函数，识别：

- `api.aig-ai.com`
- `vg-api.aig-ai.com`
- `api.ominilink.ai`
- `vg-api.ominilink.ai`

只识别这些明确的 API 主机，不使用宽泛的域名后缀匹配，也不把 `portal.*` 管理后台地址当作 API Base URL。

### 模型发现顺序

1. 对通用入口先请求标准 `/v1/models`。
2. 若返回有效列表，保留真实上游列表。
3. 只要识别为 OminiLink / LittleOrange，再合并内置官方视频目录。
4. 若标准列表返回 `404`、HTML 或未实现，但主机已明确识别，则目录加载成功，并返回内置目录、`connection_verified: false` 以及清晰的“官方目录兜底”来源提示。
5. 其他未知供应商保持现有失败行为，不把任意站点都套用 OminiLink 目录。

### 内置模型目录

目录至少包含当前官方清单中的：

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

目录与上游列表合并时去重并稳定排序。

### 多能力分类

`gemini-omni-flash-preview` 显式加入：

- `chat_models`
- `video_models`

不加入 `image_models`，除非后续官方文档明确支持图片输出。

其他模型继续使用现有名称分类规则；内置视频目录中的模型始终进入 `video_models`，不再依赖名称猜测。

### 协议纠正

- Ark 任务探测遇到 `404`、HTML、跳转或鉴权失败时不得判定为 Ark。
- OpenAI 聊天兼容探测遇到 `404` 时不得判定为 OpenAI 入口存在。
- 已识别的 OminiLink 视频入口不再自动切换为 Ark。
- 模型识别结果将现有可保存协议纠正为 `openai`，避免引入本次范围之外的新生成协议。

### API 设置反馈

验证成功时显示：

- 识别到 OminiLink / LittleOrange；
- 拉取到的实时模型数量；
- 合并后的官方目录数量；
- 当 `/v1/models` 不可用时，明确提示当前使用“官方目录兜底”，而不是显示 HTTP 400 或 Ark 路径错误。

模型选择器允许用户勾选并保存这些模型。保存后 `/api/config` 返回的供应商模型数组直接供无限画布使用。

## 错误处理

- API Key 为 `401/403`：保持鉴权失败，不使用目录结果伪装连接成功。
- 未知域名 `/v1/models` 返回 `404`：保持现有失败或手动填写提示。
- OminiLink 域名 `/v1/models` 返回 `404` 或 HTML：返回官方目录兜底、`connection_verified: false`，并清楚标明没有验证账号下的实际模型权限。
- 网络超时：若域名明确属于 OminiLink，可展示目录并返回 `connection_verified: false`；不应声称 API Key 可用。
- 管理后台 URL：继续提示用户填写 API Base URL。

## 测试设计

### 后端单元测试

- OminiLink API 域名识别正确，管理后台域名不被当作 API。
- Ark 探测的 `404` 不再返回成功。
- OminiLink `/v1/models` 不存在时返回官方视频目录。
- 上游真实模型与内置目录合并、去重、排序。
- `gemini-omni-flash-preview` 同时出现在聊天和视频列表。
- `401/403` 不触发成功兜底。
- 未知域名不获得 OminiLink 目录。

### 前端回归测试

- 验证响应的模型可以进入模型选择器。
- 保存供应商后保留 `chat_models` 和 `video_models`。
- 无限画布 LLM 节点能选择 `gemini-omni-flash-preview`。
- 无限画布视频节点能选择 `gemini-omni-flash-preview` 和官方视频目录模型。

## 范围外

本次不实现 OminiLink 专用视频提交与查询适配，不发送付费生成任务，也不自动抓取需要登录的模型广场页面。模型是否能真正生成视频仍取决于后续生成协议适配与账号权限。
