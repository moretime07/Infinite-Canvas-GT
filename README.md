# Infinite-Canvas-GT

本地运行的 AI 创作工作台：无限画布、LLM 多模态节点、图片/视频生成、批量循环、素材库与 ComfyUI / RunningHub 工作流。后端使用 Python/FastAPI，前端已包含在仓库中，不需要安装 Node.js 或执行前端打包。

**本仓库只提供程序，不包含作者的 API 密钥、个人画布、素材或生成记录。每位使用者需要填写自己的 API Key；第三方模型调用可能收费。**

## 功能支持

- 支持 OpenAI 兼容、Gemini、方舟等协议，以及已适配的 OpenRouter、Aiide、OminiLink、腾讯等接口。是否能调用特定模型，取决于供应商接口与账号权限，不保证任意中转站完全兼容。
- 支持 RunningHub 的工作流、AI 应用和收费模型调用。
- 支持火山引擎调用，人脸认证相关能力仍在修复 bug。
- 支持 ModelScope 免费 LLM 模型和图像模型调用。
- 支持即梦 CLI 调用，可直接调用即梦高级会员积分，支持文生图、图生图、文生视频、图生视频。
- 支持调用本地局域网内的 ComfyUI。
- 支持扩展图片、360 全景图预览截图、视频帧抽取、循环节点等功能。
- API 默认供应商、自由连线与循环检测、视频参考和本地动作提取（深度白模/骨骼姿态）。

## Windows 安装与运行（推荐）

### 1. 下载程序

在本仓库点击 **Code → Download ZIP**，完整解压到可写目录，例如 `D:\Infinite-Canvas-GT`。不要在压缩包里直接运行，也不要解压到 `Program Files`。更新旧版本前先备份个人数据，见下方说明。

也可以使用 Git：

```powershell
git clone https://github.com/moretime07/Infinite-Canvas-GT.git
cd Infinite-Canvas-GT
```

### 2. 双击安装

双击 **`安装依赖.bat`**。脚本将：

1. 查找 64 位 Python 3.10–3.13，推荐 Python 3.11；没有 Python 时，如电脑有 WinGet，会询问是否安装 Python 3.11。
2. 在项目内部创建 `.venv`，联网安装 `requirements.txt` 中的基础依赖，不修改系统 Python 的依赖。
3. 检查依赖与后端导入是否正常。失败会显示错误，修复网络或 Python 后可再次运行。

