@echo off
rem ================================================================
rem  scheduled_push.bat <review|plan|open>
rem  Entry point for Windows Task Scheduler (v0.22): run a Feishu push
rem  punctually from this machine, bypassing GitHub Actions cron delays.
rem  Logs go to output\scheduled_push_<type>.log (append).
rem ================================================================
setlocal
set "TYPE=%~1"
if "%TYPE%"=="" (
  echo usage: scheduled_push.bat review^|plan^|open
  exit /b 1
)
set "ROOT=%~dp0.."
cd /d "%ROOT%"
if not exist "output" mkdir "output"
set "PY=E:\conda_envs\envs\mowan_dm\python.exe"
if not exist "%PY%" (
  echo python.exe not found: %PY%
  exit /b 1
)
set "PYTHONPATH=src"
set "PYTHONIOENCODING=utf-8"
"%PY%" -m daily_review push --type "%TYPE%" >> "output\scheduled_push_%TYPE%.log" 2>&1
exit /b %errorlevel%