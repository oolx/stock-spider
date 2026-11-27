// 将项目前端源码同步到各平台的 Web 资源目录，保证 frontend/ 是唯一来源
// 这里手写递归复制，兼容较老的 Node（不使用 fs.cpSync / fs.rmSync）
const fs = require('fs');
const path = require('path');

const mobileDir = path.join(__dirname, '..');
const projectRoot = path.join(mobileDir, '..');
const wwwDir = path.join(mobileDir, 'www');
// Capacitor 原生工程的 Web 资源目录（构建 APK 时直接打包这里的文件）
const androidPublicDir = path.join(mobileDir, 'android', 'app', 'src', 'main', 'assets', 'public');

// Capacitor 要求 webDir 根目录存在 index.html，这里跳转到前端入口
const INDEX_HTML =
  '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">' +
  '<meta http-equiv="refresh" content="0; url=frontend/index.html">' +
  '</head><body><a href="frontend/index.html">进入应用</a></body></html>\n';

const SOURCE_DIRS = ['frontend', 'salary_calculation'];

function rmrf(target) {
  if (!fs.existsSync(target)) return;
  fs.readdirSync(target).forEach(function (name) {
    const current = path.join(target, name);
    if (fs.lstatSync(current).isDirectory()) rmrf(current);
    else fs.unlinkSync(current);
  });
  fs.rmdirSync(target);
}

function copyDir(src, dest) {
  fs.mkdirSync(dest, { recursive: true });
  fs.readdirSync(src).forEach(function (name) {
    const from = path.join(src, name);
    const to = path.join(dest, name);
    if (fs.statSync(from).isDirectory()) copyDir(from, to);
    else fs.writeFileSync(to, fs.readFileSync(from));
  });
}

function syncTo(targetDir) {
  SOURCE_DIRS.forEach(function (dir) {
    const dest = path.join(targetDir, dir);
    rmrf(dest);
    copyDir(path.join(projectRoot, dir), dest);
  });
  fs.writeFileSync(path.join(targetDir, 'index.html'), INDEX_HTML);
}

// 1. Capacitor 的 webDir（www/）
rmrf(wwwDir);
fs.mkdirSync(wwwDir, { recursive: true });
syncTo(wwwDir);
console.log('web assets copied to', wwwDir);

// 2. Android 原生工程的 assets/public（已存在才同步，避免误建空工程）
if (fs.existsSync(androidPublicDir)) {
  syncTo(androidPublicDir);
  console.log('android assets synced to', androidPublicDir);
}