如果没有 WinGet，先从 [Python 官网](https://www.python.org/downloads/windows/) 安装 64 位 Python 3.11，再运行脚本。基础 API 调用功能不需要 NVIDIA 显卡，不会自动安装 PyTorch、下载动作提取模型或发起收费生成。

命令行安装（已有 Python）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\install_windows.ps1 -NonInteractive
```

需要明确指定解释器时，加 `-PythonExecutable "C:\path\to\python.exe"`。需要允许 WinGet 安装 Python 时，加 `-InstallPython`。这只为当前脚本放行执行策略，不修改系统永久执行策略。

### 3. 启动

双击：

```bat
启动服务.bat
```

`启动服务.bat` 发现没有项目 Python 环境时也会引导安装。保持服务窗口打开；浏览器没有自动打开时，访问：

```text
http://127.0.0.1:3000/
```

### 4. 配置自己的模型

进入 **API 设置 → 新增平台**，填写供应商的 API Base URL、协议和自己的 Key，保存后验证地址并拉取模型。可以设为默认供应商，再到画布选择“跟随默认 API”。不同供应商的视频接口可能需要单独填写“视频 API 地址”。

不要把网页管理后台地址当作 API 地址。模型列表为空不一定是程序故障，也可能是平台没有模型列表接口、Key 权限不足或地址/协议不匹配。先用一个模型、小批量验证，再运行批量工作流。

## macOS / Linux 手动安装

基础 API 工作台可按以下方式安装；本次自动安装与启动验证使用 Windows + Python 3.11，未进行 macOS/Linux 实机验证。本地 NVIDIA 动作提取仍以 Windows 安装说明为准。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python main.py
```

浏览器打开 `http://127.0.0.1:3000/`。macOS 的 `mac-启动服务.command` 会使用 `.venv`，首次运行时安装基础依赖。

## 可选功能

- **动作提取 / RTX 5080 主机**：先完成基础安装，再安装 NVIDIA 驱动、Git、FFmpeg（含 ffprobe，加入 PATH），运行 `安装动作提取环境.bat`。这是额外的 GPU 环境安装，不是基础安装的一部分，详细限制见下文。
- **即梦 CLI**：按需运行 `安装即梦CLI.bat`、`登录即梦CLI.bat`，在第三方官方登录页面登录自己的账号。
- **本地 ComfyUI**：需要另行安装 ComfyUI，以及所选工作流需要的节点和模型；本仓库不包含这些大模型。

## 局域网访问与数据存放

启动程序监听 `0.0.0.0:3000`。同一局域网可访问 `http://部署电脑的局域网IP:3000/`；如 Windows 防火墙阻止连接，仅按需放行可信专用网络中的 TCP 3000。不要做公网端口转发。

**当前版本没有完整的多用户账号、权限隔离机制。不要把它当作“每台电脑独立保存私密 Key 和私有画布”的安全多租户服务。** 后端保存的 Key、画布和素材位于部署电脑；同一实例上的使用者应视为共享该实例的数据。个人资料需要隔离时，每人在自己的电脑安装独立副本。

常见本地数据位置（均排除在 Git 之外）：

| 目录/文件 | 内容 |
| --- | --- |
| `API/.env` | 用户填写的 API 密钥，本地明文配置，请保护文件访问权限 |
| `data/` | API 平台配置、画布、对话、提示词库、动作模型缓存等 |
| `assets/`、`output/` | 上传素材、素材库、生成图片/视频 |
| `history.json`、`global_config.json` | 本地历史和全局设置 |
| `.venv/` | 本机 Python 环境，不要复制到其他电脑使用 |

应用调用第三方 API 时，提示词和选定的参考素材会按该接口要求发送给供应商；“不上传 GitHub”不等于“模型调用不联网”。

## 一键更新（GT 版本）

从 `2026.10.29.1` 起，左下角绿色更新按钮只连接 **moretime07/Infinite-Canvas-GT**，不会切换到原作者仓库或 ModelScope 的其他版本。

**旧电脑首次切换：**

1. 保存画布，等待任务结束，在旧服务窗口按 `Ctrl+C` 停止后台（只关闭浏览器不够）。建议先备份上表中的个人数据。
2. 从本仓库下载 [`启用GT自动更新.bat`](https://raw.githubusercontent.com/moretime07/Infinite-Canvas-GT/main/%E5%90%AF%E7%94%A8GT%E8%87%AA%E5%8A%A8%E6%9B%B4%E6%96%B0.bat)，放进旧项目目录，与 `main.py` 同级，再双击运行。不需要下载整个仓库，也不需要 Git 或 GitHub 登录。
3. 工具下载并校验 GT 更新器，更新成功后自动启动服务、打开网页。以后直接点击网页左下角更新按钮，再确认开始更新。

旧版更新按钮的地址写在旧程序里，不能远程自动变更，所以上述切换只需做一次。首次工具要求旧项目已经有 `.venv/Scripts/python.exe` 或 `python/python.exe`；否则先按安装步骤安装依赖。

**日常更新：** 在部署电脑用 `http://127.0.0.1:3000/` 打开网页，保存画布并等待生成任务结束，点击更新按钮。更新器固定到同一个 Git 提交下载并校验文件，完整更新 Python 模块、前端、脚本、内置工作流；基础依赖列表变化时按需安装，不自动安装 GPU 模型或强制升级已满足要求的包。更新前检查新版能否导入，更新后自动重启并刷新页面。

- API 密钥、`data/`、`assets/`、`output/`、历史文件以及 `.venv/` 不参与程序文件覆盖，也不会上传。程序备份只含被替换的代码，保存在 `data/update_backups/`。
- 程序文件替换失败时自动恢复，新增文件也会清理；网页更新设置中可以手动回滚。备份保留在本机，不会自动删除。Python 依赖的版本变化不包含在代码回滚中；依赖安装失败请运行 `安装依赖.bat` 后重试。
- 本地自行修改过的程序或内置工作流会备份后覆盖，请先自行保存修改。更新不会删除未列入本次发布的本地文件，不修改 `.git`。
- 局域网同事只访问同一服务器时，只需在服务器本机更新，其他人刷新网页。远程访问者不能触发服务器代码更新。
- 以后发布新版本时，请同步更新 `VERSION` 和 `static/update-notes.json` 再推送 `main`，旧电脑才会显示新的版本提醒。

## 手动更新、迁移与常见问题

- **更新**：停止本地服务，备份上表中的个人数据，Git 安装执行 `git pull --ff-only`；ZIP 安装覆盖程序文件但保留个人数据。然后重新运行 `安装依赖.bat`，再启动。不要直接删除整个旧目录。
- **迁移到另一台电脑**：下载干净代码并重新安装依赖；只有确实需要迁移个人资料时才私下复制上表中的数据。不要把这些文件提交到公开仓库或发给不相关的人。
- **`ModuleNotFoundError`**：运行 `安装依赖.bat`，使用项目启动脚本，不要换用未安装依赖的其他 Python。
- **端口 3000 被占用 / 仍然运行旧代码**：关闭旧服务，或在本机画布空白处右键使用“重启后端”功能；仅关浏览器不会结束后端进程。
- **pip 下载失败**：检查网络、代理及证书，再重跑安装脚本。不要为了安装而关闭 TLS 证书校验。
- **动作提取不可用**：基础安装不包含 GPU 依赖。运行独立的动作提取安装脚本，检查 CUDA 和 FFmpeg，不影响普通云端 API 功能。

## 开发者提交前检查

`.gitignore` 排除了密钥、运行数据、个人素材、日志、虚拟环境、本机快捷方式和本地排查报告。不要使用 `git add -f` 强行添加这些内容。

暂存准备发布的文件后，执行：

```powershell
.\.venv\Scripts\python.exe tools\check_publish_safety.py --upstream origin/main
git diff --cached --stat
```

检查器扫描暂存内容和未推送提交，检查敏感文件路径、常见密钥格式，并与本机 `.env` 中配置的密钥比对；只报告文件名，不输出密钥。自动扫描不能识别全部隐私，仍需要人工确认截图、工作流模板和文档是否可公开。

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
