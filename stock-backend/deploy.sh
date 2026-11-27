#!/usr/bin/env bash
# ============================================================
# A股实时行情后端 - Linux 一键部署
#
# 自动创建 .venv 虚拟环境并安装 requirements.txt 依赖，
# 之后使用 ./start.sh 启动 / 停止 / 重启。
#
# 可选环境变量：
#   PYTHON_BIN=python3             指定用于创建虚拟环境的 Python
#   PIP_MIRROR=https://...         指定 pip 镜像源（默认清华）
# ============================================================
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
PIP_MIRROR="${PIP_MIRROR:-https://pypi.tuna.tsinghua.edu.cn/simple}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "[错误] 未找到 $PYTHON_BIN，请先安装 Python 3.9+"
  exit 1
fi

if ! "$PYTHON_BIN" -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)"; then
  echo "[错误] Python 版本过低，需要 3.9 及以上，当前：$("$PYTHON_BIN" -V 2>&1)"
  exit 1
fi

if ! "$PYTHON_BIN" -c "import venv, ensurepip" >/dev/null 2>&1; then
  echo "[错误] 当前 Python 缺少 venv/ensurepip 模块。"
  echo "       Debian/Ubuntu 请先执行：sudo apt install -y python3-venv"
  exit 1
fi

if [ ! -d "$APP_DIR/.venv" ]; then
  echo "[1/3] 创建虚拟环境 .venv ..."
  "$PYTHON_BIN" -m venv "$APP_DIR/.venv"
else
  echo "[1/3] 已存在 .venv，跳过创建"
fi

PY="$APP_DIR/.venv/bin/python"
if ! "$PY" -m pip install --upgrade pip -i "$PIP_MIRROR" --disable-pip-version-check; then
  echo "[警告] 镜像源升级 pip 失败，改用官方源重试 ..."
  "$PY" -m pip install --upgrade pip --disable-pip-version-check
fi

echo "[2/3] 安装依赖 ..."
if ! "$PY" -m pip install -r "$APP_DIR/requirements.txt" -i "$PIP_MIRROR" --disable-pip-version-check; then
  echo "[警告] 镜像源安装失败，改用官方源重试 ..."
  "$PY" -m pip install -r "$APP_DIR/requirements.txt" --disable-pip-version-check
fi

echo "[3/3] 依赖自检 ..."
"$PY" -c "import sqlite3, fastapi, uvicorn, requests, pandas, holidays; print('依赖自检通过，SQLite', sqlite3.sqlite_version)"

echo
echo "部署完成！"
echo "  启动：./start.sh start"
echo "  重启：./start.sh restart"
echo "  状态：./start.sh status"
echo "  停止：./start.sh stop"
echo "云服务器对外提供服务：HOST=0.0.0.0 PORT=8000 ./start.sh restart"
