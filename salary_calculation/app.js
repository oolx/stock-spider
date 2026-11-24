/**
 * 摸鱼秒薪计算器
 * ================
 *
 * 只需要三个参数：
 *
 *   1. 上班时间（HH:MM）
 *   2. 下班时间（HH:MM）
 *   3. 日薪（元）
 *
 * 页面会实时计算：
 *
 *   - 每秒 / 每分钟 / 每小时能挣多少钱
 *   - 从上班到现在已经挣了多少钱（每 200ms 刷新一次）
 *   - 今日进度、已工作时长、距离下班时长
 *
 * 参数同时保存在浏览器 localStorage 与后端 SQLite：
 * - 同一台机器重新打开时优先使用本地缓存；
 * - 后端保存过的值会在启动时同步回来，复制 data/stock.db 到新机器即可沿用。
 * 支持跨天班次（如下班时间早于上班时间，视为次日下班）。
 *
 * 用法：页面上放一个空的容器即可，脚本会自动渲染并接管：
 *
 *   <div id="salary-calculator"></div>
 *
 * 也可以直接打开同目录下的 index.html 单独使用。
 */
(function () {
  'use strict';

  var STORAGE_KEY = 'moyu_salary_settings_v1';
  var DEFAULTS = { start: '09:00', end: '18:00', daily: 300 };
  var TICK_MS = 200;

  var root = null;
  var timer = null;
  var serverTimer = null;
  var els = {};

  /* ---------------- 参数读写 ---------------- */

  function loadSettings() {
    try {
      var saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
      return {
        start: isClock(saved.start) ? saved.start : DEFAULTS.start,
        end: isClock(saved.end) ? saved.end : DEFAULTS.end,
        daily: isFinite(Number(saved.daily)) && Number(saved.daily) >= 0
          ? Number(saved.daily)
          : DEFAULTS.daily
      };
    } catch (e) {
      return { start: DEFAULTS.start, end: DEFAULTS.end, daily: DEFAULTS.daily };
    }
  }

  function saveSettings(settings) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
    } catch (e) {
      // 隐私模式下可能写入失败，不影响当前页面的计算
    }
  }

  /* ---------------- 服务端同步（SQLite） ---------------- */

  function toPayload(settings) {
    return {
      start: settings.start,
      end: settings.end,
      daily: Number(settings.daily)
    };
  }

  /** 参数变化后延迟 300ms 写入后端，避免频繁请求 */
  function syncToServer(settings) {
    var payload = toPayload(settings);
    if (!isClock(payload.start) || !isClock(payload.end) ||
        !isFinite(payload.daily) || payload.daily < 0) {
      return;
    }
    clearTimeout(serverTimer);
    serverTimer = setTimeout(function () {
      putServer(payload);
    }, 300);
  }

  function putServer(payload) {
    if (!window.fetch) return;
    try {
      fetch('/api/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      }).catch(function () {});
    } catch (e) {}
  }

  /** 用一份设置回填输入框（不触发写回） */
  function applySettings(settings) {
    var startInput = root.querySelector('[data-field="start"]');
    var endInput = root.querySelector('[data-field="end"]');
    var dailyInput = root.querySelector('[data-field="daily"]');
    if (!startInput || !endInput || !dailyInput) return;
    startInput.value = settings.start;
    endInput.value = settings.end;
    dailyInput.value = settings.daily;
    update();
  }

  /**
   * 启动时从后端读取设置：
   * - 有保存过的值：覆盖本地并刷新；
   * - 没有：把本地值上传，便于随 data/stock.db 一起迁移到其他机器。
   */
  function loadFromServer() {
    if (!window.fetch) return;
    try {
      fetch('/api/settings').then(function (resp) {
        return resp.ok ? resp.json() : null;
      }).then(function (data) {
        if (!data) return;
        var daily = Number(data.daily);
        if (isClock(data.start) && isClock(data.end) && isFinite(daily) && daily >= 0) {
          var settings = { start: data.start, end: data.end, daily: daily };
          saveSettings(settings);
          applySettings(settings);
        } else {
          var local = readInputs();
          saveSettings(local);
          syncToServer(local);
        }
      }).catch(function () {});
    } catch (e) {}
  }

  function isClock(text) {
    return typeof text === 'string' && /^\d{2}:\d{2}$/.test(text);
  }

  function parseClock(text) {
    var parts = String(text).split(':');
    var h = Number(parts[0]);
    var m = Number(parts[1]);
    if (!isFinite(h) || !isFinite(m)) return null;
    return { h: h, m: m };
  }

  /* ---------------- 时间计算 ---------------- */

  /** 把 "HH:MM" 套到 base 这一天的对应时刻 */
  function atDate(base, clock) {
    var d = new Date(base);
    d.setHours(clock.h, clock.m, 0, 0);
    return d;
  }

  /**
   * 计算本次班次的起止时间。
   * 下班时间早于上班时间时视为跨天班次：
   * 凌晨（还没到上班点）属于前一天开始的班次，下班在当天。
   */
  function shiftRange(now, startClock, endClock) {
    var startMin = startClock.h * 60 + startClock.m;
    var endMin = endClock.h * 60 + endClock.m;
    var overnight = endMin <= startMin;

    var start;
    var end;

    if (overnight) {
      var nowMin = now.getHours() * 60 + now.getMinutes();
      if (nowMin < startMin) {
        // 凌晨：处于前一天开始的班次
        start = atDate(now, startClock);
        start.setDate(start.getDate() - 1);
        end = atDate(now, endClock);
      } else {
        start = atDate(now, startClock);
        end = atDate(now, endClock);
        end.setDate(end.getDate() + 1);
      }
    } else {
      start = atDate(now, startClock);
      end = atDate(now, endClock);
    }

    return { start: start, end: end };
  }

  /** 秒数 -> HH:MM:SS */
  function formatDuration(seconds) {
    var total = Math.max(0, Math.floor(seconds));
    var h = Math.floor(total / 3600);
    var m = Math.floor((total % 3600) / 60);
    var s = total % 60;
    return pad(h) + ':' + pad(m) + ':' + pad(s);
  }

  function pad(n) {
    return n < 10 ? '0' + n : String(n);
  }

  function money(value, digits) {
    return '¥' + Number(value).toLocaleString('zh-CN', {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits
    });
  }

  /** 带正负号的金额，如 +¥12.30 / -¥8.00 */
  function signedMoney(value, digits) {
    return (value >= 0 ? '+' : '-') + money(Math.abs(value), digits);
  }

  /* ---------------- 渲染 ---------------- */

  function render(settings) {
    root.innerHTML = [
      '<div class="salary-head">',
      '  <div class="salary-title">',
      '    <h2>摸鱼秒薪计算器</h2>',
      '  </div>',
      '  <div class="salary-form">',
      '    <label>上班时间<input type="time" data-field="start" value="' + settings.start + '"></label>',
      '    <label>下班时间<input type="time" data-field="end" value="' + settings.end + '"></label>',
      '    <label>日薪（元）<input type="number" data-field="daily" min="0" step="10" value="' + settings.daily + '"></label>',
      '  </div>',
      '</div>',
      '<div class="salary-body">',
      '  <div class="salary-main">',
      '    <span class="salary-label">今日已赚</span>',
      '    <strong class="salary-earned" data-ref="earned">¥0.0000</strong>',
      '    <div class="salary-breakdown" data-ref="breakdown" hidden>',
      '      <span>工资 <b data-ref="salaryPart">¥--</b></span>',
      '      <span>投资 <b data-ref="investPart">¥--</b></span>',
      '    </div>',
      '    <div class="salary-progress"><i data-ref="bar"></i></div>',
      '    <div class="salary-foot">',
      '      <span data-ref="percent">已完成 0%</span>',
      '      <span data-ref="worked">已工作 --:--:--</span>',
      '      <span data-ref="left">距离下班 --:--:--</span>',
      '    </div>',
      '  </div>',
      '</div>'
    ].join('');

    els = {};
    Array.prototype.forEach.call(root.querySelectorAll('[data-ref]'), function (el) {
      els[el.getAttribute('data-ref')] = el;
    });

    bindInputs();
  }

  function bindInputs() {
    Array.prototype.forEach.call(root.querySelectorAll('[data-field]'), function (input) {
      input.addEventListener('input', function () {
        var settings = readInputs();
        saveSettings(settings);
        syncToServer(settings);
        update();
      });
    });
  }

  function readInputs() {
    return {
      start: root.querySelector('[data-field="start"]').value || DEFAULTS.start,
      end: root.querySelector('[data-field="end"]').value || DEFAULTS.end,
      daily: root.querySelector('[data-field="daily"]').value
    };
  }

  /* ---------------- 实时刷新 ---------------- */

  function update() {
    if (!root || !els.earned) return;

    var settings = readInputs();
    var startClock = parseClock(settings.start);
    var endClock = parseClock(settings.end);
    var daily = Number(settings.daily);
    var now = new Date();

    // 参数不完整时给出提示，而不是显示错误的金额
    if (!startClock || !endClock || settings.daily === '' || !isFinite(daily) || daily < 0) {
      els.earned.textContent = '¥--';
      els.bar.style.width = '0%';
      els.percent.textContent = '已完成 --';
      els.worked.textContent = '已工作 --:--:--';
      els.left.textContent = '距离下班 --:--:--';
      els.breakdown.hidden = true;
      return;
    }

    var range = shiftRange(now, startClock, endClock);
    var totalSec = (range.end - range.start) / 1000;

    if (totalSec <= 0) {
      els.earned.textContent = '时间设置有误';
      els.breakdown.hidden = true;
      return;
    }

    var perSec = daily / totalSec;
    var workedSec = Math.min(Math.max((now - range.start) / 1000, 0), totalSec);
    var remainSec = totalSec - workedSec;
    var earned = perSec * workedSec;
    var percent = (workedSec / totalSec) * 100;

    // 叠加今日投资盈亏：行情页按 (最新价 - 昨收) × 股数 计算，
    // 已在上班时刻自然归零，这里直接相加即可
    var snapshot = window.__investmentSnapshot || { profit: 0, count: 0 };
    var investProfit = Number(snapshot.profit) || 0;
    var hasHolding = Number(snapshot.count) > 0;
    var total = earned + investProfit;

    els.earned.textContent = money(total, 4);
    els.bar.style.width = percent.toFixed(2) + '%';
    els.percent.textContent = '已完成 ' + percent.toFixed(2) + '%';
    els.worked.textContent = '已工作 ' + formatDuration(workedSec);
    els.left.textContent = remainSec <= 0 ? '今日已收工' : '距离下班 ' + formatDuration(remainSec);

    if (hasHolding) {
      els.breakdown.hidden = false;
      els.salaryPart.textContent = money(earned, 4);
      els.investPart.textContent = signedMoney(investProfit, 2);
      els.investPart.className = investProfit > 0 ? 'up' : (investProfit < 0 ? 'down' : '');
    } else {
      els.breakdown.hidden = true;
    }
  }

  /* ---------------- 启动 ---------------- */

  function start() {
    root = document.getElementById('salary-calculator');
    if (!root) return;

    render(loadSettings());
    update();
    loadFromServer();

    clearInterval(timer);
    timer = setInterval(update, TICK_MS);

    // 从后台切回来时立即刷新，避免显示旧金额
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) update();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
