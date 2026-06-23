#!/bin/bash
# 修复权限并启动服务
# 双击运行即可

cd "$(dirname "$0")"

echo "============================================"
echo "   ComfyUI-API-Modelscope"
echo "============================================"
echo ""
echo "修复权限中..."

# 移除安全限制
xattr -r -d com.apple.quarantine *.command *.py 2>/dev/null

# 设置执行权限
chmod +x 启动服务.command 2>/dev/null
chmod +x 启动服务.py
chmod +x mac-*.sh 2>/dev/null

echo "权限已修复！"
echo ""
echo "正在启动服务..."
echo "============================================"
echo ""

# 直接运行 Python 启动器
python3 启动服务.py
