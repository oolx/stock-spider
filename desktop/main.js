const { app, BrowserWindow, Menu, dialog, ipcMain } = require('electron');
const path = require('path');
const http = require('http');

// 后端由 Python 环境单独启动（./start.sh start），桌面端只负责连接
const BACKEND_URL = process.env.STOCK_BACKEND_URL || 'http://127.0.0.1:8000';
const WINDOW_WIDTH = 380;
const WINDOW_HEIGHT = 540;

let win = null;
let dialogOpen = false;

function rendererEntry() {
  // 开发时直接加载项目 frontend；打包后从 resources/renderer 加载
  const base = app.isPackaged
    ? path.join(process.resourcesPath, 'renderer')
    : path.join(__dirname, '..');
  return path.join(base, 'frontend', 'index.html');
}

function iconPath() {
  return app.isPackaged
    ? path.join(process.resourcesPath, 'icon.png')
    : path.join(__dirname, 'build', 'icon.png');
}

function createWindow() {
  win = new BrowserWindow({
    width: WINDOW_WIDTH,
    height: WINDOW_HEIGHT,
    useContentSize: true,
    resizable: false,
    maximizable: false,
    fullscreenable: false,
    title: 'A股实时行情',
    icon: iconPath(),
    // 透明窗口 + 页面圆角，营造类似 Windows 11 的圆角外观
    backgroundColor: '#00000000',
    transparent: true,
    frame: false,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  win.once('ready-to-show', () => win.show());
  win.on('closed', () => {
    win = null;
  });
  win.loadFile(rendererEntry());
}

function probeBackend() {
  return new Promise(resolve => {
    const req = http.get(BACKEND_URL + '/api/symbols', { timeout: 1500 }, res => {
      res.resume();
      resolve(res.statusCode >= 200 && res.statusCode < 500);
    });
    req.on('timeout', () => {
      req.destroy();
      resolve(false);
    });
    req.on('error', () => resolve(false));
  });
}

async function ensureBackend() {
  for (;;) {
    if (await probeBackend()) return true;
    if (dialogOpen) return false;

    dialogOpen = true;
    const result = await dialog.showMessageBox(win, {
      type: 'warning',
      title: '后端未启动',
      message: '无法连接 Python 后端服务',
      detail: '请先在项目目录执行：\n./start.sh start\n\n服务地址：' + BACKEND_URL,
      buttons: ['重试', '稍后'],
      defaultId: 0,
      cancelId: 1,
    });
    dialogOpen = false;

    if (result.response !== 0) return false;
  }
}

app.whenReady().then(() => {
  Menu.setApplicationMenu(null);

  ipcMain.on('window:minimize', () => {
    if (win) win.minimize();
  });
  ipcMain.on('window:close', () => {
    if (win) win.close();
  });

  createWindow();
  win.webContents.on('did-finish-load', ensureBackend);

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  app.quit();
});
