# -*- coding:utf-8 -*-
__author__ = 'linxinloningg'

"""
实时行情爬虫（单只 / 多只股票）
================================

数据来源
--------
东方财富 push2 实时行情接口::

    http://push2.eastmoney.com/api/qt/stock/get

该接口返回的是 JSONP 文本（外面包了一层回调函数），形如::

    jQuery1124....({"rc":0,"rt":4,...,"data":{"f43":16.58,"f57":"600026",...}});

其中 ``f43``、``f57`` 等是东方财富内部字段编号，本模块会把它们翻译成中文键名，
例如 ``f43 -> 最新价``，最终返回::

    {'600026': {'最新价': 16.58}}

市场（secid）规则
-----------------
东方财富用 ``市场.代码`` 定位标的：

* ``6`` 开头（沪市股票）      -> ``1.600026``
* ``5`` 开头（沪市 ETF/LOF）  -> ``1.518880``（如黄金ETF）
* ``0`` / ``3`` 开头（深市股票）-> ``0.000001`` / ``0.300059``
* ``1`` 开头（深市 ETF/LOF）  -> ``0.159941``（如纳指ETF）

使用的接口
----------
默认走批量接口 ``api/qt/ulist.np/get``：一次请求即可拿到多只股票 / ETF 的行情，
返回结构是 ``data.diff`` 列表。若批量接口不可用，再退回逐只查询
``api/qt/stock/get``（见 ``get_url`` / ``parse_url``）。

本模块不负责指数；指数行情见 :mod:`spider.index_data_spider`。
"""

from datetime import datetime
from json import loads

from requests import get, ConnectionError

# 兼容两种运行方式：
#   1. 作为包导入：python -m spider.real_data_spider
#   2. 直接运行脚本：python spider/real_data_spider.py
try:
    from .logger import log
except ImportError:
    from logger import log


