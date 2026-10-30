# GitHub 完整便携版下载与安装

适用于 Windows 10/11 64 位。源码 ZIP 和完整便携版不是同一个包。

1. 在 GitHub Release 的 Assets 下载 `InfiniteCanvas-GT-Full.zip.001` 和 `.002`，以及 `Merge-Bundle.cmd`、`Merge-Bundle.ps1`、`bundle.json`，放在同一个文件夹。不要重命名。
2. 预留至少 20 GB 空间，双击 `Merge-Bundle.cmd`。脚本会校验每个分卷和合并结果，生成普通的 `InfiniteCanvas-GT-Full.zip`，不删除原始分卷，不覆盖已有的不同 ZIP。
3. 完整解压生成的 ZIP 到可写目录，例如 `D:\InfiniteCanvas`。不要在压缩包里运行，也不要只复制 EXE。
4. 双击解压后的 `InfiniteCanvas.exe`，浏览器会打开画布，在 API 设置里填写自己的供应商地址和 Key。

两个分卷必须全部下载。单独一个分卷不能正常解压。合并脚本仅处理本地文件，不联网、不安装软件；BAT 仅为这次 PowerShell 合并进程设置执行策略，不更改系统全局策略。公司策略阻止时请联系 IT，不要关闭安全软件。

内置 Python、基础依赖、FFmpeg、Git、PyTorch CUDA 12.8、ONNX Runtime 和白模/骨骼模型；无需先安装 Python。GPU 功能仍需要兼容的 NVIDIA 显卡驱动。云服务账号/余额、ComfyUI 独立服务、即梦 WSL 和本人登录需自行准备。

发行包不含作者的 Key、私有画布、素材、历史或代理设置。使用后目录会产生你的个人数据，转发时请使用原始发行文件，不要转发已经使用过的目录。EXE 没有商业代码签名，遇到未知发布者提示，请核对 GitHub 来源、SHA256 或联系 IT。

已有旧版后台占用 3000 端口时请先停止旧版后台；关闭浏览器并不等于关闭后台。更多说明在完整包的 `免安装使用说明.md` 中。
