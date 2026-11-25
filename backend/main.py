# -*- coding:utf-8 -*-
"""FastAPI 入口。

启动：

    cd /home/lin/Documents/stock-spider
    python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000

提供的接口：

* ``GET  /api/symbols``                            标的列表
* ``PUT  /api/symbols``                            修改标的（新增/修改/删除）
* ``GET  /api/settings``                           秒薪计算器参数
* ``PUT  /api/settings``                           保存秒薪计算器参数
* ``GET  /api/history?id=stock:600026&limit=500``  最近 N 个点
* ``GET  /api/calendar?year=2026&month=11``        月度日历（工作日/休息日/假期）
* ``GET  /api/month-summary?year=2026&month=11``   当月每个工作日的工资+投资
* ``GET  /api/daily-quote``                        某个标的历史某日涨跌额
* ``GET  /api/day-holdings?date=2026-11-24``       某日保存的持仓快照
* ``PUT  /api/day-holdings``                       保存某日持仓（不影响行情页）
* ``WS   /ws``                                     实时 tick 推送

后端只提供 API 与 WebSocket，不再托管前端静态资源；
前端由 Electron 桌面应用（desktop/）从本地文件加载。
"""

import asyncio
import calendar
import json
import re
from contextlib import asynccontextmanager
from datetime import date as date_cls, timedelta
from typing import List

import requests
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .config import DB_PATH, INDEX_NAMES, SUPPORTED_INDEX, SYMBOLS
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


class SalarySettingsIn(BaseModel):
    """摸鱼秒薪计算器参数，保存在本地 SQLite，重启/换机部署后自动恢复。"""
    start: str = "09:00"
    end: str = "18:00"
    daily: float = Field(default=300, ge=0)


class DayHoldingsIn(BaseModel):
    """某一天使用的标的与持仓快照。"""
    date: str
    items: List[SymbolIn] = Field(default_factory=list)


def _validate_symbols(items: List[SymbolIn]) -> List[dict]:
    """校验并规范化前端提交的标的列表；空列表表示当天不投资。"""
    if not items:
        return []

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


# 指数代码 -> 东财 secid（指数首位数字与市场不对应，需显式登记）
_INDEX_SECID = {
    "000001": "1.000001",
    "000300": "1.000300",
    "399001": "0.399001",
    "399006": "0.399006",
}

# holiday-cn 提供含调休的完整节假日数据，失败时退回 holidays 库
_HOLIDAY_URLS = (
    "https://cdn.jsdelivr.net/gh/NateScarlet/holiday-cn@master/%d.json",
    "https://raw.githubusercontent.com/NateScarlet/holiday-cn/master/%d.json",
)


def _secid(kind: str, symbol: str):
    """把标的转换成东财 secid；不支持时返回 None。"""
    if kind == "index":
        return _INDEX_SECID.get(symbol)
    if symbol and symbol[0] in ("6", "5"):
        return "1." + symbol
    if symbol and symbol[0] in ("0", "3", "1"):
        return "0." + symbol
    return None


def _fetch_year_holidays(year: int):
    """拉取某年的节假日（含调休补班），返回 holiday-cn 的 days 列表。"""
    for template in _HOLIDAY_URLS:
        try:
            resp = requests.get(template % year, timeout=8)
            resp.raise_for_status()
            days = resp.json().get("days")
            if isinstance(days, list) and days:
                return days
        except Exception:
            continue
    return None


def _fallback_year_holidays(year: int):
    """网络不可用时的兜底：只用法定节假日，周末按休息日处理。"""
    try:
        import holidays as holiday_lib
        return [
            {"date": str(day), "name": name, "isOffDay": True}
            for day, name in holiday_lib.CN(years=[year]).items()
        ]
    except Exception:
        return []


