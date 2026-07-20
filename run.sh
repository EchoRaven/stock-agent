#!/usr/bin/env bash
# 一键启动 stock-agent(本地开发):后端 FastAPI :8000 + 前端 Next.js :3000。
# 两者都只监听 127.0.0.1(单用户自用,绝不暴露公网)。Ctrl-C 同时停掉两个服务。
#
#   用法:  ./run.sh
#   打开:  http://localhost:3000
#
# 说明:后端首个受 token 保护的请求会惰性生成 backend/.api_token(0600,已 gitignore);
# 前端的服务端代理读它注入到后端请求,token 绝不进浏览器。
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UV="${UV:-$HOME/.local/bin/uv}"
BACK_PID=""
FRONT_PID=""

cleanup() {
  echo
  echo "停止服务…"
  [ -n "$FRONT_PID" ] && kill "$FRONT_PID" 2>/dev/null || true
  [ -n "$BACK_PID" ] && kill "$BACK_PID" 2>/dev/null || true
  # 顺带清掉可能残留的子进程
  pkill -f "app.main" 2>/dev/null || true
  wait 2>/dev/null || true
  echo "已停止。"
}
trap cleanup INT TERM EXIT

command -v "$UV" >/dev/null 2>&1 || { echo "找不到 uv($UV);先装 uv 或设 UV=<路径>"; exit 1; }
command -v npm >/dev/null 2>&1 || { echo "找不到 npm;先装 Node.js"; exit 1; }

echo "[1/3] 启动后端 (uvicorn 127.0.0.1:8000)…"
( cd "$ROOT/backend" && exec "$UV" run python -m app.main ) &
BACK_PID=$!

echo "[2/3] 等待后端健康…"
ready=""
for _ in $(seq 1 45); do
  if curl -sf http://127.0.0.1:8000/api/health >/dev/null 2>&1; then ready=1; echo "  后端就绪 ✓"; break; fi
  # 后端进程若已退出则不再空等
  kill -0 "$BACK_PID" 2>/dev/null || { echo "  后端启动失败,见上方日志"; exit 1; }
  sleep 1
done
[ -n "$ready" ] || { echo "  后端 45s 内未就绪,放弃"; exit 1; }

# 触发一次(无 token 的)受保护请求,让后端惰性生成 .api_token,供前端代理读取。
curl -s -o /dev/null -X POST http://127.0.0.1:8000/api/watchdog 2>/dev/null || true

echo "[3/3] 启动前端 (Next.js :3000)…"
( cd "$ROOT/frontend" && { [ -d node_modules ] || npm install; } && exec npm run dev ) &
FRONT_PID=$!

echo
echo "==================================================================="
echo " ✅ stock-agent 已启动"
echo "    前端 UI :  http://localhost:3000"
echo "    后端 API:  http://localhost:8000   (仅本机)"
echo "    Ctrl-C 停止两个服务。"
echo "==================================================================="
echo

# 任一进程退出即整体收摊
wait -n "$BACK_PID" "$FRONT_PID" 2>/dev/null || true
