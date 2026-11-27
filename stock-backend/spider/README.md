# spider 模块说明

东方财富行情爬虫模块，按抓取对象拆分为三个独立类。所有类都只负责「发起请求 + 解析响应」，不包含定时调度逻辑，调用方可自行决定调用频率。

## 目录结构

```
spider/
├── __init__.py               # 统一导出三个爬虫类
├── logger.py                 # 日志配置，日志写入当前工作目录的 logs/
├── real_data_spider.py       # RealDataSpider     —— 实时行情（单只/多只股票）
├── dailymarket_spider.py     # DailymarketSpider  —— 全市场当日行情
└── index_data_spider.py      # IndexDataSpider    —— 主要指数实时行情
```

## 依赖与运行环境

- Python 3.8+
- `requests`：发送 HTTP 请求
- `pandas`：全市场行情结果整理为 DataFrame

```bash
pip install requests pandas
```

运行时可从 `stock-spider` 根目录执行：

```bash
# 方式一：直接运行脚本（各文件内置 __main__ 自测）
python spider/real_data_spider.py
python spider/dailymarket_spider.py
python spider/index_data_spider.py

# 方式二：作为包模块运行
python -m spider.real_data_spider
```

> `logger.py` 会在**当前工作目录**下创建 `logs/` 并写入 `YYYYmmddHHMMSS.log`，建议统一在 `stock-spider` 根目录下运行。

---

## 一、RealDataSpider —— 实时行情（单只/多只股票、ETF）

文件：[real_data_spider.py](real_data_spider.py)

### 功能

获取一只或多只股票、ETF 的实时快照，并把接口的字段编号翻译成中文键名。

默认使用**批量接口**，一次请求即可查询多只标的：

```
GET http://push2.eastmoney.com/api/qt/ulist.np/get
```

返回普通 JSON，行情在 `data.diff` 列表中，例如：

```
{"rc":0,"data":{"total":2,"diff":[{"f2":7.396,"f3":1.57,"f12":"518880","f14":"黄金ETF华安"},...]}}
```

批量接口不可用（或调用方自定义了字段）时，回退到单只接口：

```
GET http://push2.eastmoney.com/api/qt/stock/get
```

该接口返回 JSONP 文本，例如：

```
jQuery1124....({"rc":0,"rt":4,"data":{"f43":16.58,"f57":"600026",...}});
```

### 市场编号规则

| 代码开头 | 类型 | 市场 | secid 示例 |
| --- | --- | --- | --- |
| `6` | 沪市股票 | 1 | `1.600026` |
| `5` | 沪市 ETF / LOF | 1 | `1.518880`（黄金ETF） |
| `0` / `3` | 深市股票 | 0 | `0.000001`、`0.300059` |
| `1` | 深市 ETF / LOF | 0 | `0.159941`（纳指ETF） |

其它开头（如 `8` / `4` 开头的北交所代码）不会生成请求参数，会被自动忽略。

### 主要属性

| 属性 | 说明 |
| --- | --- |
| `ULIST_API` | 批量接口地址 `http://push2.eastmoney.com/api/qt/ulist.np/get` |
| `GS_API` | 单只接口地址 `http://push2.eastmoney.com/api/qt/stock/get`（回退用） |
| `headers` | 浏览器 User-Agent |
| `datatype` | 单只接口字段编号 → 中文名映射，共约 35 个字段 |
| `parameters` | 单只接口请求的字段编号串，默认包含全部映射字段 |
| `ulist_datatype` | 批量接口字段编号 → 中文名映射 |
| `ulist_parameters` | 批量接口请求的字段编号串 |

批量接口字段（默认 `run` 返回的内容）：

| 编号 | 含义 | 编号 | 含义 |
| --- | --- | --- | --- |
| `f2` | 最新价 | `f3` | 涨幅 |
| `f4` | 涨跌 | `f5` | 成交量 |
| `f6` | 成交额 | `f8` | 换手 |
| `f10` | 量比 | `f12` | 代码 |
| `f14` | 名称 | `f15` / `f16` | 最高 / 最低 |
| `f17` | 今开 | `f18` | 昨收 |
| `f20` | 总市值 | `f21` | 流通市值 |

单只接口常用字段：

| 编号 | 含义 | 编号 | 含义 |
| --- | --- | --- | --- |
| `f43` | 最新价 | `f44` | 最高 |
| `f45` | 最低 | `f46` | 今开 |
| `f47` | 成交量 | `f48` | 成交额 |
| `f50` | 量比 | `f57` | 代码 |
| `f58` | 名称 | `f60` | 昨收 |
| `f168` | 换手 | `f170` | 涨幅 |
| `f116` | 总市值 | `f117` | 流通市值 |
| `f162` | 市盈动 | `f167` | 市净 |
| `f193`~`f197` | 主力/超大/大/中/小单净比 | | |

### 方法

#### `get_secid(code) -> str | None`（静态方法）

