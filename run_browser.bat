@echo off
REM HR Promotion - browser mode
REM Runs the Flask server without the pywebview window and opens the default browser
REM once the port is up. Close this console window (or press Ctrl+C) to stop the server.
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

"%PY%" -c "import threading, webbrowser, app; app.HAS_WEBVIEW = False; threading.Thread(target=lambda: app._wait_for_port(app.PORT) and webbrowser.open('http://127.0.0.1:' + str(app.PORT)), daemon=True).start(); app.main()"
if errorlevel 1 (
    echo.
    echo [HR System] exited with an error - see hr_promotion.log
    pause
)
endlocal
