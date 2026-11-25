@echo off
chcp 65001 >nul
setlocal EnableExtensions
title A股实时行情 - 启动管理
cd /d "%~dp0"

rem ============================================================
rem  A股实时行情 - Windows 一键启动 / 停止 / 重启
rem  与 Linux 的 start.sh 行为一致：
rem    - 后台启动服务，PID 写入 run\app.pid
rem    - 输出追加写入 logs\app.log
rem    - 启动后自动检测端口是否就绪
rem    - 停止时先温和结束，超时后强制结束
rem
rem  用法：
rem    run.bat            # 默认执行 restart（停止后重新启动）
rem    run.bat start      # 启动
rem    run.bat stop       # 停止
rem    run.bat restart    # 重启
rem    run.bat status     # 查看状态
rem
rem  可选环境变量：
rem    ENV_NAME=stock-spider   conda 环境名
rem    HOST=127.0.0.1          监听地址
rem    PORT=8000               监听端口
rem    PYTHON=D:\path\python.exe  直接指定解释器
rem ============================================================

set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"
if not defined ENV_NAME set "ENV_NAME=stock-spider"
if not defined HOST set "HOST=127.0.0.1"
if not defined PORT set "PORT=8000"
set "PID_FILE=%APP_DIR%\run\app.pid"
set "LOG_FILE=%APP_DIR%\logs\app.log"

rem 保证控制台中文/日志输出正常
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

rem ---------- 解析动作 ----------
set "ACTION=%~1"
if "%ACTION%"=="" set "ACTION=restart"
if /i "%ACTION%"=="start" goto :do_start
if /i "%ACTION%"=="stop" goto :do_stop
if /i "%ACTION%"=="restart" goto :do_restart
if /i "%ACTION%"=="status" goto :do_status
echo 用法：%~nx0 {start^|stop^|restart^|status}
exit /b 1

:do_start
call :resolve_python
call :start_app
set "RC=%errorlevel%"
goto :finish

:do_stop
call :stop_app
set "RC=%errorlevel%"
goto :finish

:do_restart
call :resolve_python
call :stop_app
call :start_app
set "RC=%errorlevel%"
goto :restart_finish

:do_status
call :status_app
set "RC=%errorlevel%"
goto :finish

:finish
rem 双击运行时保留窗口，方便查看结果
if "%~1"=="" pause
exit /b %RC%

:restart_finish
if "%~1"=="" pause
exit /b %RC%

rem ============================================================
rem  以下为子流程
rem ============================================================

rem ---------- 选择 Python 解释器 ----------
:resolve_python
set "PY="
if defined PYTHON if exist "%PYTHON%" set "PY=%PYTHON%"
if not defined PY if defined CONDA_PREFIX if exist "%CONDA_PREFIX%\python.exe" set "PY=%CONDA_PREFIX%\python.exe"
if not defined PY if exist "%USERPROFILE%\anaconda3\envs\%ENV_NAME%\python.exe" set "PY=%USERPROFILE%\anaconda3\envs\%ENV_NAME%\python.exe"
if not defined PY if exist "%USERPROFILE%\miniconda3\envs\%ENV_NAME%\python.exe" set "PY=%USERPROFILE%\miniconda3\envs\%ENV_NAME%\python.exe"
if not defined PY if exist "%ProgramData%\Anaconda3\envs\%ENV_NAME%\python.exe" set "PY=%ProgramData%\Anaconda3\envs\%ENV_NAME%\python.exe"
if not defined PY if exist "%ProgramData%\Miniconda3\envs\%ENV_NAME%\python.exe" set "PY=%ProgramData%\Miniconda3\envs\%ENV_NAME%\python.exe"
if not defined PY (
  call :find_conda
  if defined CONDA_EXE (
    for /f "delims=" %%i in ('"%CONDA_EXE%" info --base 2^>nul') do set "CONDA_BASE=%%i"
  )
  if defined CONDA_BASE if exist "%CONDA_BASE%\envs\%ENV_NAME%\python.exe" set "PY=%CONDA_BASE%\envs\%ENV_NAME%\python.exe"
)
if not defined PY (
  for /f "delims=" %%i in ('where python 2^>nul') do (
    if not defined PY set "PY=%%i"
  )
)
exit /b 0

