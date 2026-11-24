/**
 * Vue3 + ECharts 实时行情前端
 *
 * 数据流：
 *   1. GET /api/symbols              拿到配置的指数/股票
 *   2. GET /api/history?id=...       预填最近 500 个点
 *   3. WebSocket /ws                 每 3 秒收到最新 tick，追加到环形缓冲
 *   4. PUT /api/symbols              在「标的设置」中修改代码后整体保存
 *
 * 标的唯一键是 id = kind:symbol（如 index:000001、stock:000001），
 * 因此上证指数 000001 和股票 000001 可以同时存在、互不干扰。
 *
 * 环形缓冲：每个标的独立维护 points 数组，超过 MAX_POINTS 时从头丢弃。
 * 增量更新：只把新的 data 交给 ECharts（lazyUpdate），不重建图表实例。
 * 断线重连：指数退避，1s -> 2s -> 4s ... 最大 15s。
 */

const { createApp, reactive, ref, computed, nextTick, onMounted, onBeforeUnmount } = Vue;

const MAX_POINTS = 500;
const RETRY_BASE = 1000;
const RETRY_MAX = 15000;
const ALLOWED_INDEX = ['399001', '000001', '399006', '000300'];
const WEEKDAY_CN = ['日', '一', '二', '三', '四', '五', '六'];

function parseDay(value) {
  return new Date(`${value}T00:00:00`);
}

function todayString() {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  return `${now.getFullYear()}-${month}-${day}`;
}

function calendarLabel(item) {
  if (item.type === 'holiday') return item.name || '假';
  if (item.type === 'rest') return '休';
  return item.name ? '班' : '';
}

/* ---------------- 交易时间压缩 ----------------
 * 后端只在交易时段抓取数据，午休（11:30–13:00）、隔夜、周末都没有数据。
 * 如果直接用真实时间做横轴，ECharts 会把 11:30 和 13:00 两个点连起来，
 * 画出一条并不存在的“直线”。这里把时间映射成“累计交易分钟数”：
 * 只统计真实交易过的时间，非交易时段被折叠掉，折线自然衔接。
 */
const TRADING_SESSIONS = [[570, 690], [780, 930]]; // 09:30-11:30、13:00-15:30
const SESSION_MINUTES = TRADING_SESSIONS.reduce((sum, [start, end]) => sum + (end - start), 0);

/** 某个时刻在当天已经走过的交易分钟数（盘前/午休/盘后自动停在边界） */
function tradingMinutesOfDay(date) {
  const minutes = date.getHours() * 60 + date.getMinutes() + date.getSeconds() / 60;
  let passed = 0;
  for (let i = 0; i < TRADING_SESSIONS.length; i += 1) {
    const start = TRADING_SESSIONS[i][0];
    const end = TRADING_SESSIONS[i][1];
    if (minutes < start) return passed;
    if (minutes <= end) return passed + (minutes - start);
    passed += end - start;
  }
  return passed;
}

/** 1970-01-01（周四）以来的交易日序号（只剔除周六日），使跨天不留空隙 */
function tradingDayIndex(date) {
  const days = Math.floor(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()) / 86400000);
  const weeks = Math.floor(days / 7);
  let count = weeks * 5;
  for (let i = 0; i < days % 7; i += 1) {
    const weekday = (4 + i) % 7;
    if (weekday !== 0 && weekday !== 6) count += 1;
  }
  return count;
}

/** 时间戳 -> 压缩后的横轴值（分钟） */
function compressTime(ts) {
  const date = new Date(Number(ts));
  return tradingDayIndex(date) * SESSION_MINUTES + tradingMinutesOfDay(date);
}

/** 分钟数 -> "HH:MM" */
function formatClock(minutes) {
  const total = Math.round(minutes);
  const hour = Math.floor(total / 60);
  const minute = total % 60;
  return (hour < 10 ? '0' : '') + hour + ':' + (minute < 10 ? '0' : '') + minute;
}

