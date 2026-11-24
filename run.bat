@echo off
chcp 65001 >nul
setlocal EnableExtensions
title A股实时行情 - 运行中
cd /d "%~dp0"

rem ============================================================
rem  一键运行：使用 deploy.bat 创建的 conda 环境启动服务
rem  关闭此窗口即可停止服务
rem ============================================================

set "ENV_NAME=stock-spider"
set "HOST=127.0.0.1"
set "PORT=8000"

rem ---------- 定位 conda 可执行文件 ----------
set "CONDA_EXE="
if exist "%USERPROFILE%\anaconda3\Scripts\conda.exe" set "CONDA_EXE=%USERPROFILE%\anaconda3\Scripts\conda.exe"
if not defined CONDA_EXE if exist "%USERPROFILE%\miniconda3\Scripts\conda.exe" set "CONDA_EXE=%USERPROFILE%\miniconda3\Scripts\conda.exe"
if not defined CONDA_EXE if exist "%ProgramData%\Anaconda3\Scripts\conda.exe" set "CONDA_EXE=%ProgramData%\Anaconda3\Scripts\conda.exe"
if not defined CONDA_EXE if exist "%ProgramData%\Miniconda3\Scripts\conda.exe" set "CONDA_EXE=%ProgramData%\Miniconda3\Scripts\conda.exe"
if not defined CONDA_EXE (
  for /f "delims=" %%i in ('where conda.exe 2^>nul') do (
    set "CONDA_EXE=%%i"
    goto :conda_found
  )
)
:conda_found
if not defined CONDA_EXE (
  echo [错误] 未找到 conda，请先安装 Anaconda 或 Miniconda 后重试。
  pause
  exit /b 1
)

for /f "delims=" %%i in ('"%CONDA_EXE%" info --base') do set "CONDA_BASE=%%i"
set "ENV_PY=%CONDA_BASE%\envs\%ENV_NAME%\python.exe"

if not exist "%ENV_PY%" (
  echo [错误] 未找到运行环境 %ENV_NAME%，请先双击 deploy.bat 完成部署。
  pause
  exit /b 1
)

rem 保证控制台中文/日志输出正常
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

echo ==========================================
echo   A股实时行情 正在启动 ...
echo   访问地址：http://%HOST%:%PORT%/
echo   关闭此窗口即可停止服务。
echo ==========================================
echo.

rem 稍等服务启动后，用浏览器 kiosk 模式打开（真全屏，无地址栏、标签页、窗口按钮）
rem 注意：kiosk 会强制全屏，380x300 尺寸与右下角定位不再生效；退出按 Alt+F4
rem 优先 Chrome，其次 Edge，最后 fallback 到系统默认浏览器
start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 3; $url='http://%HOST%:%PORT%/'; $chrome='C:\Program Files\Google\Chrome\Application\chrome.exe'; $edge='C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'; if (Test-Path $chrome) { Start-Process $chrome ('--kiosk=' + $url) } elseif (Test-Path $edge) { Start-Process $edge ('--kiosk=' + $url) } else { Start-Process $url }"

"%ENV_PY%" -m uvicorn backend.main:app --host %HOST% --port %PORT%

echo.
echo 服务已停止。
pause
