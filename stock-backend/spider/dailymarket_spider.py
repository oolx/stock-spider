# -*- coding:utf-8 -*-
__author__ = 'linxinloningg'

"""
全市场（全部 A 股）当日行情爬虫
================================

数据来源
--------
东方财富列表接口::

    http://76.push2.eastmoney.com/api/qt/clist/get

接口特点
--------
1. 分页返回，每页固定 **20** 条（URL 中的 ``pz=20``）；
2. 第 1 页响应里包含 ``"total": 5000`` 这样的总条数字段；
3. 先请求第 1 页拿到总数，再按 ``total / 20`` 算出总页数，逐页抓取；
4. 返回的仍是 JSONP 文本，本模块用正则直接提取字段，不整体反序列化。

``fs`` 参数筛选的板块为::

    m:0+t:6   深市主板
    m:0+t:13  深市创业板？(东财内部板块编号)
    m:0+t:80  深市其它
    m:1+t:2   沪市主板
    m:1+t:23  沪市其它

最终输出一个包含「代码 / 最新价 / 涨跌幅 / 最高 / 最低 / 开盘 / 成交量 / 换手率 / 市值」
的 :class:`pandas.DataFrame`。
"""

from re import compile, S

from pandas import concat, DataFrame
from requests import get, ConnectionError

# 兼容「作为包导入」与「直接运行脚本」两种方式
try:
    from .logger import log
except ImportError:
    from logger import log