class RealDataSpider:
    """
    爬取单个或者多个股票的实时行情快照。

    该类只做「请求 + 解析」，不做定时轮询，调用方可以按需循环调用 ``run``。
    """

    def __init__(self):
        # 东方财富单只标的行情接口（批量接口不可用时的回退方案）
        self.GS_API = 'http://push2.eastmoney.com/api/qt/stock/get'

        # 批量行情接口：一次请求可同时查询多只股票 / ETF
        self.ULIST_API = 'http://push2.eastmoney.com/api/qt/ulist.np/get'

        # 伪装成浏览器，避免被服务端直接拒绝
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 6.3; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/93.0.4577.82 Safari/537.36"}

        # 字段编号 -> 中文名称 的映射表。
        # 解析响应时用它把 f43、f44 这类编号翻译成可读的中文列名。
        self.datatype = {'f43': '最新价', 'f44': '最高', 'f45': '最低', 'f46': '今开', 'f47': '成交量', 'f48': '成交额',
                         'f50': '量比',
                         'f51': '涨停', 'f52': '跌停', 'f55': '收益', 'f57': '代码', 'f58': '名称', 'f60': '昨收', 'f71': '均价',
                         'f92': '美股净资产', 'f105': '净利润', 'f116': '总市值', 'f117': '流通市值', 'f162': '市盈动',
                         'f167': '市净', 'f168': '换手', 'f169': '涨跌', 'f170': '涨幅', 'f173': 'ROE', 'f183': '总营收',
                         'f186': '毛利率', 'f187': '净利率',
                         'f188': '负债率', 'f191': '委比', 'f192': '委差', 'f193': '主力净比', 'f194': '超大单净比',
                         'f195': '大单净比',
                         'f196': '中单净比', 'f197': '小单净比', }

        # 默认请求的字段集合（与 datatype 的键保持一致）
        self.parameters = 'f43,f44,f45,f46,f47,f48,f50,f51,f52,f55,f57,f58,f60,f71,f92,f105,f116,f117,f162,f167,f168,' \
                          'f169,f170,f173,f183,f186,f187,f188,f191,f192,f193,f194,f195,f196,f197 '

        # 批量接口的字段编号 -> 中文名称。批量接口能拿到的字段比单只接口少，
        # 但价格、涨跌、成交量、市值等实时看板需要的字段都已覆盖。
        self.ulist_datatype = {'f2': '最新价', 'f3': '涨幅', 'f4': '涨跌', 'f5': '成交量', 'f6': '成交额',
                               'f8': '换手', 'f10': '量比', 'f12': '代码', 'f14': '名称', 'f15': '最高',
                               'f16': '最低', 'f17': '今开', 'f18': '昨收', 'f20': '总市值', 'f21': '流通市值'}
        self.ulist_parameters = ','.join(self.ulist_datatype)

    @staticmethod
    def get_secid(code):
        """
        把 6 位代码翻译成东方财富的 ``市场.代码``（secid）。

        ======  ==================  ==============
        开头    类型                 secid
        ======  ==================  ==============
        6       沪市股票            1.600026
        5       沪市 ETF / LOF      1.518880
        0 / 3   深市股票            0.000001 / 0.300059
        1       深市 ETF / LOF      0.159941
        ======  ==================  ==============

        :param code: 6 位代码
        :return: secid 字符串；不支持的开头（如 4/8 开头北交所）返回 ``None``
        """
        first = code[:1]
        # 沪市：6 开头股票；5 开头基金（ETF/LOF）
        if first in ("6", "5"):
            return "1.%s" % code
        # 深市：0/3 开头股票；1 开头基金（ETF/LOF）
        if first in ("0", "3", "1"):
            return "0.%s" % code
        return None

    def get_url(self, codes, parameters):
        """
        为每个股票代码构造单只查询的请求参数。

        :param codes: 股票代码列表，例如 ``['600026', '000001']``
        :param parameters: 字段编号字符串，如 ``'f43'`` 或 ``'f43,f57,f58'``；
                           传 ``'default'`` 表示沿用 ``self.parameters``
        :return: ``{代码: requests 的 params 字典}``，仅包含 0/1/3/5/6 开头的合法代码
        """
        # 调用方显式传入了字段，则覆盖默认字段集合
        if parameters != 'default':
            self.parameters = parameters

        params_dict = {}

        for code in codes:
            secid = self.get_secid(code)
            # 其它开头（如 8/4 开头的北交所代码）不支持，直接忽略
            if secid is None:
                continue

            params_dict[code] = {
                'ut': 'fa5fd1943c7b386f172d6893dbfba10b',   # 东财固定的公开 ut 参数
                'invt': '2',
                'fltt': '2',                                 # 返回价格保留 2 位小数
                'fields': self.parameters,                   # 需要返回的字段
                'secid': secid,                              # 1.600026 / 1.518880 / 0.159941
                'cb': 'jQuery112405831440079032297_1578892365285',  # JSONP 回调名
                '_': '1578892365407'                         # 时间戳，防止缓存
            }

        return params_dict

    def run_ulist(self, codes):
        """
        通过批量接口一次请求多只标的的行情。

        :param codes: 股票 / ETF 代码列表
        :return: ``{代码: {中文指标名: 值}}``；请求或解析失败时返回空字典
        """
        secids = [secid for secid in (self.get_secid(code) for code in codes) if secid]
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
        """
        发起 HTTP 请求，返回原始 JSONP 文本。

        :param params: ``get_url`` 生成的 requests 参数字典
        :return: 响应文本（失败时返回 ``None``）
        """
        try:
            response = get(self.GS_API, params=params, headers=self.headers, timeout=5)
            log.info("爬取：" + str(response.url))
            if response.status_code == 200:
                return response.text
        except ConnectionError as e:
            # 网络异常时只记录日志，由上层决定如何处理 None
            log.info('Error: %s', e)

    def get_stockdata(self, code, content):
        """
        解析单只股票的 JSONP 响应。

        :param code: 股票代码，用于组装返回字典的 key
        :param content: ``parse_url`` 返回的 JSONP 文本
        :return: ``{code: {中文指标名: 值}}``；解析失败时对应字段会被跳过
        """
        stockdata = {code: {}}

        # JSONP 形如 jQuery...({"data": ...});，先剥掉回调名、括号和分号，得到纯 JSON
        content = content.replace("jQuery112405831440079032297_1578892365285", '').replace('(', "").replace(')',
                                                                                                            '').replace(
            ';', '')

        # loads 成功即说明是合法 JSON，逐字段取值
        if loads(content) is not None:
            for parameter in self.parameters.split(','):
                try:
                    data = loads(content)['data'][parameter]
                    # 字段编号翻译成中文名；data 为 None 时也照常写入（表示停牌/无数据）
                    stockdata[code].setdefault(self.datatype[parameter], data)
                except Exception as e:
                    # 某个字段不存在不影响其它字段，记录后继续
                    log.info(e)
                    continue
            return stockdata

    def save_stockdata(self, stockdata):
        """
        预留的数据整理/落盘接口。

        当前实现只做字段遍历，没有真正写文件；返回值仍是原始字典，
        保留该方法是为了兼容早期调用方。

        :param stockdata: ``get_stockdata`` 的返回值
        :return: 原样返回 ``stockdata``
        """
        for code, datadict in stockdata.items():
            for parameter, data in datadict.items():
                for datatype, zhname in self.datatype.items():
                    if parameter == datatype:
                        parameter = zhname
                        # stockdata[code][zhname] = stockdata[code].pop(parameter)
                # 注意：这里只是一个字符串表达式，并没有输出或写入文件
                "[%s] [%s] [%s]: %s。" % (code, datetime.now(), parameter, data)
        return stockdata

    def run(self, codes, parameters='default'):
        """
        爬取入口：批量请求并解析多只股票 / ETF 的实时行情。

        :param codes: 股票 / ETF 代码列表，如 ``['600031', '300059', '518880']``
        :param parameters: 字段编号字符串或 ``'default'``；
                           传 ``'default'`` 时优先使用批量接口
        :return: ``{'600031': {'最新价': 23.17}, '518880': {'最新价': 7.396}}``；
                 整体异常时返回 ``None``
        """
        try:
            # 默认字段：优先走批量接口，一次请求即可拿到全部标的
            if parameters == 'default':
                data = self.run_ulist(codes)
                if data:
                    return data

            # 自定义字段或批量接口失败时，退回逐只查询
            data = dict()
            gpurl_dict = self.get_url(codes, parameters=parameters)

            # 逐个标的请求。此处是串行请求，代码数量多时建议调用方自行控制频率
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
    # 自测：沪市股票 / 深市股票 / 黄金ETF / 纳指ETF
    spider = RealDataSpider()
    result = spider.run(['600026', '000001', '518880', '159941'])
    for code, values in (result or {}).items():
        print(code, values.get('名称'), values.get('最新价'), values.get('涨幅'))
