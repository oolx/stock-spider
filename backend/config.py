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
# 注意：标准收盘时间为 15:00；这里按需求写到 15:30，
# 如需严格按收盘结束，把第二段改成 ("13:00", "15:00") 即可。
TRADING_WINDOWS = (("09:30", "11:30"), ("13:00", "15:30"))

# 非交易时段的检查间隔（秒）：只检查时间，不发请求
CLOSED_POLL_INTERVAL = float(os.getenv("CLOSED_POLL_INTERVAL", "30"))

# 数据库中每只标的最多保留的点数，同时也是前端环形缓冲的上限
MAX_POINTS = int(os.getenv("MAX_POINTS", "500"))

# SQLite 数据库文件位置（默认 data/stock.db，可用环境变量覆盖）
DB_PATH = Path(os.getenv("DB_PATH", str(BASE_DIR / "data" / "stock.db")))

# IndexDataSpider 支持的指数
SUPPORTED_INDEX = ("399001", "000001", "399006", "000300")