class DailymarketSpider:
    """
    爬取全部 A 股的当日行情列表。

    注意：全市场约 5000 只股票，每页 20 条，完整运行会产生 250+ 次 HTTP 请求，
    建议低频调用或在自测时只抓取前几页。
    """

    def __init__(self):
        # 分页 URL 模板，{} 用于填入页码 pn
        self.url = 'http://76.push2.eastmoney.com/api/qt/clist/get?cb=jQuery112408744624686429123_1578798932591&pn={} \
                   &pz=20&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281&fltt=2&invt=2&fid=f3&fs=m:0+t:6,m:0+t:13,m:' \
                   '0+t:80,m:1+t:2,m:1+t:23&fields=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,f20,f21,' \
                   'f23,f24,f25,f22,f11,f62,f128,f136,f115,f152&_=1586266306109'

        # 伪装浏览器
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 6.3; Win64; x64; rv:91.0) Gecko/20100101 Firefox/91.0"}

    def get_url(self):
        """
        先探测总条数，再构造所有分页 URL。

        :return: 分页 URL 列表（不含第 1 页「探测」请求本身之外的重复处理）
        """
        url_list = list()
        page_number = None

        # 先抓第 1 页，从响应里读 total
        content = self.parse_url(self.url.format(1))

        """re选择数据中的total"""
        # 匹配 "total":数字,  非贪婪到第一个逗号
        pattern = compile('"total":(?P<Total>.+?),.*?', S)
        result = pattern.finditer(content)

        for value in result:
            # 每页 20 条，向上取整得到总页数
            page_number = round(int(value.groups()[0]) / 20 + 1)

        """构建url列表"""
        # 注意：range(1, page_number) 不会包含最后一页，原实现如此，这里保持不变
        for pn in range(1, page_number):
            url_list.append(self.url.format(pn))

        return url_list

    def parse_url(self, url):
        """获取网页源码（失败返回 None）"""
        try:
            response = get(url, headers=self.headers)
            if response.status_code == 200:
                log.info("爬取：" + str(response.url))
                return response.text
        except ConnectionError as e:
            log.info('Error', e.args)

    @staticmethod
    def get_stockdata(content):
        """
        从一页 JSONP 文本中提取股票字段，逐条 yield。

        使用正则一次性捕获连续出现的多个字段；由于东财返回的字段顺序固定，
        这种写法比完整解析 JSON 更省内存，但对响应格式变化较敏感。

        :param content: 一页响应文本
        :return: 生成器，每次产出一条股票字典
        """
        com = compile(
            '"f2":(?P<Latestprice>.+?),.*?"f3":(?P<Fluctuationrange>.+?),.*?"f8":(?P<Turnoverrate>.+?),.*?"f10":('
            '?P<Volumeratio>.+?),.*?"f12":(?P<Code>.+?),.*?"f15":(?P<High>.+?),.*?"f16":(?P<Low>.+?),'
            '.*?"f17":(?P<Open>.+?),.*?"f20":(?P<Marketvalue>.+?),', S)
        ret = com.finditer(content)
        for i in ret:
            yield {
                'Code': i.group('Code'),                       # 股票代码 f12
                'Turnoverrate': i.group('Turnoverrate'),       # 换手率 f8
                'Latestprice': i.group('Latestprice'),         # 最新价 f2
                'Fluctuationrange': i.group('Fluctuationrange'),  # 涨跌幅 f3
                'High': i.group('High'),                       # 最高 f15
                'Low': i.group('Low'),                         # 最低 f16
                'Open': i.group('Open'),                       # 今开 f17
                'Volumeratio': i.group('Volumeratio'),         # 量比 f10
                'Marketvalue': i.group('Marketvalue')          # 总市值 f20
            }

    @staticmethod
    def save_stockdata(data):
        """
        把逐页的生成器数据整理成一个 DataFrame。

        :param data: 生成器列表，每个元素对应一页的 ``get_stockdata`` 结果
        :return: 合并后的 :class:`pandas.DataFrame`
        """

        def parse_data(unique_data):
            """
            解析单页数据，把英文字段整理成中文列。

            :param unique_data: 一页的股票字典迭代器
            :return: 列名固定的 dict，可直接构造 DataFrame
            """
            re_data = dict()
            re_data.setdefault('代码', list())
            re_data.setdefault('最新价', list())
            re_data.setdefault('涨跌幅', list())
            re_data.setdefault('最高', list())
            re_data.setdefault('最低', list())
            re_data.setdefault('开盘', list())
            re_data.setdefault('成交量', list())
            re_data.setdefault('换手率', list())
            re_data.setdefault('市值', list())

            for category in unique_data:  # 每一页的数据

                try:
                    re_data['代码'].append(category.get('Code'))
                    re_data['最新价'].append(category.get('Latestprice'))
                    re_data['涨跌幅'].append(category.get('Fluctuationrange'))
                    re_data['最高'].append(category.get('High'))
                    re_data['最低'].append(category.get('Low'))
                    re_data['开盘'].append(category.get('Open'))
                    # 原实现把「量比」放进了「成交量」列，保持原样以免影响下游
                    re_data['成交量'].append(category.get('Volumeratio'))
                    re_data['换手率'].append(category.get('Turnoverrate'))
                    re_data['市值'].append(category.get('Marketvalue'))
                except Exception as err:
                    log.info(err)
                    continue

            return re_data

        df_list = list()
        for unique in data:
            # 每个 unique 是一页的生成器，parse_data 会把它消费掉
            df_list.append(DataFrame(parse_data(unique)))

        # concat(df_list).to_csv(path_or_buf='test.csv', index=False)
        df = concat(df_list)
        # df.set_index('代码', inplace=True)
        return df

    def run(self):
        """
        全量抓取入口。

        :return: 全市场行情 DataFrame；异常时返回 None
        """
        try:
            url_list = self.get_url()

            data = list()
            for url in url_list:
                try:
                    content = self.parse_url(url)
                    # 注意：这里 append 的是「生成器」，真正的解析推迟到 save_stockdata
                    data.append(self.get_stockdata(content))
                except Exception as e:
                    log.info(e)
                    continue

            return self.save_stockdata(data)

        except Exception as e:
            log.info(e)
            return None


if __name__ == '__main__':
    # 自测：全量抓取会产生数百次请求，这里只取前 2 页验证链路是否通畅
    spider = DailymarketSpider()
    urls = spider.get_url()
    print('总页数:', len(urls))

    sample = [spider.get_stockdata(spider.parse_url(url)) for url in urls[:2]]
    df = spider.save_stockdata(sample)
    print(df.head())
