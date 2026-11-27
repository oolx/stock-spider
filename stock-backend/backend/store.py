# -*- coding:utf-8 -*-
"""SQLite 存取封装。

标的统一使用 ``id = kind:symbol`` 作为唯一键，例如 ``index:000001``、
``stock:000001``。这样「上证指数 000001」和「平安银行 000001」不会冲突。

数据表：

* ``symbols``：标的配置，``id`` 为主键；
* ``ticks``：行情点，按自增 ``id`` 递增，``payload`` 保存完整 tick JSON；
* ``settings``：键值配置（秒薪参数、年度节假日缓存等）；
* ``day_holdings``：按日期保存的持仓快照，用于日历页统计当日投资收入。

行情点每个标的只保留最近 ``MAX_POINTS`` 条，写入后即裁剪。
SQLite 为 Python 标准库自带，无需额外安装任何数据库服务，便于 Windows 部署。
"""

import asyncio
import functools
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from .config import DB_PATH, MAX_POINTS, TRADING_RANGES

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS symbols (
        id TEXT PRIMARY KEY,
        symbol TEXT NOT NULL,
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        cost REAL NOT NULL DEFAULT 0,
        shares REAL NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ticks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol_id TEXT NOT NULL,
        day TEXT,
        payload TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS day_holdings (
        date TEXT PRIMARY KEY,
        items TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_ticks_symbol ON ticks(symbol_id, id)",
)


def _symbol_id(item: Dict[str, Any]) -> str:
    return "%s:%s" % (item["kind"], item["symbol"])


def _normalize(item: Dict[str, Any]) -> Dict[str, Any]:
    """补齐 id / name / 持仓字段，保证读出来的结构完整。"""
    data = dict(item)
    data["id"] = _symbol_id(data)
    data.setdefault("name", data["symbol"])
    # 老数据没有持仓字段，读取时统一补 0，避免前端出现 undefined
    data["cost"] = round(float(data.get("cost") or 0), 4)
    data["shares"] = round(float(data.get("shares") or 0), 4)
    return data


def _tick_day(ts: Any) -> str:
    """tick 时间戳（毫秒）对应的本地日期，格式 YYYY-MM-DD。"""
    try:
        return datetime.fromtimestamp(float(ts) / 1000).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return ""


def _in_trading_time(ts: Any) -> bool:
    """判断 tick 是否在交易时段内，用于剔除收盘后重复的静止行情点。"""
    try:
        moment = datetime.fromtimestamp(float(ts) / 1000)
    except (TypeError, ValueError):
        return False
    minutes = moment.hour * 60 + moment.minute
    return any(start <= minutes <= end for start, end in TRADING_RANGES)


class TickStore:
    """行情数据的 SQLite 读写层。

    每次操作使用独立连接（SQLite 连接开销很小），并通过线程池执行，
    避免阻塞事件循环。所有方法都是异步的，调用方式与旧版保持一致。
    """

    def __init__(self, path: Path = DB_PATH):
        self._path = Path(path)

    async def init(self) -> None:
        """建目录、建表、建索引；重复调用安全。"""
        await self._run(self._init_sync)

    async def ping(self) -> bool:
        return await self._run(self._ping_sync)

    async def ensure_symbols(self, symbols: List[Dict[str, Any]]) -> None:
        """仅当没有标的时写入默认配置，避免重启覆盖用户修改。"""
        if await self._run(self._count_symbols_sync) == 0:
            await self.replace_symbols(symbols)

    async def replace_symbols(self, symbols: List[Dict[str, Any]]) -> None:
        """整体替换标的列表（新增、修改、删除一次完成）。"""
        normalized = [_normalize(item) for item in symbols]
        await self._run(self._replace_symbols_sync, normalized)

    async def get_symbols(self) -> List[Dict[str, Any]]:
        """读取全部标的，按 id 排序保证前端顺序稳定。"""
        rows = await self._run(self._get_symbols_sync)
        return [_normalize(row) for row in rows]

    async def push_tick(self, tick: Dict[str, Any]) -> None:
        """写入一个 tick；跨交易日时先清掉更早的行情，再裁剪到 MAX_POINTS。"""
        payload = json.dumps(tick, ensure_ascii=False)
        await self._run(self._push_tick_sync, tick["id"], payload, _tick_day(tick.get("ts")))

    async def get_history(self, symbol_id: str, limit: int, step: int = 1,
                          day: str = "") -> List[Dict[str, Any]]:
        """读取最近 limit 个点（step>1 时等间隔抽样），返回时间正序（旧 -> 新）。

        ``day``（YYYY-MM-DD）用于只返回某个交易日的行情，避免不同日期的点
        混进同一张走势图；过滤后仍按 ``step`` 抽样、``limit`` 截断。
        同时剔除收盘后（15:00 之后）重复的静止价格点。
        """
        limit = max(1, min(int(limit), MAX_POINTS))
        step = max(1, int(step))
        payloads = await self._run(self._get_history_sync, symbol_id)
        ticks = [json.loads(payload) for payload in payloads]
        if day:
            ticks = [tick for tick in ticks
                     if _tick_day(tick.get("ts")) == day and _in_trading_time(tick.get("ts"))]
        ticks = ticks[::step][:limit]
        # 查询按新 -> 旧，反转后便于前端直接追加
        ticks.reverse()
        return ticks

    async def close(self) -> None:
        """SQLite 每次操作独立连接，这里无需额外清理。"""
        return None

    async def get_setting(self, key: str):
        """读取一个配置值（JSON 字符串），不存在时返回 None。"""
        return await self._run(self._get_setting_sync, key)

    async def set_setting(self, key: str, value: str) -> None:
        """写入或覆盖一个配置值。"""
        await self._run(self._set_setting_sync, key, value)

    async def get_day_holdings_on_or_before(self, day: str):
        """返回某天或之前最近一条持仓快照（date, items），没有则 None。"""
        return await self._run(self._get_day_holdings_on_or_before_sync, day)

    async def save_day_holdings(self, day: str, items_json: str) -> None:
        """保存某一天的标的快照。"""
        await self._run(self._save_day_holdings_sync, day, items_json)

    async def list_day_holdings(self) -> List[Dict[str, Any]]:
        """按日期正序返回全部持仓快照，供月度统计一次读取。"""
        return await self._run(self._list_day_holdings_sync)

    async def _run(self, func, *args):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(func, *args))

    # ---------------- 以下为在线程中执行的同步实现 ----------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), timeout=10)
        conn.row_factory = sqlite3.Row
        # WAL 模式读写并发更好，写入更稳定
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_sync(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            for statement in _SCHEMA:
                conn.execute(statement)
            # 旧库升级：补充 day 列（交易日），并按 payload 里的 ts 回填
            columns = [row["name"] for row in conn.execute("PRAGMA table_info(ticks)")]
            if "day" not in columns:
                conn.execute("ALTER TABLE ticks ADD COLUMN day TEXT")
            conn.commit()
            missing = conn.execute(
                "SELECT id, payload FROM ticks WHERE day IS NULL").fetchall()
            if missing:
                updates = []
                for row in missing:
                    try:
                        day = _tick_day(json.loads(row["payload"]).get("ts"))
                    except ValueError:
                        day = ""
                    updates.append((day, row["id"]))
                with conn:
                    conn.executemany("UPDATE ticks SET day = ? WHERE id = ?", updates)
        finally:
            conn.close()

    def _ping_sync(self) -> bool:
        conn = self._connect()
        try:
            conn.execute("SELECT 1")
            return True
        finally:
            conn.close()

    def _count_symbols_sync(self) -> int:
        conn = self._connect()
        try:
            row = conn.execute("SELECT COUNT(*) AS n FROM symbols").fetchone()
            return int(row["n"])
        finally:
            conn.close()

    def _replace_symbols_sync(self, symbols: List[Dict[str, Any]]) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.execute("DELETE FROM symbols")
                conn.executemany(
                    "INSERT INTO symbols (id, symbol, kind, name, cost, shares) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (
                            item["id"],
                            item["symbol"],
                            item["kind"],
                            item["name"],
                            item["cost"],
                            item["shares"],
                        )
                        for item in symbols
                    ],
                )
        finally:
            conn.close()

    def _get_symbols_sync(self) -> List[Dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, symbol, kind, name, cost, shares FROM symbols ORDER BY id"
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def _push_tick_sync(self, symbol_id: str, payload: str, day: str) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO ticks (symbol_id, day, payload) VALUES (?, ?, ?)",
                    (symbol_id, day, payload),
                )
                # 每天开盘初始化：新交易日不再保留更早的行情
                conn.execute(
                    "DELETE FROM ticks WHERE symbol_id = ? AND (day IS NULL OR day < ?)",
                    (symbol_id, day),
                )
                # 只保留最近 MAX_POINTS 条
                conn.execute(
                    "DELETE FROM ticks WHERE symbol_id = ? AND id NOT IN ("
                    "SELECT id FROM ticks WHERE symbol_id = ? ORDER BY id DESC LIMIT ?)",
                    (symbol_id, symbol_id, MAX_POINTS),
                )
        finally:
            conn.close()

    def _get_history_sync(self, symbol_id: str) -> List[str]:
        """取出该标的保留的全部行情（新 -> 旧），过滤与抽样在调用方完成。"""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT payload FROM ticks WHERE symbol_id = ? ORDER BY id DESC LIMIT ?",
                (symbol_id, MAX_POINTS),
            ).fetchall()
            return [row["payload"] for row in rows]
        finally:
            conn.close()

    def _get_setting_sync(self, key: str):
        conn = self._connect()
        try:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else None
        finally:
            conn.close()

    def _set_setting_sync(self, key: str, value: str) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    (key, value),
                )
        finally:
            conn.close()

    def _get_day_holdings_on_or_before_sync(self, day: str):
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT date, items FROM day_holdings WHERE date <= ? "
                "ORDER BY date DESC LIMIT 1",
                (day,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def _save_day_holdings_sync(self, day: str, items_json: str) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO day_holdings (date, items, updated_at) "
                    "VALUES (?, ?, ?)",
                    (day, items_json, datetime.now().isoformat(timespec="seconds")),
                )
        finally:
            conn.close()

    def _list_day_holdings_sync(self) -> List[Dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT date, items FROM day_holdings ORDER BY date ASC"
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()
