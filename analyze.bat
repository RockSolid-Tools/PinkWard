@echo off
REM Quick launcher: scans the system drive and opens the dashboard in your browser.
REM You can pass other paths:  analyze.bat D:\   or   analyze.bat C:\ D:\
setlocal
REM What you typed goes through as it is. The default is deliberately not
REM quoted below: "C:\" reaches Python as C:" because the backslash escapes
REM the quote, and the scan then looks for a folder that does not exist.
set "TARGETS=%*"
if "%~1"=="" set "TARGETS=%SystemDrive%\"

REM "py" comes with the official Python installer. Windows also ships a fake
REM "python" that only opens the Microsoft Store, so check that it really runs.
set PY=
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY (python --version >nul 2>nul && set "PY=python")
if not defined PY (
  echo Python was not found. Install it from https://www.python.org/downloads/
  echo and run this file again.
  pause
  exit /b 1
)

%PY% "%~dp0pinkward.py" %TARGETS% --open
if errorlevel 1 (
  echo.
  echo The scan could not be completed.
  pause
  exit /b 1
)