把 6 位代码翻译成东财的 `市场.代码`（secid）。`6`/`5` 开头返回 `1.xxx`，`0`/`3`/`1` 开头返回 `0.xxx`，其它返回 `None`。

#### `get_url(codes, parameters) -> dict`

为每个代码构造单只接口的 requests 参数字典，返回 `{代码: params}`。

- `codes`：代码列表，如 `['600026', '000001', '518880']`
- `parameters`：字段编号串，如 `'f43'`；传 `'default'` 使用默认字段集合

#### `run_ulist(codes) -> dict`

调用批量接口，一次请求返回 `{代码: {中文指标名: 值}}`；请求或解析失败时返回 `{}`。

#### `parse_url(params) -> str | None`

发送单只接口 GET 请求，返回 JSONP 原始文本；网络异常或状态码非 200 时返回 `None`。

#### `get_stockdata(code, content) -> dict`

解析单只股票的 JSONP 响应，返回 `{code: {中文指标名: 值}}`。内部会先剥离回调名、括号和分号，再逐字段取 `data[字段编号]`；单个字段缺失只记录日志，不影响其它字段。

#### `save_stockdata(stockdata) -> dict`

预留的数据整理接口，当前不做落盘，原样返回入参。保留是为了兼容早期调用方。

#### `run(codes, parameters='default') -> dict | None`

抓取入口：

- `parameters='default'`：优先调用 `run_ulist` 批量接口，一次拿到全部标的；
- 自定义字段或批量接口失败：回退到逐只调用单只接口。

### 用法示例

```python
from spider import RealDataSpider

spider = RealDataSpider()

# 默认字段（批量接口）：股票 + 两只 ETF
print(spider.run(['600026', '518880', '159941']))
# {'518880': {'最新价': 7.396, '涨幅': 1.57, '名称': '黄金ETF华安', ...},
#  '159941': {'最新价': 1.517, '涨幅': -0.59, '名称': '纳指ETF广发', ...},
#  '600026': {'最新价': 16.58, '涨幅': 0.85, '名称': '中远海能', ...}}

# 自定义字段（回退到单只接口）：只取最新价
print(spider.run(['600026', '000001'], 'f43'))
# {'600026': {'最新价': 16.58}, '000001': {'最新价': 12.34}}

# 自定义字段组合，可拿到批量接口没有的财务字段
data = spider.run(['600026'], 'f43,f57,f58,f162')
print(data['600026']['市盈动'])
```

### 注意事项

- 批量接口一次请求即可查多只标的；仅回退到单只接口时才是**串行**请求，代码数量较多时请自行控制频率。
- 单只接口的 JSONP 回调名和 `_` 时间戳是硬编码的，接口若调整需同步修改。
- 接口返回的数值一般已按 `fltt=2` 保留两位小数，但类型可能是 `int` / `float` / `None`，使用前建议做类型判断。
- 若默认 `run` 的某个字段在结果中缺失，说明批量接口没有返回该字段，可改用自定义字段走单只接口。

---

## 二、DailymarketSpider —— 全市场当日行情

文件：[dailymarket_spider.py](dailymarket_spider.py)

### 功能

分页抓取全部 A 股的当日行情，整理为一个 pandas DataFrame。

```
GET http://76.push2.eastmoney.com/api/qt/clist/get
```

- 每页固定 20 条（`pz=20`）
- 第 1 页响应中含 `"total": 数量`，据此计算总页数
- 使用正则直接从 JSONP 文本中提取字段，不整体反序列化

筛选的板块（`fs` 参数）：`m:0+t:6`、`m:0+t:13`、`m:0+t:80`、`m:1+t:2`、`m:1+t:23`。

### 主要属性

| 属性 | 说明 |
| --- | --- |
| `url` | 分页 URL 模板，`{}` 为页码占位符 |
| `headers` | 浏览器 User-Agent |

### 方法

#### `get_url() -> list`

先请求第 1 页读取 `total`，按 `total / 20 + 1` 计算总页数并生成分页 URL 列表。

#### `parse_url(url) -> str | None`

请求单页并返回文本，失败返回 `None`。

#### `get_stockdata(content) -> generator`（静态方法）

从一页响应中逐条 `yield` 股票字典，键为英文字段：

| 键 | 含义 | 东财字段 |
| --- | --- | --- |
| `Code` | 代码 | `f12` |
| `Latestprice` | 最新价 | `f2` |
| `Fluctuationrange` | 涨跌幅 | `f3` |
| `Turnoverrate` | 换手率 | `f8` |
| `Volumeratio` | 量比 | `f10` |
| `High` / `Low` / `Open` | 最高 / 最低 / 今开 | `f15` / `f16` / `f17` |
| `Marketvalue` | 总市值 | `f20` |

#### `save_stockdata(data) -> DataFrame`（静态方法）

把每页的生成器整理为中文列 DataFrame：

`代码、最新价、涨跌幅、最高、最低、开盘、成交量、换手率、市值`

