#!/bin/bash
# Hermes Dashboard — 一键启动

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

export HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"

echo "🦞 Hermes Dashboard 启动中…"
echo "  目录: $SCRIPT_DIR"
echo "  HERMES_HOME: $HERMES_HOME"

# 虚拟环境
VENV="$SCRIPT_DIR/.venv"
if [ ! -d "$VENV" ]; then
    echo "→ 创建虚拟环境…"
    python3 -m venv "$VENV"
fi

echo "→ 安装依赖…"
"$VENV/bin/pip" install -q flask flask-cors psutil pyyaml

# 启动后端 (3801)
echo "→ 启动后端 (port 3801)…"
"$VENV/bin/python" backend/app.py &
BACKEND_PID=$!

sleep 1

# 启动前端代理 (3800)
echo "→ 启动前端 (port 3800)…"
"$VENV/bin/python" server.py &
FRONTEND_PID=$!

echo ""
echo "✅ 启动完成!"
echo "   前端: http://localhost:3800"
echo "   后端: http://localhost:3801"
echo ""
echo "   PID: backend=$BACKEND_PID frontend=$FRONTEND_PID"
echo ""
echo "   按 Ctrl+C 停止所有服务"

wait
