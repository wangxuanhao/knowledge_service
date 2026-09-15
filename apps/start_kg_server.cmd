@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   Start KG Explorer  http://127.0.0.1:8000
echo   (console will auto-close after launch)
echo ============================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_kg_server.ps1"
echo.
pause
