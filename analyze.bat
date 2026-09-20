@echo off
REM Quick launcher: scans the system drive and opens the dashboard in your browser.
REM You can pass another path:  analyze.bat D:\
setlocal
set TARGET=%~1
if "%TARGET%"=="" set TARGET=%SystemDrive%\

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

%PY% "%~dp0pinkward.py" "%TARGET%" --open
if errorlevel 1 (
  echo.
  echo The scan could not be completed.
  pause
  exit /b 1
)
