const { contextBridge, ipcRenderer } = require('electron');

// 无边框窗口的最小化 / 关闭按钮由页面顶部栏触发
contextBridge.exposeInMainWorld('desktop', {
  minimize: () => ipcRenderer.send('window:minimize'),
  close: () => ipcRenderer.send('window:close'),
});
