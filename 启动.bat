@echo off
rem ================================================================
rem  Daily Review launcher - double-click to open the GUI window
rem  (ASCII-only body: cp936/GBK-safe; pythonw has no console flash)
rem ================================================================
set "PYW=E:\conda_envs\envs\mowan_dm\pythonw.exe"
if not exist "%PYW%" (
  echo pythonw.exe not found: %PYW%
  echo Please create the mowan_dm conda environment first.
  echo See the project docs for setup steps.
  pause
  exit /b 1
)
start "" "%PYW%" "%~dp0launcher.py"
exit /b 0
