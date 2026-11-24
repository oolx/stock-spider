# -*- coding:utf-8 -*-
"""FastAPI 入口。

启动：

    cd /home/lin/Documents/stock-spider
    python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000

提供的接口：

* ``GET  /api/symbols``                            标的列表
* ``PUT  /api/symbols``                            修改标的（新增/修改/删除）
* ``GET  /api/history?id=stock:600026&limit=500``  最近 N 个点
* ``WS   /ws``                                     实时 tick 推送
* ``GET  /salary_calculation/``                    摸鱼秒薪计算器（独立页面 / 静态资源）
* ``GET  /``                                       前端页面（Vue3 + ECharts）
"""

import asyncio
import re
from contextlib import asynccontextmanager
from typing import List

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import BASE_DIR, DB_PATH, FRONTEND_DIR, INDEX_NAMES, SUPPORTED_INDEX, SYMBOLS
from .hub import ConnectionHub
from .scheduler import TickScheduler
from .store import TickStore


class SymbolIn(BaseModel):
    symbol: str
    kind: str
    name: str = ""
    # 持仓信息（仅股票/ETF 使用）：成本价与股数，用于计算浮动盈亏
    cost: float = Field(default=0, ge=0, description="持仓成本价，0 表示未填写")
    shares: float = Field(default=0, ge=0, description="持仓股数")


class SymbolListIn(BaseModel):
    items: List[SymbolIn] = Field(default_factory=list)


def _validate_symbols(items: List[SymbolIn]) -> List[dict]:
    """校验并规范化前端提交的标的列表，非法时返回 400。"""
    if not items:
        raise HTTPException(status_code=400, detail="至少保留一个标的")

    normalized = []
    seen = set()
    for raw in items:
        symbol = raw.symbol.strip()
        kind = raw.kind.strip().lower()
        name = (raw.name or "").strip()

        if kind not in ("index", "stock"):
            raise HTTPException(status_code=400, detail="标的类型只能是 index 或 stock")
        if not re.fullmatch(r"\d{6}", symbol):
            raise HTTPException(status_code=400, detail="代码必须是 6 位数字：%s" % symbol)
        if kind == "index" and symbol not in SUPPORTED_INDEX:
            raise HTTPException(
                status_code=400,
                detail="指数仅支持：%s" % " / ".join(SUPPORTED_INDEX),
            )
        if kind == "stock" and symbol[0] not in ("0", "1", "3", "5", "6"):
            raise HTTPException(
                status_code=400,
                detail="股票/ETF 代码需以 0/1/3/5/6 开头：%s" % symbol,
            )

        symbol_id = "%s:%s" % (kind, symbol)
        if symbol_id in seen:
            raise HTTPException(status_code=400, detail="存在重复标的：%s" % symbol_id)
        seen.add(symbol_id)

        if not name:
            name = INDEX_NAMES.get(symbol, symbol) if kind == "index" else symbol

        # 持仓信息：指数没有持仓概念，统一置 0
        cost = float(raw.cost or 0)
        shares = float(raw.shares or 0)
        if cost < 0 or shares < 0:
            raise HTTPException(status_code=400, detail="持仓价和股数不能为负数：%s" % symbol)
        if kind == "index":
            cost, shares = 0, 0

        normalized.append({
            "id": symbol_id,
            "symbol": symbol,
            "kind": kind,
            "name": name,
            "cost": round(cost, 4),
            "shares": round(shares, 4),
        })

    return normalized


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：初始化数据库、启动后台轮询，关闭时清理。"""
    store = TickStore(DB_PATH)
    await store.init()
    await store.ensure_symbols(SYMBOLS)

    hub = ConnectionHub()
    scheduler = TickScheduler(store, hub)
    task = asyncio.create_task(scheduler.run())

    app.state.store = store
    app.state.hub = hub
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await store.close()


app = FastAPI(title="A股实时行情", lifespan=lifespan)

# 允许前端单独部署时跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/symbols")
async def get_symbols(request: Request):
    """返回当前配置的标的列表。"""
    return {"items": await request.app.state.store.get_symbols()}


@app.put("/api/symbols")
async def put_symbols(request: Request, payload: SymbolListIn):
    """整体替换标的列表，支持新增、修改代码和删除。"""
    items = _validate_symbols(payload.items)
    await request.app.state.store.replace_symbols(items)
    return {"items": await request.app.state.store.get_symbols()}


@app.get("/api/history")
async def get_history(
    request: Request,
    id: str = Query(..., description="标的 id，如 index:000001 / stock:600026"),
    limit: int = Query(500, ge=1, le=2000, description="返回的点数，上限受 MAX_POINTS 限制"),
):
    """返回某个标的最近 limit 个 tick（时间正序）。"""
    if ":" not in id:
        raise HTTPException(status_code=400, detail="id 格式应为 kind:symbol")
    items = await request.app.state.store.get_history(id, limit)
    return {"id": id, "items": items}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """实时推送：连接建立后由服务端主动广播所有标的的最新 tick。"""
    hub: ConnectionHub = websocket.app.state.hub
    await hub.connect(websocket)
    try:
        # 客户端不需要发送业务消息，这里只负责保持连接
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        hub.disconnect(websocket)
    except Exception:
        hub.disconnect(websocket)


# 摸鱼秒薪计算器的静态资源（独立页面与行情页共用同一份 css/js）
SALARY_DIR = BASE_DIR / "salary_calculation"
if SALARY_DIR.exists():
    app.mount(
        "/salary_calculation",
        StaticFiles(directory=str(SALARY_DIR), html=True),
        name="salary-calculation",
    )

# 前端静态资源挂在根路径，必须在 API 路由之后注册
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
else:
    @app.get("/")
    async def missing_frontend():
        return JSONResponse({"error": "frontend 目录不存在"}, status_code=404)
