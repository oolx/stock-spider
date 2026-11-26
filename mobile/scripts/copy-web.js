// 将项目前端源码复制到 Capacitor 的 webDir，保持单一来源
// 这里手写递归复制，兼容较老的 Node（不使用 fs.cpSync / fs.rmSync）
const fs = require('fs');
const path = require('path');

const mobileDir = path.join(__dirname, '..');
const projectRoot = path.join(mobileDir, '..');
const wwwDir = path.join(mobileDir, 'www');

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

rmrf(wwwDir);
fs.mkdirSync(wwwDir, { recursive: true });

['frontend', 'salary_calculation'].forEach(function (dir) {
  copyDir(path.join(projectRoot, dir), path.join(wwwDir, dir));
});

// Capacitor 要求 webDir 根目录存在 index.html，这里跳转到前端入口
fs.writeFileSync(
  path.join(wwwDir, 'index.html'),
  '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">' +
    '<meta http-equiv="refresh" content="0; url=frontend/index.html">' +
    '</head><body><a href="frontend/index.html">进入应用</a></body></html>\n'
);

console.log('web assets copied to', wwwDir);
