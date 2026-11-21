@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0..\..\.."
set "PY="
for /f "delims=" %%P in ('where python 2^>nul ^| findstr /I /V /L /C:"\WindowsApps\"') do (
    if not defined PY set "PY=%%P"
)
if not defined PY (
    echo Не нашёл Python. Откройте python-setup.html.
    pause
    exit /b
)
"%PY%" "tools\startup_check.py"
echo.
pause
