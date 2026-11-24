# -*- coding:utf-8 -*-
"""定时抓取调度。

每 ``POLL_INTERVAL`` 秒（默认 3 秒）从数据库读取当前标的配置并抓取：

1. 股票 / ETF：``RealDataSpider``，支持 0/1/3/5/6 开头的 A 股与 ETF 代码；
2. 指数：``IndexDataSpider``，仅支持 config 中列出的指数。

只在 A 股交易时段抓取（``TRADING_WINDOWS``，周末视为非交易）；
非交易时段按 ``CLOSED_POLL_INTERVAL`` 低频检查时间，不请求行情接口。

标的列表每轮重新读取，因此页面上的修改最多 3 秒后生效。
抓取在线程池中执行，避免 requests 阻塞事件循环；结果写入数据库后
通过 WebSocket 广播给所有前端。
"""

import asyncio
from datetime import datetime, time as clock_time
from time import time
from typing import Any, Dict, List, Optional

from spider.index_data_spider import IndexDataSpider
from spider.logger import log
from spider.real_data_spider import RealDataSpider

from .config import CLOSED_POLL_INTERVAL, POLL_INTERVAL, SUPPORTED_INDEX, TRADING_WINDOWS
from .hub import ConnectionHub
from .store import TickStore


def _parse_clock(value: str) -> clock_time:
    hour, minute = value.split(":")
    return clock_time(int(hour), int(minute))


# 启动时把 "HH:MM" 解析成 time 对象，避免每轮重复解析
TRADING_RANGES = tuple((_parse_clock(start), _parse_clock(end)) for start, end in TRADING_WINDOWS)


def in_trading_hours(now: Optional[datetime] = None) -> bool:
    """判断当前是否处于交易时段；周末一律视为非交易。"""
    current = now or datetime.now()
    if current.weekday() >= 5:
        return False
    current_time = current.time()
    return any(start <= current_time <= end for start, end in TRADING_RANGES)


def _to_float(value: Any) -> Optional[float]:
    """把爬虫返回的字段安全转成 float（None、'-'、空串都返回 None）。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_tick(item: Dict[str, Any], raw: Optional[Dict[str, Any]], ts: int) -> Optional[Dict[str, Any]]:
    """把爬虫的原始字段整理成统一的 tick 结构。"""
    data = raw or {}
    price = _to_float(data.get("最新价"))
    if price is None:
        return None

    prev_close = _to_float(data.get("昨收"))
    change = _to_float(data.get("涨跌"))
    change_pct = _to_float(data.get("涨幅"))

    # 兜底：接口没给涨跌/涨幅时，用昨收自行计算
    if change is None and prev_close:
        change = round(price - prev_close, 3)
    if change_pct is None and prev_close:
        change_pct = round((price / prev_close - 1) * 100, 2)

    # 名称优先级：用户配置的名称 > 接口返回的名称 > 代码
    configured_name = (item.get("name") or "").strip()
    if configured_name and configured_name != item["symbol"]:
        name = configured_name
    else:
        name = data.get("名称") or configured_name or item["symbol"]

    return {
        "type": "tick",
        "id": "%s:%s" % (item["kind"], item["symbol"]),
        "symbol": item["symbol"],
        "kind": item["kind"],
        "name": name,
        "price": price,
        "prev_close": prev_close,
        "change": change,
        "change_pct": change_pct,
        "ts": ts,
    }


class TickScheduler:
    """单实例轮询器，应用启动时作为后台任务运行。"""

    def __init__(self, store: TickStore, hub: ConnectionHub) -> None:
        self.store = store
        self.hub = hub
        self.stock_spider = RealDataSpider()
        self.index_spider = IndexDataSpider()

    def _fetch_once(self, symbols: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """按当前标的配置同步抓取一轮，返回所有成功解析的 tick。"""
        ts = int(time() * 1000)
        stock_items = [item for item in symbols if item["kind"] == "stock"]
        index_items = []
        for item in symbols:
            if item["kind"] != "index":
                continue
            if item["symbol"] in SUPPORTED_INDEX:
                index_items.append(item)
            else:
                log.warning("跳过不支持的指数 %s（可选：%s）", item["symbol"], SUPPORTED_INDEX)

        ticks: List[Dict[str, Any]] = []

        # 股票：一次请求可以带多个代码
        if stock_items:
            codes = [item["symbol"] for item in stock_items]
            try:
                data = self.stock_spider.run(codes, "default") or {}
            except Exception as e:  # 网络异常不影响指数抓取
                log.warning("股票行情抓取失败: %s", e)
                data = {}
            for item in stock_items:
                tick = _build_tick(item, data.get(item["symbol"]), ts)
                if tick:
                    ticks.append(tick)

        # 指数：修改爬虫的 codes 后复用同一个爬虫实例
        if index_items:
            self.index_spider.codes = [item["symbol"] for item in index_items]
            try:
                data = self.index_spider.run("default") or {}
            except Exception as e:
                log.warning("指数行情抓取失败: %s", e)
                data = {}
            for item in index_items:
                tick = _build_tick(item, data.get(item["symbol"]), ts)
                if tick:
                    ticks.append(tick)

        return ticks

    async def run(self) -> None:
        """后台循环：非交易时段休眠；交易时段 读配置 -> 抓取 -> 落库 -> 广播。"""
        loop = asyncio.get_running_loop()
        was_trading = None

        while True:
            trading = in_trading_hours()
            if trading != was_trading:
                log.info("进入交易时段，开始抓取" if trading else "非交易时段，暂停抓取")
                was_trading = trading

            # 非交易时段不发请求，低频检查时间即可
            if not trading:
                await asyncio.sleep(CLOSED_POLL_INTERVAL)
                continue

            started = time()
            try:
                # 每轮重新读取，支持页面随时修改标的
                symbols = await self.store.get_symbols()
                # requests 是阻塞调用，放到线程池执行
                ticks = await loop.run_in_executor(None, self._fetch_once, symbols)
                for tick in ticks:
                    await self.store.push_tick(tick)
                    await self.hub.broadcast(tick)
                if ticks:
                    log.info("本轮抓取 %d 条，第一条: %s %s", len(ticks), ticks[0]["id"], ticks[0]["price"])
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("轮询异常: %s", e)

            # 扣掉抓取耗时，尽量保持 3 秒的稳定节拍
            elapsed = time() - started
            await asyncio.sleep(max(0.2, POLL_INTERVAL - elapsed))
