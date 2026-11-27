# A股实时行情 - 后端服务（独立部署包）

本目录是**独立的后端部署包**，只包含 Python 后端与部署脚本，不包含桌面/安卓客户端。
客户端（Electron 桌面端、Android App）通过「实时连接」填写本机地址与端口即可连接。

- 提供 REST API 与 WebSocket 实时行情推送
- 数据库使用 Python 标准库自带的 **SQLite**，无需安装 Redis/MySQL 等任何数据库服务
- 支持 Linux 与 Windows 部署

---

## 一、目录结构

```
stock-backend/
├─ backend/              后端服务（FastAPI）
│  ├─ main.py            API / WebSocket 入口
│  ├─ scheduler.py       交易时段定时抓取
│  ├─ store.py           SQLite 存取
│  ├─ hub.py             WebSocket 广播
│  └─ config.py          端口、标的、交易时段等配置
├─ spider/               行情爬虫
├─ requirements.txt      Python 依赖
├─ deploy.sh             Linux 一键部署（创建 .venv 并安装依赖）
├─ start.sh              Linux 一键启动/停止/重启
├─ deploy.bat            Windows 一键部署（创建 conda 环境）
├─ run.bat               Windows 一键启动/停止/重启
└─ README.md             本文档
```

首次启动会自动创建 `data/`（SQLite 数据库）、`logs/`、`run/` 目录。

---

## 二、环境要求

- Python 3.10+（Windows 使用 conda，Linux 使用 python3 + venv）
- 能访问东方财富行情接口（需要外网）
- 无需任何数据库服务

---

## 三、Linux 部署（含云服务器）

```bash
chmod +x deploy.sh start.sh

./deploy.sh             # 创建 .venv 并安装依赖
./start.sh start        # 启动
./start.sh status       # 查看状态
./start.sh restart      # 重启
./start.sh stop         # 停止
```

默认监听 `127.0.0.1:8000`。若要对局域网或云服务器公网提供服务：

```bash
HOST=0.0.0.0 PORT=8000 ./start.sh restart
```

> 云服务器需在安全组/防火墙放行对应 TCP 端口。

也支持使用已有的 conda 环境（无需 deploy.sh）：

```bash
ENV_NAME=python-stock ./start.sh restart
PYTHON=/path/to/python ./start.sh restart
```

### 可选：systemd 常驻

```ini
[Unit]
Description=AStock backend
After=network.target

[Service]
WorkingDirectory=/opt/stock-backend
Environment=HOST=0.0.0.0
Environment=PORT=8000
ExecStart=/opt/stock-backend/.venv/bin/python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

### 可选：HTTPS 反向代理

若通过 Nginx/Caddy 配置了 HTTPS，客户端「服务器设置」中勾选 HTTPS 即可（API 走 https，WebSocket 自动走 wss）。
反向代理需同时转发 WebSocket（`/ws`）并支持 Upgrade。

---

## 四、Windows 部署

1. 将本目录复制到 Windows 电脑（建议路径不含空格，如 `D:\stock-backend`）
2. 双击 `deploy.bat`：自动创建 conda 环境 `stock-spider` 并安装依赖
3. 双击 `run.bat`：默认重启（先停后启）

命令行用法：

```bat
run.bat            :: 默认重启
run.bat start
run.bat stop
run.bat restart
run.bat status
```

可选环境变量：

```bat
set PORT=8001
set HOST=0.0.0.0
run.bat restart
```

服务在后台运行，关闭命令行窗口不会停止服务；PID 写入 `run\app.pid`，日志追加到 `logs\app.log`。

---

## 五、客户端如何连接

在桌面端/安卓端顶部点击「实时连接」（服务器设置），填写：

| 场景 | 地址 | 端口 | HTTPS |
| --- | --- | --- | --- |
| 后端与客户端同一台电脑 | `127.0.0.1` | `8000` | 不勾选 |
| 局域网其他电脑/手机 | 后端电脑的局域网 IP（如 `192.168.1.20`） | `8000` | 不勾选 |
| 云服务器（HTTP） | 公网 IP 或域名 | `8000` | 不勾选 |
| 云服务器（HTTPS 反代） | 域名 | `443` | 勾选 |

远程访问时后端必须以 `HOST=0.0.0.0` 启动，并放行端口。若连不上，先在本机执行：

```bash
curl http://127.0.0.1:8000/api/symbols
```

返回值正常说明后端已启动，问题一般在网络/防火墙。

---

## 六、数据、日志与迁移

| 内容 | 位置 | 说明 |
| --- | --- | --- |
| 数据库 | `data/stock.db` | 标的配置、持仓、秒薪设置、行情点 |
| 日志 | `logs/app.log` | 启动与运行日志（追加写入） |
| PID | `run/app.pid` | 进程管理使用 |

迁移到其他电脑时，复制整个目录并保留 `data/stock.db` 即可沿用全部配置；
若希望全新开始，删除 `data/stock.db` 后重启会自动写入默认标的。

---

## 七、接口说明

- `GET /api/symbols`：标的配置
- `GET /api/history`：历史行情点
- `GET /api/month-summary`：日历页月度统计
- `WS /ws`：实时行情推送
- 根路径 `/` 返回 404 属正常现象：后端不再托管前端页面，前端由客户端加载

交易时段（工作日 09:30–11:30、13:00–15:30）每 3 秒抓取一次；
非交易时段不请求行情接口，仅定时检查时间。

---

## 八、常见问题

| 问题 | 处理 |
| --- | --- |
| 端口被占用 | `PORT=8001 ./start.sh restart`（Windows 先 `set PORT=8001`） |
| 其他设备连不上 | 确认 `HOST=0.0.0.0`、安全组/防火墙已放行、客户端填写的是后端机器 IP |
| 页面/客户端暂无数据 | 非交易时段不抓取；交易时段启动后约 3 秒出现数据 |
| 依赖安装慢或失败 | `PIP_MIRROR=... ./deploy.sh`；或手动使用 conda 环境 |
| 想恢复默认标的 | 停止服务 → 删除 `data/stock.db` → 重新启动 |