rem ---------- 定位 conda 可执行文件 ----------
:find_conda
set "CONDA_EXE="
if exist "%USERPROFILE%\anaconda3\Scripts\conda.exe" set "CONDA_EXE=%USERPROFILE%\anaconda3\Scripts\conda.exe"
if not defined CONDA_EXE if exist "%USERPROFILE%\miniconda3\Scripts\conda.exe" set "CONDA_EXE=%USERPROFILE%\miniconda3\Scripts\conda.exe"
if not defined CONDA_EXE if exist "%ProgramData%\Anaconda3\Scripts\conda.exe" set "CONDA_EXE=%ProgramData%\Anaconda3\Scripts\conda.exe"
if not defined CONDA_EXE if exist "%ProgramData%\Miniconda3\Scripts\conda.exe" set "CONDA_EXE=%ProgramData%\Miniconda3\Scripts\conda.exe"
if not defined CONDA_EXE (
  for /f "delims=" %%i in ('where conda.exe 2^>nul') do (
    if not defined CONDA_EXE set "CONDA_EXE=%%i"
  )
)
exit /b 0

rem ---------- 进程是否在运行 ----------
:is_running
set "PID="
if exist "%PID_FILE%" set /p PID=<"%PID_FILE%"
if not defined PID exit /b 1
powershell -NoProfile -Command "if (Get-Process -Id %PID% -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" >nul 2>&1
exit /b %errorlevel%

rem ---------- 端口是否就绪 ----------
:port_ready
"%PY%" -c "import socket,sys; s=socket.socket(); s.settimeout(1); sys.exit(0 if s.connect_ex(('%HOST%', %PORT%))==0 else 1)" >nul 2>&1
exit /b %errorlevel%

rem ---------- 启动 ----------
:start_app
call :is_running
if errorlevel 1 goto :start_do
echo [信息] 服务已在运行（PID %PID%），如需重启请执行：%~nx0 restart
exit /b 0

:start_do
if not defined PY (
  echo [错误] 未找到可用的 Python，请设置 PYTHON，或先运行 deploy.bat 创建 conda 环境：%ENV_NAME%
  exit /b 1
)
if not exist "%APP_DIR%\logs" mkdir "%APP_DIR%\logs"
if not exist "%APP_DIR%\run" mkdir "%APP_DIR%\run"
if not exist "%APP_DIR%\data" mkdir "%APP_DIR%\data"

echo [信息] 使用解释器：%PY%
echo [信息] 启动服务：http://%HOST%:%PORT%/

rem 后台启动：新开一个隐藏控制台运行 cmd /c，输出追加到 app.log，
rem 与 Linux 的 nohup ... >> app.log 2>&1 & 一致，关闭当前窗口不影响服务。
set "APP_PID="
for /f "usebackq delims=" %%i in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$q = [char]34; $cmd = '/c ' + $q + $q + '%PY%' + $q + ' -m uvicorn backend.main:app --host %HOST% --port %PORT% >> ' + $q + '%LOG_FILE%' + $q + ' 2>&1' + $q; $p = Start-Process -FilePath $env:ComSpec -ArgumentList $cmd -WorkingDirectory '%APP_DIR%' -WindowStyle Hidden -PassThru; $p.Id"`) do set "APP_PID=%%i"
if not defined APP_PID (
  echo [错误] 启动进程失败，请查看日志：%LOG_FILE%
  exit /b 1
)
> "%PID_FILE%" echo %APP_PID%

set /a WAIT=0
:start_wait
ping -n 2 127.0.0.1 >nul
call :is_running
if errorlevel 1 (
  echo [错误] 服务启动失败，日志：%LOG_FILE%
  if exist "%LOG_FILE%" powershell -NoProfile -Command "Get-Content -Path '%LOG_FILE%' -Tail 20"
  del /q "%PID_FILE%" >nul 2>&1
  exit /b 1
)
call :port_ready
if not errorlevel 1 (
  echo [成功] 服务已启动（PID %PID%）
  echo [信息] 日志：%LOG_FILE%
  exit /b 0
)
set /a WAIT+=1
if %WAIT% geq 30 (
  echo [警告] 进程已启动，但端口暂未就绪，请稍后查看日志：%LOG_FILE%
  exit /b 0
)
goto :start_wait

rem ---------- 停止 ----------
:stop_app
call :is_running
if errorlevel 1 (
  echo [信息] 服务未在运行
  del /q "%PID_FILE%" >nul 2>&1
  exit /b 0
)

echo [信息] 正在停止服务（PID %PID%）...
taskkill /PID %PID% /T >nul 2>&1
for /l %%k in (1,1,10) do (
  call :is_running
  if errorlevel 1 goto :stop_done
  ping -n 2 127.0.0.1 >nul
)
echo [警告] 进程未退出，强制结束
taskkill /F /PID %PID% /T >nul 2>&1

:stop_done
del /q "%PID_FILE%" >nul 2>&1
echo [成功] 服务已停止
exit /b 0

rem ---------- 状态 ----------
:status_app
call :is_running
if errorlevel 1 (
  echo [未运行]
  exit /b 0
)
echo [运行中] PID %PID%，地址 http://%HOST%:%PORT%/
exit /b 0
