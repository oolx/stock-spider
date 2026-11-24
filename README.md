# A股实时行情（Windows 部署版）

实时抓取 A 股 / ETF 与指数行情，通过 WebSocket 推送到前端，使用 Vue3 + ECharts
以窄栏形式展示；页面下方带有「摸鱼秒薪计算器」，可把工资与持仓收益合并为「今日已赚」。

数据库已从 Redis **彻底替换为 SQLite**（Python 标准库自带），部署时**只需安装 Python 依赖**，
不需要额外安装 Redis 或任何数据库服务，方便迁移到 Windows 工作机。

---

## 一、目录结构

```
stock-spider/
├─ backend/              后端服务（FastAPI）
│  ├─ main.py            入口与 REST / WebSocket 接口
│  ├─ scheduler.py       交易时段定时抓取
│  ├─ store.py           SQLite 数据存取
│  ├─ hub.py             WebSocket 连接与广播
│  └─ config.py          全局配置
├─ spider/               行情爬虫
├─ frontend/             行情页面（Vue3 + ECharts）
├─ salary_calculation/   摸鱼秒薪计算器
├─ data/                 SQLite 数据库目录（首次运行自动生成）
├─ logs/                 运行日志（自动生成）
├─ requirements.txt      Python 依赖
├─ deploy.bat            一键部署脚本
├─ run.bat               一键运行脚本
└─ README.md             本文档
```

---

## 二、环境要求

- Windows 10 / 11
- 已安装 **Anaconda** 或 **Miniconda**（仅需 conda）
- 能正常访问东方财富行情接口（需要外网）

> 数据库使用 Python 自带的 `sqlite3`，无需安装 Redis、MySQL 等任何服务。

---

## 三、一键部署

1. 将整个 `stock-spider` 文件夹复制到 Windows 电脑（建议路径不含空格和特殊符号，例如 `D:\stock-spider`）。
2. 双击 **`deploy.bat`**。脚本会自动完成：
   - 定位 conda；
   - 创建 conda 环境 `stock-spider`（Python 3.10）；
   - 安装 `requirements.txt` 中的 Python 依赖；
   - 依赖自检并打印 SQLite 版本。
3. 看到「部署完成！」即表示成功。

> 默认使用清华 pip 镜像；若安装失败会自动改用官方源重试。

---

## 四、一键运行

1. 双击 **`run.bat`**。
2. 脚本会启动服务并自动打开浏览器：<http://127.0.0.1:8000/>
   （若浏览器未自动打开，手动访问该地址即可）。
3. 关闭命令行窗口即停止服务。

---

## 五、手动部署（可选，不使用 bat 时）

```bat
conda create -y -n stock-spider -c conda-forge --override-channels python=3.10 pip
conda activate stock-spider
pip install -r requirements.txt
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

---

## 五之二、Linux 一键启动 / 重启

Linux（开发机或服务器）使用 `start.sh`，支持 `start / stop / restart / status`，
默认执行 `restart`（先停后启）：

```bash
chmod +x start.sh
./start.sh            # 默认重启
./start.sh start      # 启动
./start.sh stop       # 停止
./start.sh restart    # 重启
./start.sh status     # 查看状态
```

可选环境变量（直接加在命令前）：

```bash
ENV_NAME=python-stock HOST=0.0.0.0 PORT=8000 ./start.sh restart
PYTHON=/path/to/python ./start.sh restart
```

脚本行为：

- 自动查找 conda 环境 `python-stock` 的解释器（也可用 `PYTHON` 指定）；
- 后台启动 uvicorn，PID 写入 `run/app.pid`，输出追加到 `logs/app.log`；
- 启动后自动检测端口是否就绪；
- 停止时先温和结束，超时后强制结束。

---

## 六、数据与配置

- **数据库文件**：`data/stock.db`（SQLite），首次启动自动创建。
- **标的配置**（指数/股票/ETF、名称、持仓价、股数）保存在数据库中，重启后保留。
- **秒薪计算器参数**（上班时间、下班时间、日薪）同时保存到浏览器 localStorage 和数据库，打开页面自动恢复。
- **迁移到其他机器**：复制整个项目目录（确保包含 `data/stock.db`）即可沿用全部设置，无需重新配置。
- **行情数据**：每个标的只保留最近 500 个点，写入数据库时自动裁剪。
- **日志**：`logs/` 目录下按启动时间生成日志文件。
- **重置**：关闭服务后删除 `data/stock.db`，再启动即恢复默认标的。

### 交易时段

- 抓取时段：`09:30–11:30`、`13:00–15:30`（可在 `backend/config.py` 调整）。
- 周末视为非交易时段。
- 非交易时段**不会请求行情接口**，仅每 30 秒检查一次时间。

---

## 七、功能说明

- **实时行情**：交易时段每 3 秒抓取一次，WebSocket 推送；所有折线合并到同一图表，支持指数/股票双 Y 轴。
- **标的设置**：右上角「标的设置」可增删改指数、股票/ETF。
  - 指数支持：`399001` / `000001` / `399006` / `000300`
  - 股票/ETF 支持：以 `0/1/3/5/6` 开头的 6 位代码
- **持仓收益**：为股票/ETF 填写「持仓价」和「股数」后，按
  `(最新价 − 昨收) × 股数` 计算今日投资盈亏，并叠加到「今日已赚」。
  该口径每个交易日开盘自然归零。
- **摸鱼秒薪计算器**：设置上班时间、下班时间、日薪，实时显示「今日已赚 = 工资 + 投资」；
  工资按当日上班时刻起算，每天上班自动从 0 开始累计。

---

## 八、常见问题

| 问题 | 处理方式 |
| --- | --- |
| 提示未找到 conda | 确认已安装 Anaconda / Miniconda；脚本会自动查找常见安装目录，也可将 conda 加入系统 PATH |
| pip 安装慢或失败 | `deploy.bat` 默认使用清华镜像，失败会自动改用官方源；也可手动修改脚本中的 `PIP_MIRROR` |
| 端口 8000 被占用 | 修改 `run.bat` 中的 `PORT` 为其他端口 |
| 想局域网/其他设备访问 | 将 `run.bat` 中的 `HOST` 改为 `0.0.0.0`，并在 Windows 防火墙放行对应端口 |
| 页面暂时没有数据 | 非交易时段不抓取；交易时段启动后约 3 秒出现数据 |
| 想恢复默认标的 | 关闭服务后删除 `data/stock.db`，重新启动 |
| 控制台中文乱码 | 脚本已执行 `chcp 65001`；若仍异常，可在 Windows 终端设置中选择「UTF-8」编码 |

---

## 九、卸载

```bat
conda env remove -n stock-spider
```

然后直接删除 `stock-spider` 目录即可。
