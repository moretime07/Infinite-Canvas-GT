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

## Local motion-reference extraction (Windows/NVIDIA)

The canvas `动作提取` node is a local Windows workflow for an NVIDIA RTX GPU. Use Windows 10 or 11, a current NVIDIA driver, the repository's `.venv` environment, and `git`, `ffmpeg`, and `ffprobe` available on `PATH`. The installer checks those native tools before it installs Python packages. After the base environment exists, run the one-time installer from the repository root and restart the app:

```bat
安装动作提取环境.bat
```

The web API never installs Python packages. If the node reports that the local motion environment is unavailable, close the app, run `安装动作提取环境.bat`, confirm that both CUDA checks succeed, and restart the app.

On first use, the local worker downloads and SHA-256 verifies approximately 468 MB of pinned model weights. Allow additional free space for download staging. The cache stays under the repository-relative `data/motion_models/` directory; no user-specific absolute cache path is published. The task may enter `downloading`/`preparing`, show the current model name and progress when reported, and then continue automatically after verification. A cancelled or failed download is never accepted as a model.

A new node starts with:

- `深度白模` enabled;
- `骨骼姿态` disabled;
- `保留原始音频` disabled.

At least one visual processor must remain enabled. Turn on `骨骼姿态` to publish a second pose video, and turn on `保留原始音频` when both generated MP4 files should carry the source audio. Processor and audio switches are locked while a task is queued, preparing, or running so the active task cannot diverge from its submitted settings.

Inputs are limited to one local constant-frame-rate video of 30.0 seconds or less. Variable-frame-rate sources are rejected before queueing; convert them to constant frame rate first. Supported media must use even dimensions, no more than 4096 pixels on either side, no more than 3840 × 2160 total pixels in either portrait or landscape orientation, no more than 60 fps or 1,800 frames, and no more than 24 GiB of decoded RGB data. Motion extraction never silently resizes or crops an input. When source audio is shorter than the generated branch video, it is padded without shortening the video; longer audio is capped to the video duration.

Connect the named `DEPTH` and `POSE` output ports independently to choose the reference role for each downstream video node. `DEPTH` and `POSE` publish separate browser-safe MP4 URLs; enabling one does not replace the other. Existing legacy canvas connections without a saved `fromPort` retain their previous behavior.

Use `取消` while a task is queued or running. After cancellation or a branch failure, use `重试` to submit a fresh local task with the current switches. Cancellation cleans task-local decoded frames and incomplete outputs; already completed branch results remain distinguishable from disabled or failed branches. The local service admits at most eight unfinished motion tasks, runs one GPU worker, probes at most two inputs concurrently, and retains only a bounded recent public terminal history; task-private paths are discarded immediately after cleanup.

Motion extraction never uses an API-provider key and never falls back to a paid or cloud media service. Source video, decoded frames, model inference, and MP4 publication remain on the local machine.

## Notes

Runtime configuration, API keys, generated media, local history, user canvases, and bundled Python files are intentionally excluded from Git.
