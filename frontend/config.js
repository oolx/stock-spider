/* 前端运行时配置（浏览器与 Electron 共用）。
 *
 * 服务器地址优先级：
 *   1. window.STOCK_API_BASE（启动前注入）
 *   2. 用户在状态栏「实时连接」中保存的地址（localStorage）
 *   3. Electron 默认 127.0.0.1:8000；浏览器默认同源
 */
(function () {
  'use strict';

  var STORAGE_KEY = 'stock_server_v1';
  var DEFAULT_HOST = '127.0.0.1';
  var DEFAULT_PORT = '8000';
  var isDesktop = location.protocol === 'file:';

  function readSaved() {
    try {
      var raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      var data = JSON.parse(raw);
      if (!data || !data.host) return null;
      return {
        host: String(data.host),
        port: String(data.port || DEFAULT_PORT),
        tls: Boolean(data.tls),
      };
    } catch (e) {
      return null;
    }
  }

  function buildBase(server) {
    return (server.tls ? 'https' : 'http') + '://' + server.host + ':' + server.port;
  }

  function resolveApiBase() {
    if (window.STOCK_API_BASE) {
      return String(window.STOCK_API_BASE).replace(/\/+$/, '');
    }
    var saved = readSaved();
    if (saved) return buildBase(saved);
    return isDesktop ? buildBase({ host: DEFAULT_HOST, port: DEFAULT_PORT, tls: false }) : '';
  }

  function apply() {
    var apiBase = resolveApiBase();
    var httpBase = apiBase
      || (location.protocol === 'https:' ? 'https://' : 'http://') + location.host;
    window.APP_CONFIG.apiBase = apiBase;
    window.APP_CONFIG.wsBase = httpBase.replace(/^http/, 'ws');
  }

  window.APP_CONFIG = {
    isDesktop: isDesktop,
    apiBase: '',
    wsBase: '',
    server: {
      get: function () {
        return readSaved() || {
          host: DEFAULT_HOST,
          port: DEFAULT_PORT,
          tls: false,
        };
      },
      save: function (host, port, tls) {
        var data = {
          host: String(host || DEFAULT_HOST),
          port: String(port || DEFAULT_PORT),
          tls: Boolean(tls),
        };
        localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
        apply();
        return data;
      },
      reset: function () {
        localStorage.removeItem(STORAGE_KEY);
        apply();
      },
      isDefault: function () {
        return !readSaved() && !window.STOCK_API_BASE;
      },
    },
  };

  apply();

  // 桌面端标记：用于透明背景与窗口圆角等 Electron 专属样式
  if (isDesktop) {
    document.documentElement.classList.add('is-desktop');
  }
})();
