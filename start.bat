@echo off
setlocal

cd /d "%~dp0"

set "PY_CMD="
if exist ".venv\Scripts\python.exe" set "PY_CMD=.venv\Scripts\python.exe"
if not defined PY_CMD where python >nul 2>&1 && set "PY_CMD=python"
if not defined PY_CMD where py >nul 2>&1 && set "PY_CMD=py -3"
if not defined PY_CMD where python3 >nul 2>&1 && set "PY_CMD=python3"

if not defined PY_CMD (
    echo [ERROR] Python was not found on PATH.
    echo Install Python 3 and try again.
    pause
    exit /b 1
)

set "RUN_CMD=%PY_CMD% app.py"

echo Starting MeetBot server...
start "MeetBot Server" cmd /k "%RUN_CMD%"
timeout /t 3 >nul
start "" "http://127.0.0.1:5001/dashboard"

exit /b 0
