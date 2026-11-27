# -*- coding:utf-8 -*-
"""全局配置。

``SYMBOLS`` 只是首次启动时写入数据库的默认标的；页面右上角「标的设置」
修改后以数据库中的数据为准，重启不会覆盖。

* ``kind = "stock"``：任意 A 股或 ETF 代码（0/1/3/5/6 开头），由 RealDataSpider 抓取；
* ``kind = "index"``：仅支持 399001 / 000001 / 399006 / 000300，
  因为 IndexDataSpider 内置了这几个指数的市场编号。
"""

import os
from pathlib import Path

# 项目根目录（stock-spider/）
BASE_DIR = Path(__file__).resolve().parent.parent

SYMBOLS = [
    {"symbol": "000001", "kind": "index", "name": "上证指数"},
    {"symbol": "600026", "kind": "stock", "name": "中远海能"},
]

# 支持的指数代码及默认名称
INDEX_NAMES = {
    "399001": "深证成指",
    "000001": "上证指数",
    "399006": "创业板指",
    "000300": "沪深300",
}

# 抓取间隔（秒）
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "3"))

# A 股交易时段（本地时区）：非交易时段不请求行情接口。
# 收盘后（15:00 之后）行情接口返回的是静止的收盘价，继续抓取会写入大量
# 重复的“直线”数据，因此严格按 15:00 收盘结束。
TRADING_WINDOWS = (("09:30", "11:30"), ("13:00", "15:00"))


def _clock_minutes(text: str) -> int:
    hour, minute = text.split(":")
    return int(hour) * 60 + int(minute)


# 交易时段的分钟表示，供历史数据过滤等逻辑复用
TRADING_RANGES = tuple((_clock_minutes(start), _clock_minutes(end))
                       for start, end in TRADING_WINDOWS)

# 非交易时段的检查间隔（秒）：只检查时间，不发请求
CLOSED_POLL_INTERVAL = float(os.getenv("CLOSED_POLL_INTERVAL", "30"))

# 数据库中每只标的最多保留的点数（3 秒一个点约覆盖 5 小时，够一整天交易）
# 前端通过 /api/history 的 step 参数抽样展示全天走势
MAX_POINTS = int(os.getenv("MAX_POINTS", "6000"))

# SQLite 数据库文件位置（默认 data/stock.db，可用环境变量覆盖）
DB_PATH = Path(os.getenv("DB_PATH", str(BASE_DIR / "data" / "stock.db")))

# IndexDataSpider 支持的指数
SUPPORTED_INDEX = ("399001", "000001", "399006", "000300")
