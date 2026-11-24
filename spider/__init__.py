# -*- coding:utf-8 -*-
__author__ = 'linxinloningg'

"""
股票行情爬虫包
==============

按职责拆分为三个独立模块：

* :mod:`spider.real_data_spider`      —— ``RealDataSpider``   实时行情（单只/多只股票）
* :mod:`spider.dailymarket_spider`    —— ``DailymarketSpider`` 全市场当日行情
* :mod:`spider.index_data_spider`     —— ``IndexDataSpider``   主要指数实时行情

这里统一再导出一次，保持 ``from spider import RealDataSpider`` 的老用法可用。
"""

from .dailymarket_spider import DailymarketSpider
from .index_data_spider import IndexDataSpider
from .real_data_spider import RealDataSpider

__all__ = ['RealDataSpider', 'DailymarketSpider', 'IndexDataSpider']
