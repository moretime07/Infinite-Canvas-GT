#!/bin/bash
# 修复 macOS Gatekeeper 安全限制
# 运行此脚本后，双击 .command 文件就不会再提示权限问题了

cd "$(dirname "$0")"

echo "============================================"
echo "   修复 macOS 安全限制"
echo "============================================"
echo ""

# 移除下载文件的 quarantine 属性（这是阻止运行的主要原因）
echo "正在移除安全限制..."

xattr -r -d com.apple.quarantine 启动服务.command 2>/dev/null
xattr -r -d com.apple.quarantine 启动服务.py 2>/dev/null
xattr -r -d com.apple.quarantine mac-安装依赖.sh 2>/dev/null
xattr -r -d com.apple.quarantine mac-启动服务.sh 2>/dev/null

echo "✓ 已移除安全限制"
echo ""

# 设置执行权限
chmod +x 启动服务.command
chmod +x 启动服务.py
chmod +x mac-安装依赖.sh 2>/dev/null
chmod +x mac-启动服务.sh 2>/dev/null

echo "✓ 已设置执行权限"
echo ""

echo "============================================"
echo "   修复完成！"
echo "============================================"
echo ""
echo "现在可以正常双击 '启动服务.command' 了。"
echo ""
echo "如果仍然提示权限问题，请到："
echo "系统设置 → 隐私与安全性 → 点击'仍要打开'"
echo ""
read -p "按 Enter 键退出..."
