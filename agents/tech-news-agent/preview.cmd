@echo off
where pwsh >nul 2>nul
if errorlevel 1 (
  echo PowerShell 7 is required. Add pwsh to PATH and try again.
  pause
  exit /b 1
)
pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-agent.ps1" -Mode preview
pause