#### `run() -> DataFrame | None`

全量抓取入口。先 `get_url()` 得到分页列表，再逐页请求并把生成器收集起来，最后交给 `save_stockdata` 合并。

### 用法示例

```python
from spider import DailymarketSpider

spider = DailymarketSpider()

# 完整抓取（约 5000 只股票 / 278 页，耗时较长）
df = spider.run()
print(df.head())
# df.to_csv('dailymarket.csv', index=False)

# 只抓前两页，快速验证
urls = spider.get_url()
pages = [spider.get_stockdata(spider.parse_url(u)) for u in urls[:2]]
df = spider.save_stockdata(pages)
print(df.head())
```

### 注意事项（均为原始实现行为）

- **不含最后一页**：`range(1, page_number)` 生成的页码不包含 `page_number` 本身。
- **代码带引号**：正则捕获包含 JSON 的双引号，如 `'"301030"'`，如需纯代码可自行 `str.strip('"')`。
- **量比写入成交量列**：`save_stockdata` 中 `成交量` 列实际存放的是 `Volumeratio`（量比）。
- **解析依赖字段顺序**：正则假设返回 JSON 中字段顺序固定，接口格式变化时容易失效。
- 完整运行会产生 270+ 次请求，建议低频调用或分批抓取。

---

## 三、IndexDataSpider —— 主要指数实时行情

文件：[index_data_spider.py](index_data_spider.py)

### 功能

与 `RealDataSpider` 共用东方财富批量接口 `ulist.np/get`（失败时回退单只接口 `stock/get`），但代码固定为常见指数。由于指数代码的首位数字与市场并不一一对应，secid 在 `INDEX_SECID` 中显式登记。

默认抓取：

| 代码 | 名称 | secid |
| --- | --- | --- |
| `399001` | 深证成指 | `0.399001` |
| `000001` | 上证指数 | `1.000001` |
| `399006` | 创业板指 | `0.399006` |
| `000300` | 沪深300 | `1.000300` |

### 主要属性

| 属性 | 说明 |
| --- | --- |
| `ULIST_API` | 批量接口地址 |
| `GS_API` | 单只接口地址（回退用） |
| `headers` | 浏览器 User-Agent |
| `datatype` | 单只接口字段编号 → 中文名映射 |
| `ulist_datatype` | 批量接口字段编号 → 中文名映射 |
| `codes` | 待抓取的指数代码列表，可按需增删 |
| `INDEX_SECID` | 指数代码 → secid 映射，新增指数时在此补充 |
| `parameters` | 单只接口默认请求字段 |

### 方法

#### `get_url(parameters) -> dict`

按 `self.codes` 逐个构造单只接口的请求参数，返回 `{指数代码: params}`；未出现在 `INDEX_SECID` 中的代码会被跳过。

#### `run_ulist() -> dict`

调用批量接口，一次请求返回所有已登记指数的行情；失败时返回 `{}`。

#### `parse_url(params) -> str | None`

请求并返回 JSONP 文本。

#### `get_stockdata(code, content) -> dict`

解析单个指数响应，返回 `{code: {中文指标名: 值}}`。指数没有个股的财务字段，缺失字段会记录日志后跳过。

#### `save_stockdata(stockdata) -> dict`

预留接口，原样返回。

#### `run(parameters='default') -> dict | None`

抓取全部默认指数：`'default'` 时优先走批量接口，否则回退逐只查询。

### 用法示例

```python
from spider import IndexDataSpider

spider = IndexDataSpider()

data = spider.run()
for code, values in data.items():
    print(code, values.get('名称'), values.get('最新价'), values.get('涨幅'))

# 自定义要抓取的指数（需在 INDEX_SECID 中登记）
spider.codes = ['000001', '399001']
print(spider.run())
```

### 注意事项

- 默认 `run()` 返回批量接口字段（最新价、涨幅、昨收等），其中涨幅可直接取 `涨幅`。
- 指数与股票共用字段编号，但部分个股字段（如净利润、毛利率）对指数不适用；回退到单只接口时这些字段会缺失或为 `None`。
- 新增指数必须同时在 `INDEX_SECID` 中登记 secid，否则该指数不会出现在结果中。

---

## 四、日志

文件：[logger.py](logger.py)

```python
log = logging.getLogger(__name__)
```

- 日志级别：`DEBUG`
- 输出：控制台 + `logs/<YYYYmmddHHMMSS>.log`
- `logs/` 相对**当前工作目录**创建，首次导入模块时即生成

## 五、类之间的关系

```
RealDataSpider    ── 按 0/1/3/5/6 自动判断市场，适合股票 / ETF 实时快照
IndexDataSpider   ── secid 显式登记，适合固定指数
DailymarketSpider ── 列表接口 + 分页，适合全市场横截面数据
```

三者相互独立，均只依赖 `requests`、`pandas` 和 `logger.py`，可以单独复制使用。
