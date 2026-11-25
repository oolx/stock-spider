/* 前端运行时配置（浏览器与 Electron 共用）。
 *
 * - 浏览器访问：页面与 API 同源，使用相对路径；
 * - Electron（file://）：页面没有同源后端，固定连接本机 127.0.0.1:8000。
 *
 * 如需指向其他后端，可在加载本文件前设置 window.STOCK_API_BASE。
 */
(function () {
  'use strict';

  var isDesktop = location.protocol === 'file:';
  var apiBase = window.STOCK_API_BASE || (isDesktop ? 'http://127.0.0.1:8000' : '');
  if (apiBase.charAt(apiBase.length - 1) === '/') {
    apiBase = apiBase.slice(0, -1);
  }

  var httpBase = apiBase
    || (location.protocol === 'https:' ? 'https://' : 'http://') + location.host;

  window.APP_CONFIG = {
    isDesktop: isDesktop,
    apiBase: apiBase,
    wsBase: httpBase.replace(/^http/, 'ws'),
  };
})();
