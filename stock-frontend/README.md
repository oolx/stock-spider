# A股实时行情 - 前端与客户端（stock-frontend）

本仓库只包含**前端页面与各平台客户端构建**，不包含任何后端代码：

- `frontend/`：行情页面（Vue3 + ECharts），Electron 渲染层与安卓 WebView 的同一份源码
- `salary_calculation/`：秒薪计算器（行情页内嵌，同一份前端资源）
- `desktop/`：Electron 桌面端工程（无边框窗口 + 图标）
- `mobile/`：Capacitor 安卓工程（含原生Java端动态壁纸）

后端是独立的 `stock-backend` 项目，负责行情采集、SQLite 存储、统计（月汇总等）
并通过 REST API / WebSocket 对外提供数据。客户端只负责展示，首次启动时填写后端地址即可。

---

## 零、开发流程（单一来源）

**`frontend/` + `salary_calculation/` 是所有平台唯一的代码来源**，
安卓/桌面端只是把同一份前端资源打包进各自的壳里，不允许在副本里直接改。

新增或修改界面的标准流程：

1. 只改 `frontend/`（必要时 `salary_calculation/`），平台差异用 `is-native` / `is-desktop` 类做适配；
2. 同步 Web 资源：`cd mobile && npm run sync-web`
   —— 会同时刷新 `mobile/www/` 和 `mobile/android/app/src/main/assets/public/`；
3. 再构建对应平台：
   - Android：`cd mobile/android && ./gradlew installDebug`（或 `npm run build:apk`）
   - Linux 桌面：`cd desktop && npm run dist`
   - Windows 桌面：在 Windows 上 `cd desktop && npx electron-builder --win`
   - iOS：在 macOS 上 `cd mobile && npx cap add ios && npm run sync-web` 后用 Xcode 打包

> `mobile/www/`、`mobile/android/app/src/main/assets/public/`、`desktop/dist/` 都是生成产物（已在 `.gitignore` 中），
> 直接修改会在下次同步时被覆盖。

---

## 一、目录结构

```
stock-frontend/
├─ frontend/             行情页面（Vue3 + ECharts）
│  ├─ index.html        页面结构
│  ├─ app.js            行情、日历、标的设置等逻辑
│  ├─ config.js         服务器地址与运行平台判定
│  ├─ style.css         样式（桌面 / 安卓自适应）
│  └─ vendor/           Vue、ECharts 等本地依赖
├─ salary_calculation/   秒薪计算器（行情页内嵌）
├─ desktop/              Electron 工程（主进程 + AppImage 打包配置）
├─ mobile/               Capacitor 安卓工程
│  ├─ capacitor.config.json
│  ├─ scripts/copy-web.js  把前端资源复制到 www/
│  └─ android/          Android 原生工程（含动态壁纸服务）
└─ README.md             本文档
```

---

## 二、前端开发

前端是纯静态资源，不需要构建步骤，改完刷新即可：

- 桌面端调试：`cd desktop && npm start`
- 浏览器预览：直接打开 `frontend/index.html`（需允许本地文件访问后端），
  或在本目录起一个静态服务，例如 `python3 -m http.server 5173`

服务器地址优先级（见 `frontend/config.js`）：

1. `window.STOCK_API_BASE`（启动前注入）
2. 状态栏「实时连接」中保存的地址（localStorage，键 `stock_server_v1`）
3. Electron 默认 `http://127.0.0.1:8000`；安卓端未配置时弹出服务器设置

---

## 三、桌面应用（Electron）

需要 Node.js 18+。

```bash
cd desktop
npm install
npm run dist          # 构建 Linux AppImage
npm start             # 开发调试
```

产物：`desktop/dist/AStock-1.0.0-x86_64.AppImage`，赋执行权限后直接运行。
Windows 端可在 Windows 机器上执行 `npx electron-builder --win` 构建。

- 窗口固定 380×540，无边框透明 + 圆角，顶部栏可拖动
- 启动时自动探测后端，未连接会提示；点击顶部「实时连接」可修改后端 IP/端口/HTTPS
- 后端不托管静态文件，桌面端从本地文件加载页面

---

## 四、安卓客户端（Capacitor）

构建环境：JDK 17、Android SDK（platform 34 / build-tools 34.0.0）。

```bash
cd mobile
npm install
npm run sync-web        # 仅复制前端资源到 www/
npm run build:apk       # 复制资源 + 同步 + gradle 打包
```

APK 输出：`mobile/android/app/build/outputs/apk/debug/app-debug.apk`。

说明：

- 安卓端没有本地后端，首次启动弹出「服务器设置」，填写运行后端的电脑 IP 或云服务器地址；
- 手机与后端需在同一局域网（或用公网地址），后端需监听 `0.0.0.0`；
- 应用已允许明文 HTTP（`usesCleartextTraffic` + 混合内容），云服务器可用 HTTPS/WSS；
- 动态壁纸为原生 Canvas 绘制，通过「实时连接 → 设为动态壁纸」配置并应用，
  后台定期拉取 `/api/history` 绘制整段走势。

---

## 五、与后端的关系

客户端用到的接口（均由 `stock-backend` 提供）：

| 接口 | 用途 |
| --- | --- |
| `GET /api/symbols` | 标的列表与持仓配置 |
| `GET /api/history?id=&limit=&step=` | 历史行情（`step` 抽样用于长走势） |
| `GET /api/settings` | 秒薪计算器参数 |
| `GET /api/month-summary?year=&month=` | 日历页月度工资与投资统计 |
| `WS /ws` | 实时 tick 推送 |

- 数据库、标的配置、持仓快照、月统计都在后端，客户端不保存业务数据（仅保存服务器地址与秒薪参数缓存）；
- 行情只在交易时段（09:30–11:30、13:00–15:30）由后端抓取，非交易时段图表显示当日已有数据。

---

## 六、功能说明

- **实时行情**：折线合并到同一图表，指数/股票双 Y 轴，支持最多多个标的
- **标的设置**：右上角「标的设置」可增删改指数、股票/ETF
  - 指数支持：`399001` / `000001` / `399006` / `000300`
  - 股票/ETF 支持：以 `0/1/3/5/6` 开头的 6 位代码
- **持仓收益**：填写持仓价与股数后，按 `(最新价 − 昨收) × 股数` 计算当日投资盈亏
- **秒薪计算器**：设置上班时间、下班时间、日薪，实时显示「今日已赚 = 工资 + 投资」
- **日历页**：按日查看当月工资与投资收入，投资按当日历史行情计算

---

## 七、常见问题

| 问题 | 处理方式 |
| --- | --- |
| 客户端显示「连接中…」 | 确认后端已启动、地址端口填写正确，手机/电脑在同一网段 |
| 页面暂无数据 | 非交易时段不产生新行情；交易时段启动后端后约 3 秒出现数据 |
| 安卓构建报 Node 版本错误 | `copy-web.js` 已兼容低版本 Node，仍失败请升级到 Node 18+ |
| 打不开 AppImage | `chmod +x AStock-1.0.0-x86_64.AppImage` 后再运行 |
| 后端地址填错 | 顶部「实时连接」→ 修改地址 → 保存并重连 |
