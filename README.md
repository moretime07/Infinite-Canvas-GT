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
- 视频下载进度、分阶段耗时日志，以及可自定义协议、地址和端口的视频下载代理（默认关闭）。

## Windows 安装与运行（推荐）

### 完整便携包（EXE＋配套文件夹）

从 [GitHub Releases](https://github.com/moretime07/Infinite-Canvas-GT/releases/latest) 下载完整便携版。约 3.94 GB 的 ZIP 分为 `.zip.001`、`.zip.002` 两部分（每个附件小于 2 GiB）；把两个分卷、`Merge-Bundle.cmd`、`Merge-Bundle.ps1`、`bundle.json` 下载到同一文件夹，双击 **Merge-Bundle.cmd** 校验并合成普通 ZIP，再完整解压。两个分卷都必须下载，不要改名，也不要单独解压其中一个。建议预留 20 GB 空间。

如果拿到的是 `InfiniteCanvas-GT-Full.zip` 完整发行 ZIP，完整解压后直接双击 **InfiniteCanvas.exe**，无需安装 Python 或基础依赖。内置 FFmpeg、Git、CUDA 12.8 运行库和深度白模/骨骼姿态的模型。不要只复制 EXE；使用说明见包内 `免安装使用说明.md`。

便携启动器仅监听本机，需要本人填写 API Key；本地 GPU 功能仍需要兼容的 NVIDIA 驱动。ComfyUI 独立服务、即梦 WSL 环境及个人账号不在包内。转发给同事请使用原始干净 ZIP，不要转发已经保存个人数据的目录。GitHub 的 **Code → Download ZIP** 是源码包，不是完整便携包，仍按下方步骤安装。

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
2. 在项目内部创建 `.venv`，联网安装 `requirements.txt` 中的基础依赖，不修改系统 Python 的依赖。默认先用 PyPI 官方源，遇到 SSL 断连、网络错误或超时，再尝试清华 TUNA 镜像。
3. 隔离额外 pip 下载源和 `trusted-host` 设置，保留代理及 CA 证书；不修改全局 `pip.ini`，不关闭 TLS 校验。旧版 pip 更新失败不会单独导致整个安装中断。
4. 检查依赖与后端导入是否正常。失败会显示不含认证地址的错误摘要；修复网络或 Python 后可再次运行，不需要删除画布或 API 配置。

如果没有 WinGet，先从 [Python 官网](https://www.python.org/downloads/windows/) 安装 64 位 Python 3.11，再运行脚本。基础 API 调用功能不需要 NVIDIA 显卡，不会自动安装 PyTorch、下载动作提取模型或发起收费生成。

命令行安装（已有 Python）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\install_windows.ps1 -NonInteractive
```

需要明确指定解释器时，加 `-PythonExecutable "C:\path\to\python.exe"`。需要允许 WinGet 安装 Python 时，加 `-InstallPython`。这只为当前脚本放行执行策略，不修改系统永久执行策略。

### 安装网络问题：SSL EOF、反复重试或公司代理

从 `2026.10.30.2` 起，基础安装及新版自动更新的依赖安装共用上述单源重试机制。不会因电脑遗留的 NVIDIA 等额外 pip 源反复重试；只有网络错误才切换备用源，版本冲突/编译失败不会靠换源掩盖。

多数电脑直接双击安装即可。受限网络可在**项目目录**打开 PowerShell，按需选择以下一种方式：

```powershell
# 明确只用清华 TUNA HTTPS 镜像
.\安装依赖.bat -PackageSource tuna

# 使用自己的 HTTP/混合代理端口；示例端口以代理软件实际设置为准
.\安装依赖.bat -ProxyUrl http://127.0.0.1:7890

# 公司 HTTPS 检查网络：使用 IT 提供的可信 CA 证书（不是关闭证书校验）
.\安装依赖.bat -CaBundle "C:\certs\company-ca.pem"

# 公司要求使用内网包源时，沿用现有 pip 主源（不使用额外源）
.\安装依赖.bat -PackageSource configured

# 或显式指定经公司认可的 HTTPS 源
.\安装依赖.bat -IndexUrl https://packages.example.com/simple
```

`-PackageSource pypi` 可只使用官方源；代理与 CA 参数可以组合。显式指定源或选择 `configured` 时，不会自动转向其他公共源。参数仅作用于此次安装的子进程，不写入项目配置或系统设置。为避免密码留在终端历史，不要在命令中填写带密码的代理/下载源地址；必要时让 IT 配置现有的受保护网络配置。

安装器保留已有 `PIP_PROXY`、`PIP_CERT`、`PIP_CLIENT_CERT`、HTTP(S) 代理和 CA 环境配置，也读取 pip 配置中的代理/证书；默认不沿用 pip 主源和额外源。原始 pip 输出可能含带凭据的地址，因此不会写入安装日志，只展示阶段、下载源名称和错误类别。单次网络重试有上限，仍失败时检查网络/代理、证书、磁盘及 Python 版本，不能保证被网络策略拦截的电脑无需配置即可安装。

**网页中的“视频下载代理”仅用于下载生成视频，不用于安装 Python 依赖。** 安装时需要代理，请使用上述安装参数或已有网络配置。

仍在使用旧版安装脚本的电脑，需要先下载本仓库最新 ZIP；新电脑解压后安装即可。旧电脑先备份个人数据、停止后台，再用下方“启用 GT 自动更新”工具更新程序；该工具下载最新更新器。不要只复制新的 `.bat` 而漏掉 `tools/install_dependencies.py` 等文件。

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
python tools/install_dependencies.py --requirements requirements.txt
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
| `data/download-proxy.json` | 后台电脑的视频下载代理设置，不随程序更新覆盖或随画布导出 |
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

**日常更新：** 在部署电脑用 `http://127.0.0.1:3000/` 打开网页，保存画布并等待生成任务结束，点击更新按钮。更新器固定到同一个 Git 提交下载并校验文件，完整更新 Python 模块、前端、脚本、内置工作流；基础依赖列表变化时按需安装，不自动安装 GPU 模型或强制升级已满足要求的包。新版依赖下载使用与安装器相同的安全备用源逻辑，保留现有代理/CA；需要指定公司包源时，先手动用上方参数安装新版依赖。更新前检查新版能否导入，更新后自动重启并刷新页面。

- API 密钥、`data/`、`assets/`、`output/`、历史文件以及 `.venv/` 不参与程序文件覆盖，也不会上传。程序备份只含被替换的代码，保存在 `data/update_backups/`。
- 程序文件替换失败时自动恢复，新增文件也会清理；网页更新设置中可以手动回滚。备份保留在本机，不会自动删除。Python 依赖的版本变化不包含在代码回滚中；依赖安装失败请运行 `安装依赖.bat` 后重试。
- 本地自行修改过的程序或内置工作流会备份后覆盖，请先自行保存修改。更新不会删除未列入本次发布的本地文件，不修改 `.git`。
- 局域网同事只访问同一服务器时，只需在服务器本机更新，其他人刷新网页。远程访问者不能触发服务器代码更新。
- 以后发布新版本时，请同步更新 `VERSION` 和 `static/update-notes.json` 再推送 `main`，旧电脑才会显示新的版本提醒。

## 视频下载代理

从 `2026.10.30.1` 起，左侧 **更多设置 → 视频下载代理** 可配置生成视频下载使用的代理。默认关闭，软件不内置 VPN，也不会安装 VPN、更改系统代理或自动获取订阅。

### 设置步骤

1. 在**运行后台的电脑**上启动自己的代理软件，查看它的代理协议和监听端口。
2. 打开 **更多设置 → 视频下载代理**，勾选“启用视频下载代理”。
3. 填写以下项目。示例端口仅供说明，必须以代理软件实际设置为准。

   | 设置项 | 怎么填写 |
   | --- | --- |
   | 代理协议 | 普通 HTTP／混合端口选 **HTTP**；SOCKS5 端口选 **SOCKS5**；仅当代理本身使用 TLS 时选 **HTTPS** |
   | 代理服务器 | 同机代理通常填 `127.0.0.1`，只填 IP 或主机名，不填完整 URL、订阅链接、路径或账号密码 |
   | 代理端口 | 填代理软件的监听端口，范围 `1–65535`，例如 `10808` 或 `7890` |

4. 点击“测试代理连接”；测试成功后点击“保存设置”。**测试不会自动保存**。
5. 设置对下一次开始的视频下载生效；正在下载的任务保持原连接，不会因为保存设置而重新提交生成。

HTTP 代理也可以下载 HTTPS 视频，不需要因为视频地址以 `https://` 开头就选择 HTTPS 代理。目前不支持需要账号密码的代理。

### 旧电脑升级

已启用 GT 自动更新的电脑，先保存画布并等待任务结束，再点击左下角绿色更新按钮。此次基础依赖有变化，更新器会安装 SOCKS5 所需依赖，完成后重启；其他局域网用户刷新网页即可。

手动更新或依赖安装失败时：停止后台，按上方更新说明保留个人数据并更新代码，再运行 `安装依赖.bat`，然后启动服务。刷新页面，必要时使用 `Ctrl+F5`。不需要重新安装 GPU 模型或重新填写 API Key。

### 作用范围与隐私

- 只影响后续开始的生成视频下载；不影响正在下载的任务、模型调用、上传素材、系统代理或本地文件导出。内网 IP 和 localhost 视频地址保持直连。关闭后恢复下载器原有网络策略。
- 这是后台电脑的共享网络配置。局域网客户端的 `127.0.0.1` 与后台不同，只有在后台电脑通过 `localhost`／`127.0.0.1` 打开的设置页可修改。
- 配置保存在被 Git 忽略的 `data/download-proxy.json`，不随画布导出。请只使用可信代理：代理可看到下载目标和流量信息。TLS 校验保持开启，代理下载失败不会静默切换直连。
- 连接测试只访问固定公开地址 `https://www.gstatic.com/generate_204`，不发送密钥、提示词或素材，不保存配置或消耗生成额度。测试成功不能保证所有视频 CDN 都更快。
- SOCKS5 需要 `httpx[socks]`（已包含在基础依赖中）；旧安装请运行 `安装依赖.bat` 后重启后台。此功能不会安装 VPN 或创建 VPN 隧道。

### 常见问题

- **没有看到入口或提示后台尚未加载功能**：确认已更新代码、重新启动后台并刷新网页。只关网页不会结束旧后台进程。
- **测试失败**：检查代理软件是否启动、协议是否选对、端口是否匹配。部署在另一台电脑时，`127.0.0.1` 指后台电脑，而不是当前浏览器所在电脑。
- **提示缺少 SOCKS5 支持**：运行项目的 `安装依赖.bat` 并重启后台，不要换用其他未安装依赖的 Python。
- **测试成功但视频仍慢**：测试仅证明测试地址可达。供应商 CDN、代理线路和本地带宽都会影响速度；可更换线路，或关闭自定义代理对比。代理无法缩短上游排队和视频生成时间。
- **怎么查看各阶段耗时**：进入画布右上角“日志”，新任务可查看已记录的素材准备/上传、提交、生成、下载/保存及总耗时；无法区分的阶段会合并显示。旧记录没有采集的细项不能补算。

## 手动更新、迁移与常见问题

- **更新**：停止本地服务，备份上表中的个人数据，Git 安装执行 `git pull --ff-only`；ZIP 安装覆盖程序文件但保留个人数据。然后重新运行 `安装依赖.bat`，再启动。不要直接删除整个旧目录。
- **迁移到另一台电脑**：下载干净代码并重新安装依赖；只有确实需要迁移个人资料时才私下复制上表中的数据。不要把这些文件提交到公开仓库或发给不相关的人。
- **`ModuleNotFoundError`**：运行 `安装依赖.bat`，使用项目启动脚本，不要换用未安装依赖的其他 Python。
- **端口 3000 被占用 / 仍然运行旧代码**：关闭旧服务，或在本机画布空白处右键使用“重启后端”功能；仅关浏览器不会结束后端进程。
- **pip 出现 `SSLError` / `UNEXPECTED_EOF_WHILE_READING` / 反复 `/pip/` 重试**：使用最新完整安装包，按上方“安装网络问题”选择备用源、安装代理或可信 CA；脚本会自动处理常见断连并隔离额外源。不要使用 `--trusted-host` 或关闭 TLS 校验绕过错误。
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
