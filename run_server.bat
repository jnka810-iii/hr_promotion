@echo off
REM HR Promotion - server for the local network (LAN)
REM Other machines on the same network open http://<this machine's IP>:5000
REM WARNING: there is no login. Anyone who can reach port 5000 can read, edit and DELETE all data.
REM Close this console window (or press Ctrl+C) to stop the server.
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set HR_HOST=0.0.0.0

if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

echo IP addresses of this machine:
ipconfig | findstr /c:"IPv4"
echo.

"%PY%" -c "import app; app.HAS_WEBVIEW = False; app.main()"
if errorlevel 1 (
    echo.
    echo [HR System] exited with an error - see hr_promotion.log
    pause
)
endlocal
