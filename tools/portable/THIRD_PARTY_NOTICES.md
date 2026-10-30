# Third-party components in the portable distribution

This distribution retains upstream copyright and license files. Component versions and download integrity hashes are recorded in PORTABLE_COMPONENTS.json and PORTABLE_PYTHON_PACKAGES.json. No provider account credentials are distributed.

## CPython / python-build-standalone

- CPython 3.11.18, x86_64 Windows, Astral build 20261026.
- Distribution: https://github.com/astral-sh/python-build-standalone/releases/tag/20261026
- Build source: https://github.com/astral-sh/python-build-standalone/tree/20261026
- CPython source: https://github.com/python/cpython/tree/v3.11.18
- Python Software Foundation license and bundled-library notices are retained under python/.

## FFmpeg and its bundled libraries

- FFmpeg n9.0.2-37-gae19868753, BtbN GPL shared build, 20261029.
- Binary release: https://github.com/BtbN/FFmpeg-Builds/releases/tag/latest
- FFmpeg corresponding source: https://github.com/FFmpeg/FFmpeg/tree/ae19868753
- Build scripts, dependency source locations, versions and build options: https://github.com/BtbN/FFmpeg-Builds/tree/5031c6f
- FFmpeg's GPL v3 license is retained at bin/ffmpeg/LICENSE.txt. Headers and upstream documentation are preserved alongside the binaries. The `ffmpeg -buildconf` command shows the configuration. FFmpeg and its included libraries retain their respective upstream licenses; the application uses these tools as separate processes.
- Source code archives for FFmpeg and the build scripts are supplied under third-party-sources/ for this package. Refer to the build scripts for the corresponding source repositories of linked dependencies. Preserve these notices and access to corresponding source when redistributing.

## Git for Windows / MinGit

- Version 2.56.0.windows.4, x64 MinGit.
- Distribution and corresponding source: https://github.com/git-for-windows/git/releases/tag/v2.56.0.windows.4
- Source: https://github.com/git-for-windows/git/tree/v2.56.0.windows.4
- Git's GPL and bundled-library licenses are retained in bin/git/. A source archive for the Git revision is supplied under third-party-sources/.

## GPU and Python packages

- PyTorch 2.11.0 + CUDA 12.8 and torchvision 0.26.0: https://pytorch.org/ ; https://github.com/pytorch/pytorch/tree/v2.11.0 ; https://github.com/pytorch/vision/tree/v0.26.0
- ONNX Runtime CUDA 12-compatible build: https://github.com/microsoft/onnxruntime ; https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html
- NVIDIA runtime components bundled with the PyTorch distribution remain subject to their NVIDIA redistribution/license terms. GPU drivers are not included.
- Other Python distribution metadata and licenses are retained under python/Lib/site-packages/. Exact installed versions are recorded separately. These packages were installed into a newly downloaded runtime, not copied from the author's environment.
- App-local Microsoft Visual C++ redistributable runtime DLLs are unmodified, Microsoft-signed files. They remain subject to the Microsoft Visual C++ runtime license. They are not the NVIDIA driver or the Windows operating system.

## Motion models and pinned public sources

- Video Depth Anything Small: https://huggingface.co/depth-anything/Video-Depth-Anything-Small (Apache-2.0).
- Source: https://github.com/DepthAnything/Video-Depth-Anything/tree/4f5ae23172ba60fd7bc11ef671cca678842c7072
- DWPose: https://github.com/IDEA-Research/DWPose/tree/3dca5db79d9f9ffdd378753ddf6ec66535aace88
- ONNX weights: https://huggingface.co/yzd-v/DWPose
- Upstream LICENSE files are retained with the source checkouts. These are clean shallow public checkouts, not the author's project Git repository or private Git configuration. The DWPose checkout uses a sparse working tree to avoid Windows path-length problems while preserving its pinned source identity.

External services (OpenRouter, RunningHub, other model providers, ComfyUI services and Jimeng accounts) are not provided by these binary files and retain their own terms and access requirements.