def _fetch_daily_quote(kind: str, symbol: str, day: str):
    """读取某个标的历史某一天的收盘价与涨跌额（不复权，涨跌额即当日盈亏基准）。"""
    secid = _secid(kind, symbol)
    if not secid:
        return None
    try:
        end = date_cls.fromisoformat(day)
    except ValueError:
        return None
    beg = end - timedelta(days=10)
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",
        "fqt": "0",
        "beg": beg.strftime("%Y%m%d"),
        "end": end.strftime("%Y%m%d"),
    }
    resp = requests.get(
        "http://push2his.eastmoney.com/api/qt/stock/kline/get",
        params=params,
        timeout=8,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    resp.raise_for_status()
    data = resp.json().get("data") or {}
    for line in data.get("klines") or []:
        parts = line.split(",")
        if len(parts) >= 10 and parts[0] == day:
            return {
                "date": parts[0],
                "close": float(parts[2]),
                "pct": float(parts[8]),
                "change": float(parts[9]),
                "name": data.get("name") or symbol,
            }
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：初始化数据库、启动后台轮询，关闭时清理。"""
    store = TickStore(DB_PATH)
    await store.init()
    # 用一个标记区分「首次启动」和「用户主动清空标的」：
    # 只有首次启动才写入默认标的，避免重启后把用户清空的配置还原。
    if not await store.get_setting("symbols_initialized"):
        await store.ensure_symbols(SYMBOLS)
        await store.set_setting("symbols_initialized", "1")

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

# 前端由 Electron 以 file:// 加载，需要允许跨域访问本服务
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
    """整体替换标的列表，同时记录为「今天」的持仓快照。"""
    items = _validate_symbols(payload.items)
    await request.app.state.store.replace_symbols(items)
    # 首页标的代表当前持仓，写入今天的快照后，日历里之后的工作日会沿用这份设置
    today = date_cls.today().isoformat()
    await request.app.state.store.save_day_holdings(
        today, json.dumps(items, ensure_ascii=False)
    )
    await request.app.state.store.set_setting("symbols_initialized", "1")
    return {"items": await request.app.state.store.get_symbols()}


@app.get("/api/settings")
async def get_settings(request: Request):
    """返回秒薪计算器参数；没有保存过时返回空对象。"""
    raw = await request.app.state.store.get_setting("salary")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        return {}


@app.put("/api/settings")
async def put_settings(request: Request, payload: SalarySettingsIn):
    """保存秒薪计算器参数（上班时间、下班时间、日薪）。"""
    if not re.fullmatch(r"\d{2}:\d{2}", payload.start) or not re.fullmatch(r"\d{2}:\d{2}", payload.end):
        raise HTTPException(status_code=400, detail="时间格式应为 HH:MM")
    data = {"start": payload.start, "end": payload.end, "daily": payload.daily}
    await request.app.state.store.set_setting("salary", json.dumps(data, ensure_ascii=False))
    return data


def _fetch_kline_closes(kind: str, symbol: str, start: str, end: str):
    """一次拉取区间内每个交易日的收盘价，返回 {date: close}。"""
    secid = _secid(kind, symbol)
    if not secid:
        return {}
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",
        "fqt": "0",
        "beg": start.replace("-", ""),
        "end": end.replace("-", ""),
    }
    resp = requests.get(
        "http://push2his.eastmoney.com/api/qt/stock/kline/get",
        params=params,
        timeout=10,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    resp.raise_for_status()
    data = resp.json().get("data") or {}
    closes = {}
    for line in data.get("klines") or []:
        parts = line.split(",")
        if len(parts) >= 3:
            try:
                closes[parts[0]] = float(parts[2])
            except ValueError:
                continue
    return closes


async def _calendar_days(store: TickStore, year: int, month: int) -> List[dict]:
    """返回某月每天的日历类型（含调休），年度节假日数据缓存在 settings。"""
    key = "calendar:%d" % year
    raw = await store.get_setting(key)
    days = None
    if raw:
        try:
            days = json.loads(raw)
        except ValueError:
            days = None
    if not isinstance(days, list):
        loop = asyncio.get_event_loop()
        days = await loop.run_in_executor(None, _fetch_year_holidays, year)
        if not days:
            days = _fallback_year_holidays(year)
        try:
            await store.set_setting(key, json.dumps(days, ensure_ascii=False))
        except Exception:
            pass

    off_map = {
        item.get("date"): item
        for item in days
        if isinstance(item, dict) and item.get("date")
    }
    result = []
    _, last_day = calendar.monthrange(year, month)
    for day_num in range(1, last_day + 1):
        current = date_cls(year, month, day_num)
        ds = current.isoformat()
        item = off_map.get(ds)
        if item is not None:
            is_off = bool(item.get("isOffDay"))
            day_type = "holiday" if is_off else "work"
            name = item.get("name") or ""
        else:
            day_type = "rest" if current.weekday() >= 5 else "work"
            name = ""
        result.append({
            "date": ds,
            "day": day_num,
            "weekday": current.weekday(),
            "type": day_type,
            "name": name,
        })
    return result


@app.get("/api/calendar")
async def get_calendar(
    request: Request,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
):
    """返回某个月的日历：工作日 / 休息日 / 假期（含调休补班）。"""
    days = await _calendar_days(request.app.state.store, year, month)
    return {"year": year, "month": month, "days": days}


@app.get("/api/month-summary")
async def get_month_summary(
    request: Request,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
):
    """当月每个工作日的收入：工资 +（当日收盘价 − 持仓价）× 股数。"""
    store = request.app.state.store
    days = await _calendar_days(store, year, month)
    work_days = [item for item in days if item["type"] == "work"]
    if not work_days:
        return {"year": year, "month": month, "days": []}

    # 工资沿用秒薪计算器的日薪设置
    salary = 0.0
    raw = await store.get_setting("salary")
    if raw:
        try:
            salary = float(json.loads(raw).get("daily") or 0)
        except (ValueError, TypeError, AttributeError):
            salary = 0.0

    snapshots = []
    for row in await store.list_day_holdings():
        try:
            items = json.loads(row["items"])
        except ValueError:
            items = []
        snapshots.append((row["date"], items))

    def holdings_on(day: str):
        """沿用该日之前最近一份快照。"""
        chosen = []
        for snapshot_day, items in snapshots:
            if snapshot_day > day:
                break
            chosen = items
        return chosen

    day_items = {item["date"]: holdings_on(item["date"]) for item in work_days}

    # 收集需要行情的股票，每只股票整月只请求一次收盘价
    needed = set()
    for items in day_items.values():
        for item in items:
            if item.get("kind") == "stock" \
                    and float(item.get("cost") or 0) > 0 \
                    and float(item.get("shares") or 0) > 0:
                needed.add(item["symbol"])

    loop = asyncio.get_event_loop()
    first_day = work_days[0]["date"]
    last_day = work_days[-1]["date"]
    closes = {}
    for symbol in needed:
        try:
            closes[symbol] = await loop.run_in_executor(
                None, _fetch_kline_closes, "stock", symbol, first_day, last_day
            )
        except Exception:
            closes[symbol] = {}

    result = []
    for day in work_days:
        invest = 0.0
        for item in day_items[day["date"]]:
            if item.get("kind") != "stock":
                continue
            cost = float(item.get("cost") or 0)
            shares = float(item.get("shares") or 0)
            if cost <= 0 or shares <= 0:
                continue
            close = closes.get(item["symbol"], {}).get(day["date"])
            if close is not None:
                invest += (close - cost) * shares
        result.append({
            "date": day["date"],
            "salary": salary,
            "invest": round(invest, 2),
        })
    return {"year": year, "month": month, "days": result}


@app.get("/api/daily-quote")
async def get_daily_quote(
    kind: str = Query(..., description="index 或 stock"),
    symbol: str = Query(..., description="6 位代码"),
    date: str = Query(..., description="YYYY-MM-DD"),
):
    """历史某日的涨跌额，用于日历统计投资收入。"""
    if kind not in ("index", "stock") or not re.fullmatch(r"\d{6}", symbol) \
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise HTTPException(status_code=400, detail="参数格式不正确")
    loop = asyncio.get_event_loop()
    try:
        quote = await loop.run_in_executor(None, _fetch_daily_quote, kind, symbol, date)
    except Exception:
        quote = None
    if not quote:
        return {"kind": kind, "symbol": symbol, "date": date, "found": False}
    quote.update({"kind": kind, "symbol": symbol, "found": True})
    return quote


@app.get("/api/day-holdings")
async def get_day_holdings(request: Request, date: str = Query(...)):
    """读取某一天的持仓：优先当天快照，否则沿用之前最近一次设置。

    返回的 ``source``：
    * ``exact``：当天专门保存过；
    * ``inherited``：沿用 snapshot_date 那天的设置；
    * ``none``：之前没有任何投资记录（投资收入按 0 计算）。
    """
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise HTTPException(status_code=400, detail="date 格式应为 YYYY-MM-DD")
    row = await request.app.state.store.get_day_holdings_on_or_before(date)
    if not row:
        return {"date": date, "snapshot_date": None, "source": "none", "items": []}
    try:
        items = json.loads(row["items"])
    except ValueError:
        items = []
    source = "exact" if row["date"] == date else "inherited"
    return {
        "date": date,
        "snapshot_date": row["date"],
        "source": source,
        "items": items,
    }


@app.put("/api/day-holdings")
async def put_day_holdings(request: Request, payload: DayHoldingsIn):
    """保存某日持仓快照；空列表表示从该日起不再投资。

    仅保存日历页的历史记录，不影响行情页（首页）的标的设置。
    """
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", payload.date):
        raise HTTPException(status_code=400, detail="date 格式应为 YYYY-MM-DD")
    items = _validate_symbols(payload.items)
    await request.app.state.store.save_day_holdings(
        payload.date, json.dumps(items, ensure_ascii=False)
    )
    return {"date": payload.date, "items": items}


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
