@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "JUDGE_PY_ARGS="
if defined JUDGE_PYTHON (
  if exist "%JUDGE_PYTHON%" goto python_ready
  echo JUDGE_PYTHON 指定的解释器不存在，请设置为 python.exe 的完整路径。
  pause
  exit /b 1
)
if exist "%~dp0.venv\Scripts\python.exe" (
  set "JUDGE_PYTHON=%~dp0.venv\Scripts\python.exe"
  goto python_ready
)
if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
  set "JUDGE_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
  goto python_ready
)
where py >nul 2>&1
if not errorlevel 1 (
  py -3 -c "import sys;sys.exit(0 if sys.version_info >= (3,12) else 1)" >nul 2>&1
  if not errorlevel 1 (
    set "JUDGE_PYTHON=py"
    set "JUDGE_PY_ARGS=-3"
    goto python_ready
  )
)
where python >nul 2>&1
if not errorlevel 1 (
  set "JUDGE_PYTHON=python"
  goto python_ready
)
echo 未找到 Python 3.12 或更新版本。请安装 Python，或设置 JUDGE_PYTHON。
pause
exit /b 1
:python_ready
"%JUDGE_PYTHON%" %JUDGE_PY_ARGS% -c "import sys;sys.exit(0 if sys.version_info >= (3,12) else 1)" >nul 2>&1
if errorlevel 1 (
  echo 此入口需要 Python 3.12 或更新版本，请检查 README。
  pause
  exit /b 1
)
"%JUDGE_PYTHON%" %JUDGE_PY_ARGS% manage.py start
if errorlevel 1 goto done
echo 已请求启动，请使用“查看状态”确认 API 健康与公网地址。
echo 关闭此窗口不会停止后台服务；请按课程安排保持电脑联网。
:done
pause
