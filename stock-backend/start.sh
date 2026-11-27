#!/usr/bin/env bash
# ============================================================
# A股实时行情 - Linux 一键启动 / 停止 / 重启
#
# 用法：
#   ./start.sh            # 默认执行 restart（停止后重新启动）
#   ./start.sh start      # 启动
#   ./start.sh stop       # 停止
#   ./start.sh restart    # 重启
#   ./start.sh status     # 查看状态
#
# 可选环境变量：
#   ENV_NAME=python-stock   conda 环境名
#   HOST=0.0.0.0          监听地址
#   PORT=8000               监听端口
#   PYTHON=/path/to/python  直接指定解释器
# ============================================================
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

ENV_NAME="${ENV_NAME:-python-stock}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
PID_FILE="$APP_DIR/run/app.pid"
LOG_FILE="$APP_DIR/logs/app.log"

# ---------- 选择 Python 解释器 ----------
resolve_python() {
  if [ -n "${PYTHON:-}" ] && [ -x "${PYTHON}" ]; then
    echo "${PYTHON}"
    return 0
  fi

  local candidates=()
  # deploy.sh 创建的虚拟环境优先
  candidates+=("$APP_DIR/.venv/bin/python")
  if [ -n "${CONDA_PREFIX:-}" ]; then
    candidates+=("$CONDA_PREFIX/bin/python")
  fi
  candidates+=(
    "$HOME/anaconda3/envs/$ENV_NAME/bin/python"
    "$HOME/miniconda3/envs/$ENV_NAME/bin/python"
    "/opt/conda/envs/$ENV_NAME/bin/python"
  )

  local p
  for p in "${candidates[@]}"; do
    if [ -x "$p" ]; then
      echo "$p"
      return 0
    fi
  done

  if command -v conda >/dev/null 2>&1; then
    local base
    base="$(conda info --base 2>/dev/null || true)"
    if [ -n "$base" ] && [ -x "$base/envs/$ENV_NAME/bin/python" ]; then
      echo "$base/envs/$ENV_NAME/bin/python"
      return 0
    fi
  fi

  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return 0
  fi

  return 1
}

# ---------- 进程状态 ----------
is_running() {
  [ -f "$PID_FILE" ] || return 1
  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

port_ready() {
  local py="$1"
  "$py" - "$HOST" "$PORT" >/dev/null 2>&1 <<'PY'
import socket, sys
host, port = sys.argv[1], int(sys.argv[2])
with socket.socket() as s:
    s.settimeout(1)
    s.connect((host, port))
PY
}

# ---------- 启动 ----------
start_app() {
  if is_running; then
    echo "[信息] 服务已在运行（PID $(cat "$PID_FILE")），如需重启请执行：$0 restart"
    return 0
  fi

  local py
  if ! py="$(resolve_python)"; then
    echo "[错误] 未找到可用的 Python，请设置 PYTHON，或先创建 conda 环境：$ENV_NAME"
    return 1
  fi

  # 端口占用检查：restart 时旧进程可能还没完全释放端口，先等待几秒
  local i
  for i in $(seq 1 10); do
    port_ready "$py" || break
    sleep 0.5
  done
  if port_ready "$py"; then
    echo "[错误] 端口 $PORT 已被其他程序占用，无法启动。"
    echo "       请先停止占用该端口的程序（例如旧版后端），或改用其他端口：PORT=8010 $0 start"
    return 1
  fi

  mkdir -p "$APP_DIR/logs" "$APP_DIR/run" "$APP_DIR/data"

  echo "[信息] 使用解释器：$py"
  echo "[信息] 启动服务：http://$HOST:$PORT/"

  nohup "$py" -m uvicorn backend.main:app --host "$HOST" --port "$PORT" >> "$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"

  for i in $(seq 1 30); do
    if ! is_running; then
      echo "[错误] 服务启动失败，日志：$LOG_FILE"
      tail -n 20 "$LOG_FILE" || true
      rm -f "$PID_FILE"
      return 1
    fi
    if port_ready "$py"; then
      echo "[成功] 服务已启动（PID $(cat "$PID_FILE")）"
      return 0
    fi
    sleep 0.5
  done

  echo "[警告] 进程已启动，但端口暂未就绪，请稍后查看日志：$LOG_FILE"
  return 0
}

# ---------- 停止 ----------
stop_app() {
  if ! is_running; then
    echo "[信息] 服务未在运行"
    rm -f "$PID_FILE"
    return 0
  fi

  local pid
  pid="$(cat "$PID_FILE")"
  echo "[信息] 正在停止服务（PID $pid）..."
  kill "$pid" 2>/dev/null || true

  local i
  for i in $(seq 1 20); do
    if ! kill -0 "$pid" 2>/dev/null; then
      break
    fi
    sleep 0.5
  done

  if kill -0 "$pid" 2>/dev/null; then
    echo "[警告] 进程未退出，强制结束"
    kill -9 "$pid" 2>/dev/null || true
  fi

  rm -f "$PID_FILE"
  echo "[成功] 服务已停止"
}

# ---------- 状态 ----------
status_app() {
  if is_running; then
    echo "[运行中] PID $(cat "$PID_FILE")，地址 http://$HOST:$PORT/"
  else
    echo "[未运行]"
  fi
}

ACTION="${1:-restart}"
case "$ACTION" in
  start)   start_app ;;
  stop)    stop_app ;;
  restart) stop_app; start_app ;;
  status)  status_app ;;
  *)
    echo "用法：$0 {start|stop|restart|status}"
    exit 1
    ;;
esac
