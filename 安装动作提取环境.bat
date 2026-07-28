@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "PYTHON=%SCRIPT_DIR%.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo 未找到 .venv 虚拟环境，请先在项目目录中创建它。
    exit /b 1
)

"%PYTHON%" -m pip install torch==2.11.0 torchvision==0.26.0 --index-url https://download.pytorch.org/whl/cu128
if errorlevel 1 exit /b 1

"%PYTHON%" -m pip install -r "%SCRIPT_DIR%requirements-motion.txt"
if errorlevel 1 exit /b 1

"%PYTHON%" -c "import sys, torch, onnxruntime as ort; torch_cuda = torch.cuda.is_available(); onnx_cuda = 'CUDAExecutionProvider' in ort.get_available_providers(); print('torch.cuda.is_available() = {}'.format(torch_cuda)); print('CUDAExecutionProvider = {}'.format(onnx_cuda)); sys.exit(0 if torch_cuda and onnx_cuda else 1)"
if errorlevel 1 (
    echo CUDA 或 ONNX Runtime CUDAExecutionProvider 不可用，请检查 NVIDIA 驱动和 CUDA 环境。
    exit /b 1
)

echo 动作提取环境安装完成。
