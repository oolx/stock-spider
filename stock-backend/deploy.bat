@echo off
chcp 65001 >nul
setlocal EnableExtensions
title A股实时行情 - 一键部署
cd /d "%~dp0"

rem ============================================================
rem  一键部署：创建 conda 环境并安装 Python 依赖
rem  需要：Windows + 已安装 Anaconda / Miniconda
rem  说明：数据库使用 Python 自带的 SQLite，无需安装 Redis 等额外服务
rem ============================================================

set "ENV_NAME=stock-spider"
set "PY_VER=3.10"
rem 如需更换 pip 源，修改下一行；留空则使用官方源
set "PIP_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple"

echo ==========================================
echo   A股实时行情 - 一键部署
echo ==========================================
echo.

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
  goto :fail
)

for /f "delims=" %%i in ('"%CONDA_EXE%" info --base') do set "CONDA_BASE=%%i"
set "ENV_PY=%CONDA_BASE%\envs\%ENV_NAME%\python.exe"
echo [信息] conda 目录：%CONDA_BASE%
echo.

rem ---------- 创建 conda 环境 ----------
if exist "%ENV_PY%" (
  echo [1/2] 已存在环境 %ENV_NAME%，跳过创建。
) else (
  echo [1/2] 正在创建 conda 环境 %ENV_NAME% ^(Python %PY_VER%^) ...
  "%CONDA_EXE%" create -y -n %ENV_NAME% -c conda-forge --override-channels python=%PY_VER% pip
  if errorlevel 1 goto :fail
)

rem ---------- 安装 Python 依赖 ----------
echo [2/2] 正在安装 Python 依赖 ...
"%ENV_PY%" -m pip install --upgrade pip --disable-pip-version-check
if defined PIP_MIRROR (
  "%ENV_PY%" -m pip install -r "%~dp0requirements.txt" -i %PIP_MIRROR% --disable-pip-version-check
) else (
  "%ENV_PY%" -m pip install -r "%~dp0requirements.txt" --disable-pip-version-check
)
if errorlevel 1 (
  echo [警告] 镜像源安装失败，改用官方源重试 ...
  "%ENV_PY%" -m pip install -r "%~dp0requirements.txt" --disable-pip-version-check
  if errorlevel 1 goto :fail
)

rem ---------- 自检 ----------
"%ENV_PY%" -c "import sqlite3, fastapi, uvicorn, requests, pandas; print('依赖自检通过，SQLite', sqlite3.sqlite_version)"
if errorlevel 1 goto :fail

echo.
echo ==========================================
echo   部署完成！
echo   双击 run.bat 即可启动服务。
echo   访问地址：http://127.0.0.1:8000/
echo ==========================================
pause
exit /b 0

:fail
echo.
echo [失败] 部署未完成，请根据上面的错误信息排查后重试。
pause
exit /b 1
