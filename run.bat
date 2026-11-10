@echo off
REM HR Promotion - desktop window mode (pywebview)
REM Falls back to a plain web server on http://127.0.0.1:5000 if pywebview is missing.
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

"%PY%" app.py
if errorlevel 1 (
    echo.
    echo [HR System] exited with an error - see hr_promotion.log
    pause
)
endlocal
