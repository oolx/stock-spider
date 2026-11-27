# -*- coding:utf-8 -*-
__author__ = 'linxinloningg'

"""
主要指数实时行情爬虫
====================

与 :mod:`spider.real_data_spider` 使用同一个东方财富接口::

    http://push2.eastmoney.com/api/qt/stock/get

区别在于：

* 股票代码固定为几个常见指数（见 ``self.codes``）；
* 每个指数的市场编号是写死的（见 ``get_url``），因为指数代码与股票代码的
  首位数字规则并不一致，不能按 6/0/3 自动判断。

默认抓取的指数
--------------
=========  ==========  ==========
代码        名称        市场编号
=========  ==========  ==========
399001     深证成指    0
000001     上证指数    1
399006     创业板指    0
000300     沪深300     1
=========  ==========  ==========

与股票爬虫一样，默认优先使用批量接口 ``api/qt/ulist.np/get``，
失败时再退回逐只查询 ``api/qt/stock/get``。
"""

from datetime import datetime
from json import loads

from requests import get, ConnectionError

# 兼容「作为包导入」与「直接运行脚本」两种方式
try:
    from .logger import log
except ImportError:
    from logger import log


class IndexDataSpider:
    """
    爬取主要指数的实时行情快照。

    返回结构与 :class:`spider.real_data_spider.RealDataSpider` 一致：
    ``{指数代码: {中文指标名: 值}}``。
    """

    def __init__(self):
        # 东方财富单只标的行情接口（指数与股票共用，批量接口不可用时回退）
        self.GS_API = 'http://push2.eastmoney.com/api/qt/stock/get'

        # 批量行情接口：一次请求可同时查询多个指数
        self.ULIST_API = 'http://push2.eastmoney.com/api/qt/ulist.np/get'

        # 伪装浏览器
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 6.3; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/93.0.4577.82 Safari/537.36"}

        # 字段编号 -> 中文名称
        self.datatype = {'f43': '最新价', 'f44': '最高', 'f45': '最低', 'f46': '今开', 'f47': '成交量', 'f48': '成交额',
                         'f50': '量比',
                         'f51': '涨停', 'f52': '跌停', 'f55': '收益', 'f57': '代码', 'f58': '名称', 'f60': '昨收', 'f71': '均价',
                         'f92': '美股净资产', 'f105': '净利润', 'f116': '总市值', 'f117': '流通市值', 'f162': '市盈动',
                         'f167': '市净', 'f168': '换手', 'f169': '涨跌', 'f170': '涨幅', 'f173': 'ROE', 'f183': '总营收',
                         'f186': '毛利率', 'f187': '净利率',
                         'f188': '负债率', 'f191': '委比', 'f192': '委差', 'f193': '主力净比', 'f194': '超大单净比',
                         'f195': '大单净比',
                         'f196': '中单净比', 'f197': '小单净比', }

        # 需要抓取的指数代码，可按需增删
        self.codes = ['399001', '000001', '399006', '000300']

        # 指数代码 -> secid。指数不能用首位数字自动判断市场，这里显式写死：
        # 上证指数 000001 属于沪市（1），深证成指/创业板指属于深市（0），
        # 沪深300 在东财归在沪市（1）。
        self.INDEX_SECID = {
            '399001': '0.399001',
            '000001': '1.000001',
            '399006': '0.399006',
            '000300': '1.000300',
        }

        # 批量接口字段编号 -> 中文名称
        self.ulist_datatype = {'f2': '最新价', 'f3': '涨幅', 'f4': '涨跌', 'f5': '成交量', 'f6': '成交额',
                               'f8': '换手', 'f10': '量比', 'f12': '代码', 'f14': '名称', 'f15': '最高',
                               'f16': '最低', 'f17': '今开', 'f18': '昨收', 'f20': '总市值', 'f21': '流通市值'}
        self.ulist_parameters = ','.join(self.ulist_datatype)

        # 默认请求字段
        self.parameters = 'f43,f44,f45,f46,f47,f48,f50,f51,f52,f55,f57,f58,f60,f71,f92,f105,f116,f117,f162,f167,f168,' \
                          'f169,f170,f173,f183,f186,f187,f188,f191,f192,f193,f194,f195,f196,f197 '

    def get_url(self, parameters):
        """
        为每个指数构造单只查询的请求参数。

        :param parameters: 字段编号字符串，``'default'`` 表示沿用默认字段
        :return: ``{指数代码: requests 参数字典}``
        """
        if parameters != 'default':
            self.parameters = parameters

        params_dict = {}

        for code in self.codes:
            secid = self.INDEX_SECID.get(code)
            # 未登记 market 映射的指数直接跳过
            if secid is None:
                continue

            params_dict[code] = {
                'ut': 'fa5fd1943c7b386f172d6893dbfba10b',
                'invt': '2',
                'fltt': '2',
                'fields': self.parameters,
                'secid': secid,
                'cb': 'jQuery112405831440079032297_1578892365285',
                '_': '1578892365407'
            }

        return params_dict

    def run_ulist(self):
        """
        通过批量接口一次请求所有已登记指数的行情。

        :return: ``{指数代码: {中文指标名: 值}}``；请求或解析失败时返回空字典
        """
        secids = [self.INDEX_SECID[code] for code in self.codes if code in self.INDEX_SECID]
        if not secids:
            return {}

        params = {
            'ut': 'fa5fd1943c7b386f172d6893dbfba10b',
            'fltt': '2',
            'invt': '2',
            'fields': self.ulist_parameters,
            'secids': ','.join(secids),
        }

        response = get(self.ULIST_API, params=params, headers=self.headers, timeout=5)
        log.info("爬取：" + str(response.url))
        if response.status_code != 200:
            return {}

        diff = (response.json().get('data') or {}).get('diff') or []

        data = {}
        for row in diff:
            code = str(row.get('f12') or '')
            if not code:
                continue
            data[code] = {zh: row[key] for key, zh in self.ulist_datatype.items() if key in row}

        return data

    def parse_url(self, params):
        """发起请求并返回 JSONP 原始文本（失败返回 None）"""
        try:
            response = get(self.GS_API, params=params, headers=self.headers, timeout=5)
            log.info("爬取：" + str(response.url))
            if response.status_code == 200:
                return response.text
        except ConnectionError as e:
            log.info('Error: %s', e)

    def get_stockdata(self, code, content):
        """
        解析单个指数的响应。

        :param code: 指数代码
        :param content: JSONP 文本
        :return: ``{code: {中文指标名: 值}}``
        """
        stockdata = {code: {}}

        # 去掉 JSONP 外壳，得到纯 JSON
        content = content.replace("jQuery112405831440079032297_1578892365285", '').replace('(', "").replace(')',
                                                                                                            '').replace(
            ';', '')

        if loads(content) is not None:
            for parameter in self.parameters.split(','):
                try:
                    data = loads(content)['data'][parameter]
                    stockdata[code].setdefault(self.datatype[parameter], data)
                except Exception as e:
                    # 指数没有某些个股字段（如净利润、毛利率），缺失属正常情况
                    log.info(e)
                    continue
            return stockdata

    def save_stockdata(self, stockdata):
        """
        预留的数据整理接口，当前原样返回。

        :param stockdata: ``get_stockdata`` 的返回值
        :return: 原样返回
        """
        for code, datadict in stockdata.items():
            for parameter, data in datadict.items():
                for datatype, zhname in self.datatype.items():
                    if parameter == datatype:
                        parameter = zhname
                        # stockdata[code][zhname] = stockdata[code].pop(parameter)
                # 仅字符串表达式，无实际输出
                "[%s] [%s] [%s]: %s。" % (code, datetime.now(), parameter, data)
        return stockdata

    def run(self, parameters='default'):
        """
        抓取入口。

        :param parameters: 字段编号字符串或 ``'default'``；
                           传 ``'default'`` 时优先使用批量接口
        :return: ``{'399001': {'最新价': ...}, '000001': {'最新价': ...}, ...}``；
                 异常时返回 None
        """
        try:
            # 默认字段：优先走批量接口
            if parameters == 'default':
                data = self.run_ulist()
                if data:
                    return data

            # 自定义字段或批量接口失败时，退回逐只查询
            data = dict()
            gpurl_dict = self.get_url(parameters=parameters)
            for code, url in gpurl_dict.items():
                content = self.parse_url(url)
                if content is None:
                    continue
                stockdata = self.get_stockdata(code, content)
                if stockdata is not None:
                    data.update(stockdata)

            return data
        except Exception as e:
            log.info(e)
            return None


if __name__ == '__main__':
    # 自测：抓取四个默认指数的全部默认字段
    spider = IndexDataSpider()
    for index_code, values in (spider.run() or {}).items():
        print(index_code, values.get('名称'), values.get('最新价'), values.get('涨幅'))
