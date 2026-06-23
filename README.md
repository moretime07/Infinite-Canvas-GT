# Infinite-Canvas-GT

Infinite Canvas GT is a local AI creative studio built with FastAPI and a multi-page web frontend. It provides infinite canvas workflows, smart node-based generation, image and video generation, AI chat, asset management, prompt libraries, ComfyUI workflow support, and multi-provider API configuration.

The app supports ModelScope, local ComfyUI, RunningHub, Volcengine, Jimeng CLI, and OpenAI-compatible API providers. It is designed to run locally and can also be accessed from the same LAN.

## 功能支持

- 支持几乎所有 OpenAI 协议的 API、异步协议、Gemini 协议和方舟协议。
- 支持 RunningHub 的工作流、AI 应用和收费模型调用。
- 支持火山引擎调用，人脸认证相关能力仍在修复 bug。
- 支持 ModelScope 免费 LLM 模型和图像模型调用。
- 支持即梦 CLI 调用，可直接调用即梦高级会员积分，支持文生图、图生图、文生视频、图生视频。
- 支持调用本地局域网内的 ComfyUI。
- 支持扩展图片、360 全景图预览截图、视频帧抽取、循环节点等功能。
- `tools` 文件夹中提供 Chrome 批量采集到素材库的插件，以及 Photoshop 直连画布调用所有功能的插件。

## Run

On Windows, start the local service with:

```bat
启动服务.bat
```

Then open:

```text
http://127.0.0.1:3000/
```

## Notes

Runtime configuration, API keys, generated media, local history, user canvases, and bundled Python files are intentionally excluded from Git.
