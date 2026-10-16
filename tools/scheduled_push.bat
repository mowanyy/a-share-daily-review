@echo off
rem ================================================================
rem  scheduled_push.bat <review|plan|open>
rem  Entry point for Windows Task Scheduler (v0.22): run a Feishu push
rem  punctually from this machine, bypassing GitHub Actions cron delays.
rem  Logs go to output\scheduled_push_<type>.log (append).
rem  v0.38.7: ASCII only (cmd parses .bat in system codepage GBK; any
rem  UTF-8 non-ASCII byte desyncs the parser). Added plain-text trail
rem  lines at entry/exit/fail so every trigger is visible in the log,
rem  including runs that die before reaching python (9/22-9/23 issue).
rem ================================================================
setlocal
set "TYPE=%~1"
if "%TYPE%"=="" (
  echo usage: scheduled_push.bat review^|plan^|open
  exit /b 1
)
set "ROOT=%~dp0.."
set "PY=E:\conda_envs\envs\mowan_dm\python.exe"
set "LOG=%ROOT%\output\scheduled_push_%TYPE%.log"
if not exist "%ROOT%\output" mkdir "%ROOT%\output" 2>nul
echo ===== [%date% %time%] TRIGGER scheduled_push.bat type=%TYPE% ===== >> "%LOG%" 2>nul
cd /d "%ROOT%"
if not exist "%PY%" (
  echo python.exe not found: %PY%
  echo ===== [%date% %time%] FAIL python.exe not found ===== >> "%LOG%" 2>nul
  exit /b 1
)
set "PYTHONPATH=src"
set "PYTHONIOENCODING=utf-8"
"%PY%" -m daily_review push --type "%TYPE%" >> "%LOG%" 2>&1
set "RC=%errorlevel%"
echo ===== [%date% %time%] DONE exit=%RC% ===== >> "%LOG%" 2>nul
exit /b %RC%