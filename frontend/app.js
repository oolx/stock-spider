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
        axisLabel: { color: '#94a3b8' },
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
          left: hasIndex ? 52 : 16,
          right: hasStock ? 46 : 16,
          top: 12,
          bottom: 24,
        },
        tooltip: {
          trigger: 'axis',
          axisPointer: { type: 'cross', label: { backgroundColor: '#334155' } },
          backgroundColor: 'rgba(17, 24, 39, 0.94)',
          borderColor: '#334155',
          textStyle: { color: '#e2e8f0', fontSize: 12 },
          formatter(params) {
            if (!params || !params.length) return '';
            const time = new Date(params[0].value[0]).toLocaleTimeString('zh-CN', { hour12: false });
            const lines = params.map(point => (
              `${point.marker}${point.seriesName}：<b>${Number(point.value[1]).toFixed(2)}</b>`
            ));
            return `${time}<br/>${lines.join('<br/>')}`;
          },
        },
        xAxis: {
          type: 'time',
          ...axisStyle(),
          splitLine: { show: false },
          axisLabel: { color: '#94a3b8', hideOverlap: true },
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
          lineStyle: { width: 1.8 },
          emphasis: { focus: 'series' },
          data: card.points,
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
      // 按 series id 只更新这一条线，实现增量追加
      chart.setOption({
        series: [{ id: card.id, name: card.name, data: card.points }],
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
    };
  },
}).mount('#app');
