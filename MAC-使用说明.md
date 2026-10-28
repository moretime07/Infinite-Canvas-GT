# macOS 安装说明

完整安装与隐私说明见 [README.md](README.md)。基础 API 功能不需要 NVIDIA 显卡；动作提取的 Windows/CUDA 安装脚本不适用于 macOS。

先安装 Python 3.11，解压项目并在项目目录打开终端：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python main.py
```

打开 `http://127.0.0.1:3000/`，在 API 设置里填写自己的 Key。保持终端窗口打开，按 `Ctrl+C` 停止。

也可以使用 `mac-启动服务.command`，首次运行会创建虚拟环境并安装基础依赖。如果提示权限不足，先执行：

```bash
chmod +x mac-启动服务.command
```

如系统阻止下载的脚本，请先检查代码来源，再按 macOS 的“隐私与安全性”提示决定是否允许打开。不要全局关闭系统安全检查。

更新代码后重新执行 `.venv/bin/python -m pip install -r requirements.txt`。若 `.venv` 来自其他电脑，请保留必要的备份后重新创建本机虚拟环境，不要直接使用复制来的环境。

本次发布验证使用 Windows/Python 3.11，macOS 启动脚本未在实机验证。