/** 压缩后的横轴值 -> 真实时钟，用于坐标轴刻度和指示线 */
function clockLabel(minutes) {
  let rest = Number(minutes) % SESSION_MINUTES;
  if (rest < 0) rest += SESSION_MINUTES;
  for (let i = 0; i < TRADING_SESSIONS.length; i += 1) {
    const start = TRADING_SESSIONS[i][0];
    const end = TRADING_SESSIONS[i][1];
    if (rest <= end - start) return formatClock(start + rest);
    rest -= end - start;
  }
  return formatClock(TRADING_SESSIONS[TRADING_SESSIONS.length - 1][1]);
}

/** 把 [ts, price] 原始缓冲转换为图表数据，同时保留原始时间供 tooltip 使用 */
function toSeriesData(points) {
  return points.map(point => ({ value: [compressTime(point[0]), point[1]], ts: point[0] }));
}

createApp({
  setup() {
    const cards = reactive([]);
    const status = ref('connecting');
    const error = ref('');

    // 标的设置面板
    const settingsOpen = ref(false);
    const draft = reactive([]);
    const saving = ref(false);
    const saveMessage = ref('');

    // 翻页：0 = 行情页，1 = 日历页
    const page = ref(0);

    // 日历
    const calYear = ref(new Date().getFullYear());
    const calMonth = ref(new Date().getMonth() + 1);
    const calDays = ref([]);
    // 当月每个工作日的收入：{ date: { salary, invest } }
    const calSummary = ref({});
    const calendarLoaded = ref(false);
    const calLoading = ref(false);
    const calError = ref('');

    // 某一天的收入明细
    const dayOpen = ref(false);
    const dayDate = ref('');
    const dayType = ref('work');
    const dayName = ref('');
    const dayDraft = reactive([]);
    const daySalary = ref(0);
    const dayInvest = ref(0);
    const dayLoading = ref(false);
    const daySaving = ref(false);
    const dayMessage = ref('');
    const daySource = ref('none');
    const daySnapshotDate = ref(null);

    const daySourceNote = computed(() => {
      if (daySource.value === 'inherited' && daySnapshotDate.value) {
        return `沿用 ${daySnapshotDate.value} 的持仓设置`;
      }
      if (daySource.value === 'none') return '该日之前没有投资记录，默认不投资';
      return '';
    });

    const dayTotal = computed(() => Number(daySalary.value) + Number(dayInvest.value));
    const dayTitle = computed(() => {
      if (!dayDate.value) return '';
      const current = parseDay(dayDate.value);
      return `${dayDate.value} 周${WEEKDAY_CN[current.getDay()]}`;
    });
    const daySubtitle = computed(() => {
      const labels = { work: '工作日', rest: '休息日', holiday: '假期' };
      return `${labels[dayType.value] || ''}${dayName.value ? ` · ${dayName.value}` : ''}`;
    });

    // 日历格子：按周一为第一列补齐前置空白
    const calCells = computed(() => {
      const cells = [];
      if (!calDays.value.length) return cells;
      const first = parseDay(calDays.value[0].date);
      const offset = (first.getDay() + 6) % 7;
      for (let i = 0; i < offset; i += 1) {
        cells.push({ key: `blank-${i}`, day: null });
      }
      const today = todayString();
      calDays.value.forEach(item => {
        const summary = calSummary.value[item.date] || null;
        const isWork = item.type === 'work';
        // 小方块内只显示整数，便于快速浏览；点击后再看精确明细
        const salaryValue = isWork && summary ? Math.round(Number(summary.salary) || 0) : 0;
        const investValue = isWork && summary ? Math.round(Number(summary.invest) || 0) : 0;
        cells.push({
          ...item,
          key: item.date,
          label: calendarLabel(item),
          isToday: item.date === today,
          salaryText: salaryValue > 0 ? String(salaryValue) : '',
          investText: isWork && summary
            ? (investValue > 0 ? `+${investValue}` : String(investValue))
            : '',
          investClass: investValue > 0 ? 'up' : (investValue < 0 ? 'down' : 'flat'),
        });
      });
      return cells;
    });

    // 所有标的共用一个 ECharts 实例
    let chart = null;

    let socket = null;
    let retryCount = 0;
    let retryTimer = null;

    const statusText = computed(() => ({
      online: '实时连接',
      connecting: '连接中…',
      offline: '已断线，重连中…',
    }[status.value] || status.value));

    /* ---------------- 图表 ---------------- */

    function axisStyle() {
      return {
        axisLine: { lineStyle: { color: '#334155' } },
        axisLabel: { color: '#94a3b8', fontSize: 9 },
        splitLine: { lineStyle: { color: 'rgba(148, 163, 184, 0.12)' } },
      };
    }

    function buildOption() {
      const hasIndex = cards.some(card => card.kind === 'index');
      const hasStock = cards.some(card => card.kind === 'stock');

      return {
        animation: false,
        color: ['#38bdf8', '#fbbf24', '#a78bfa', '#34d399', '#f472b6', '#22d3ee', '#fb923c', '#e879f9'],
        grid: {
          left: hasIndex ? 30 : 8,
          right: hasStock ? 26 : 8,
          top: 4,
          bottom: 16,
        },
        tooltip: {
          trigger: 'axis',
          axisPointer: {
            type: 'cross',
            label: { backgroundColor: '#334155' },
          },
          backgroundColor: 'rgba(17, 24, 39, 0.94)',
          borderColor: '#334155',
          textStyle: { color: '#e2e8f0', fontSize: 11 },
          formatter(params) {
            if (!params || !params.length) return '';
            const first = params[0];
            const ts = (first.data && first.data.ts) || first.value[0];
            const time = new Date(ts).toLocaleTimeString('zh-CN', { hour12: false });
            const lines = params.map(point => (
              `${point.marker}${point.seriesName}：<b>${Number(point.value[1]).toFixed(2)}</b>`
            ));
            return `${time}<br/>${lines.join('<br/>')}`;
          },
        },
        xAxis: {
          // 使用压缩后的“交易分钟”作为数值轴，午休/隔夜/周末不再占位
          type: 'value',
          scale: true,
          ...axisStyle(),
          splitLine: { show: false },
          axisLabel: {
            color: '#94a3b8',
            fontSize: 9,
            hideOverlap: true,
            formatter: value => clockLabel(value),
          },
          // 十字准星的 x 轴标签同样还原成时钟
          axisPointer: {
            label: { formatter: params => clockLabel(params.value) },
          },
        },
        // 指数与股票价格量级差异大，分别使用左右两条 Y 轴
        yAxis: [
          {
            type: 'value',
            show: hasIndex,
            position: 'left',
            scale: true,
            ...axisStyle(),
          },
          {
            type: 'value',
            show: hasStock,
            position: 'right',
            scale: true,
            ...axisStyle(),
            splitLine: { show: false },
          },
        ],
        series: cards.map(card => ({
          id: card.id,
          name: card.name,
          type: 'line',
          yAxisIndex: card.kind === 'index' ? 0 : 1,
          showSymbol: false,
          smooth: false,
          lineStyle: { width: 1.2 },
          emphasis: { focus: 'series' },
          data: toSeriesData(card.points),
        })),
      };
    }

    function initChart() {
      if (chart) return;
      const el = document.getElementById('main-chart');
      if (!el) return;
      chart = echarts.init(el);
    }

    function renderChart() {
      if (!chart) return;
      // 标的增删或改名时整体重建，避免残留旧 series
      chart.setOption(buildOption(), { notMerge: true });
    }

    function redraw(card) {
      if (!chart) return;
      // 按 series id 只更新这一条线，实现增量追加（横轴同为压缩后的交易分钟）
      chart.setOption({
        series: [{ id: card.id, name: card.name, data: toSeriesData(card.points) }],
      }, { lazyUpdate: true });
    }

    /* ---------------- 持仓收益 ---------------- */

    /**
     * 计算今日投资盈亏：Σ (最新价 - 昨收) × 股数。
     *
     * 这等价于「以上班前价格为基准，累计到现在的持仓浮盈变化」：
     *    (最新价 - 成本价) - (昨收 - 成本价) = 最新价 - 昨收
     * 因此每天开盘（上班）时会自然归零，无需额外记录基准。
     * 只有同时填写了持仓价和股数的标的才计入。
     * 结果写入 window.__investmentSnapshot，供秒薪计算器叠加到今日已赚。
     */
    function updateInvestmentSnapshot() {
      let profit = 0;
      let count = 0;

      cards.forEach(card => {
        const cost = Number(card.cost);
        const shares = Number(card.shares);
        if (card.kind !== 'stock' || !(cost > 0) || !(shares > 0)) return;

        count += 1;
        // 价格或昨收还没拿到时不参与计算，避免把 null 当成 0
        if (card.price === null || card.price === undefined) return;
        if (card.prevClose === null || card.prevClose === undefined) return;

        const price = Number(card.price);
        const prevClose = Number(card.prevClose);
        if (Number.isFinite(price) && Number.isFinite(prevClose)) {
          profit += (price - prevClose) * shares;
        }
      });

      window.__investmentSnapshot = { profit, count };
    }

    /* ---------------- 环形缓冲 ---------------- */

    function appendPoint(card, tick, silent) {
      const ts = Number(tick.ts) || Date.now();
      const price = Number(tick.price);
      if (!Number.isFinite(price)) return;

      card.points.push([ts, price]);
      // 超过上限时丢弃最旧的点，保持最多 500 个
      if (card.points.length > MAX_POINTS) {
        card.points.splice(0, card.points.length - MAX_POINTS);
      }

      card.price = price;
      if (tick.prev_close !== null && tick.prev_close !== undefined) {
        card.prevClose = Number(tick.prev_close);
      }
      card.change = tick.change;
      card.change_pct = tick.change_pct;
      card.lastTime = new Date(ts).toLocaleTimeString('zh-CN', { hour12: false });
      // 股票名称由后端从行情接口带回（用户自定义名称优先）
      if (tick.name) card.name = tick.name;

      updateInvestmentSnapshot();

      if (!silent) redraw(card);
    }

    /* ---------------- 初始加载 ---------------- */

    async function loadData() {
      const response = await fetch('/api/symbols');
      if (!response.ok) throw new Error(`加载标的失败：HTTP ${response.status}`);
      const payload = await response.json();

      (payload.items || []).forEach(item => {
        const id = item.id || `${item.kind}:${item.symbol}`;
        cards.push({
          ...item,
          id,
          cost: Number(item.cost) || 0,
          shares: Number(item.shares) || 0,
          price: null,
          prevClose: null,
          change: null,
          change_pct: null,
          lastTime: '',
          points: [],
        });
      });

      updateInvestmentSnapshot();

      // 等容器渲染后再初始化图表（所有标的共用一个实例）
      await nextTick();
      initChart();
      renderChart();
      // 布局稳定后再校正一次尺寸（例如设置面板收起后）
      requestAnimationFrame(() => chart && chart.resize());

      // 并发拉取每个标的的历史数据，静默填充，最后统一重绘
      await Promise.all(cards.map(async card => {
        try {
          const res = await fetch(`/api/history?id=${encodeURIComponent(card.id)}&limit=${MAX_POINTS}`);
          if (!res.ok) return;
          const history = await res.json();
          (history.items || []).forEach(tick => appendPoint(card, tick, true));
          if (card.points.length) redraw(card);
        } catch (e) {
          console.warn('加载历史数据失败', card.id, e);
        }
      }));

      // 历史数据补齐后整体重绘一次
      renderChart();
    }

    async function reloadDashboard() {
      // 销毁旧图表，重新按最新标的列表加载
      if (chart) {
        try {
          chart.dispose();
        } catch (e) {
          console.warn('销毁图表失败', e);
        }
        chart = null;
      }
      cards.splice(0, cards.length);
      window.__investmentSnapshot = { profit: 0, count: 0 };
      error.value = '';
      try {
        await loadData();
      } catch (e) {
        error.value = e.message || String(e);
      }
    }

    /* ---------------- 标的设置 ---------------- */

    function toggleSettings() {
      if (!settingsOpen.value) {
        // 每次打开都从当前生效的标的复制一份草稿
        draft.splice(0, draft.length, ...cards.map(card => ({
          kind: card.kind,
          symbol: card.symbol,
          name: card.name,
          cost: card.cost || '',
          shares: card.shares || '',
          originalKind: card.kind,
          originalSymbol: card.symbol,
          originalName: card.name,
        })));
        saveMessage.value = '';
      }
      settingsOpen.value = !settingsOpen.value;
    }

    function addDraft(kind) {
      draft.push({
        kind,
        symbol: '',
        name: '',
        cost: '',
        shares: '',
        originalKind: kind,
        originalSymbol: '',
        originalName: '',
      });
    }

    function removeDraft(index) {
      draft.splice(index, 1);
    }

    async function saveSymbols() {
      saveMessage.value = '';

      const items = draft.map(item => {
        const symbol = (item.symbol || '').trim();
        let name = (item.name || '').trim();
        const changed = symbol !== item.originalSymbol || item.kind !== item.originalKind;
        // 只改了代码/类型、没改名称时，清空旧名称，让后端自动获取新标的名称
        if (changed && name === item.originalName) name = '';
        return {
          kind: item.kind,
          symbol,
          name,
          cost: Number(item.cost) || 0,
          shares: Number(item.shares) || 0,
        };
      });

      if (!items.length) {
        saveMessage.value = '至少保留一个标的';
        return;
      }

      for (const item of items) {
        if (!/^\d{6}$/.test(item.symbol)) {
          saveMessage.value = `代码必须是 6 位数字：${item.symbol || '(空)'}`;
          return;
        }
        if (item.kind === 'index' && !ALLOWED_INDEX.includes(item.symbol)) {
          saveMessage.value = `指数仅支持 ${ALLOWED_INDEX.join(' / ')}`;
          return;
        }
        if (item.kind === 'stock' && !/^[01356]/.test(item.symbol)) {
          saveMessage.value = `股票/ETF 代码需以 0/1/3/5/6 开头：${item.symbol}`;
          return;
        }
        if (item.cost < 0 || item.shares < 0) {
          saveMessage.value = `持仓价和股数不能为负数：${item.symbol}`;
          return;
        }
      }

      saving.value = true;
      try {
        const response = await fetch('/api/symbols', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ items }),
        });
        const payload = await response.json();
        if (!response.ok) {
          saveMessage.value = payload.detail || `保存失败：HTTP ${response.status}`;
          return;
        }
        settingsOpen.value = false;
        await reloadDashboard();
      } catch (e) {
        saveMessage.value = e.message || String(e);
      } finally {
        saving.value = false;
      }
    }

    /* ---------------- 日历页 ---------------- */

    function switchPage(index) {
      if (index === page.value) return;
      page.value = index;
      if (index === 1) {
        if (!calendarLoaded.value) loadCalendar();
        return;
      }
      // 从日历页切回行情页时，图表在隐藏状态下尺寸为 0，需要重新校正
      nextTick(() => {
        initChart();
        renderChart();
        requestAnimationFrame(() => chart && chart.resize());
      });
    }

    async function loadCalendar() {
      calLoading.value = true;
      calError.value = '';
      try {
        const response = await fetch(`/api/calendar?year=${calYear.value}&month=${calMonth.value}`);
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || `加载日历失败：HTTP ${response.status}`);
        calDays.value = payload.days || [];
        calendarLoaded.value = true;
        // 方格内的数字依赖历史行情，单独异步加载，不阻塞日历显示
        loadMonthSummary();
      } catch (e) {
        calError.value = e.message || String(e);
      } finally {
        calLoading.value = false;
      }
    }

    /** 拉取当月工资 + 投资的小数字，填充到日历方格 */
    async function loadMonthSummary() {
      try {
        const response = await fetch(
          `/api/month-summary?year=${calYear.value}&month=${calMonth.value}`
        );
        if (!response.ok) return;
        const payload = await response.json();
        const map = {};
        (payload.days || []).forEach(item => {
          map[item.date] = item;
        });
        calSummary.value = map;
      } catch (e) {
        // 数字加载失败不影响日历本身
      }
    }

    async function selectDay(cell) {
      if (!cell || !cell.day || cell.type !== 'work') return;
      dayDate.value = cell.date;
      dayType.value = cell.type;
      dayName.value = cell.name || '';
      dayOpen.value = true;
      dayMessage.value = '';
      daySalary.value = 0;
      dayInvest.value = 0;
      dayDraft.splice(0, dayDraft.length);

      dayLoading.value = true;
      try {
        const [snapRes, settingsRes] = await Promise.all([
          fetch(`/api/day-holdings?date=${cell.date}`),
          fetch('/api/settings'),
        ]);
        const snapshot = snapRes.ok ? await snapRes.json() : null;
        const settings = settingsRes.ok ? await settingsRes.json() : {};
        daySalary.value = cell.type === 'work' ? (Number(settings.daily) || 0) : 0;
        if (snapshot) {
          daySource.value = snapshot.source || 'none';
          daySnapshotDate.value = snapshot.snapshot_date || null;
          // 日历页只统计股票/ETF 的持仓收益，指数没有持仓概念，直接忽略
          dayDraft.splice(0, dayDraft.length, ...(snapshot.items || [])
            .filter(item => item.kind === 'stock')
            .map(item => ({
              kind: 'stock',
              symbol: item.symbol,
              name: item.name || '',
              cost: Number(item.cost) || 0,
              shares: Number(item.shares) || 0,
              profit: null,
            })));
        }
      } catch (e) {
        dayMessage.value = `读取当日设置失败：${e.message || e}`;
      }
      await calcDayProfit();
    }

    async function calcDayProfit() {
      if (!dayDate.value) return;
      dayLoading.value = true;
      const results = await Promise.all(dayDraft.map(async item => {
        item.profit = null;
        // 收益 = (当日收盘价 - 设置持仓价) × 股数，两者都填写才算
        if (!(Number(item.cost) > 0) || !(Number(item.shares) > 0) || !/^\d{6}$/.test(item.symbol)) {
          item.profit = 0;
          return 0;
        }
        try {
          const response = await fetch(
            `/api/daily-quote?kind=stock&symbol=${item.symbol}&date=${dayDate.value}`
          );
          const quote = await response.json();
          const close = Number(quote.close);
          if (quote.found && Number.isFinite(close)) {
            item.profit = (close - Number(item.cost)) * Number(item.shares);
            return item.profit;
          }
        } catch (e) {
          // 单只失败按 0 处理，不阻塞其他标的
        }
        item.profit = 0;
        return 0;
      }));
      dayInvest.value = results.reduce((sum, value) => sum + value, 0);
      dayLoading.value = false;
    }

    function closeDay() {
      dayOpen.value = false;
    }

    function addDayItem() {
      dayDraft.push({ kind: 'stock', symbol: '', name: '', cost: 0, shares: 0, profit: null });
    }

    function removeDayItem(index) {
      dayDraft.splice(index, 1);
      calcDayProfit();
    }

    /** 清空当日投资标的（保存后表示从这一天起不投资） */
    function clearDayItems() {
      dayDraft.splice(0, dayDraft.length);
      daySource.value = 'exact';
      dayMessage.value = '';
      calcDayProfit();
    }

    async function saveDay() {
      dayMessage.value = '';
      const items = dayDraft.map(item => ({
        kind: 'stock',
        symbol: (item.symbol || '').trim(),
        name: (item.name || '').trim(),
        cost: Number(item.cost) || 0,
        shares: Number(item.shares) || 0,
      }));

      for (const item of items) {
        if (!/^\d{6}$/.test(item.symbol)) {
          dayMessage.value = `代码必须是 6 位数字：${item.symbol || '(空)'}`;
          return;
        }
        if (!/^[01356]/.test(item.symbol)) {
          dayMessage.value = `股票/ETF 代码需以 0/1/3/5/6 开头：${item.symbol}`;
          return;
        }
      }

      daySaving.value = true;
      try {
        const response = await fetch('/api/day-holdings', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ date: dayDate.value, items }),
        });
        const payload = await response.json();
        if (!response.ok) {
          dayMessage.value = payload.detail || `保存失败：HTTP ${response.status}`;
          return;
        }
        dayMessage.value = '已保存，正在按当日价格计算投资收益…';
        // 保存后重新爬取当日行情，并按「持仓价 / 股数」刷新当日投资收入
        await calcDayProfit();
        // 同步刷新日历方格内的数字
        await loadMonthSummary();
        dayMessage.value = '已保存';
      } catch (e) {
        dayMessage.value = e.message || String(e);
      } finally {
        daySaving.value = false;
      }
    }

    function money(value) {
      const number = Number(value);
      return Number.isFinite(number) ? number.toFixed(2) : '0.00';
    }

    function signedMoney(value) {
      const number = Number(value);
      return `${number > 0 ? '+' : ''}${money(number)}`;
    }

    function signClass(value) {
      const number = Number(value);
      if (number > 0) return 'up';
      if (number < 0) return 'down';
      return 'flat';
    }

    /* ---------------- WebSocket ---------------- */

    function scheduleReconnect() {
      status.value = 'offline';
      const delay = Math.min(RETRY_BASE * Math.pow(2, retryCount), RETRY_MAX);
      retryCount += 1;
      clearTimeout(retryTimer);
      retryTimer = setTimeout(connect, delay);
    }

    function connect() {
      clearTimeout(retryTimer);
      status.value = 'connecting';

      const protocol = location.protocol === 'https:' ? 'wss://' : 'ws://';
      try {
        socket = new WebSocket(`${protocol}${location.host}/ws`);
      } catch (e) {
        scheduleReconnect();
        return;
      }

      socket.onopen = () => {
        retryCount = 0;
        status.value = 'online';
      };

      socket.onmessage = event => {
        let message;
        try {
          message = JSON.parse(event.data);
        } catch (e) {
          return;
        }
        if (message.type !== 'tick') return;

        const id = message.id || `${message.kind}:${message.symbol}`;
        const card = cards.find(item => item.id === id);
        if (card) appendPoint(card, message);
      };

      socket.onerror = () => {
        // 触发 close 后统一走重连逻辑
        if (socket) socket.close();
      };

      socket.onclose = () => {
        socket = null;
        scheduleReconnect();
      };
    }

    /* ---------------- 展示辅助 ---------------- */

    function formatPrice(card) {
      if (card.price === null || card.price === undefined) return '--';
      return Number(card.price).toFixed(2);
    }

    function formatPct(card) {
      if (card.change_pct === null || card.change_pct === undefined) return '--';
      const value = Number(card.change_pct);
      return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
    }

    function trendClass(card) {
      if (card.change_pct === null || card.change_pct === undefined) return 'flat';
      if (card.change_pct > 0) return 'up';
      if (card.change_pct < 0) return 'down';
      return 'flat';
    }

    function handleResize() {
      if (chart) chart.resize();
    }

    onMounted(async () => {
      window.addEventListener('resize', handleResize);
      try {
        await loadData();
      } catch (e) {
        error.value = e.message || String(e);
        console.error(e);
      }
      connect();
    });

    onBeforeUnmount(() => {
      window.removeEventListener('resize', handleResize);
      clearTimeout(retryTimer);
      if (socket) socket.close();
      if (chart) {
        chart.dispose();
        chart = null;
      }
    });

    return {
      cards,
      status,
      statusText,
      error,
      settingsOpen,
      draft,
      saving,
      saveMessage,
      toggleSettings,
      addDraft,
      removeDraft,
      saveSymbols,
      formatPrice,
      formatPct,
      trendClass,
      // 翻页与日历
      page,
      switchPage,
      calYear,
      calMonth,
      calCells,
      calLoading,
      calError,
      selectDay,
      dayOpen,
      dayTitle,
      daySubtitle,
      dayDraft,
      daySalary,
      dayInvest,
      dayTotal,
      dayLoading,
      daySaving,
      dayMessage,
      daySourceNote,
      closeDay,
      addDayItem,
      removeDayItem,
      calcDayProfit,
      clearDayItems,
      saveDay,
      money,
      signedMoney,
      signClass,
    };
  },
}).mount('#app');
