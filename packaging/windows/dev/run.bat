@echo off
setlocal
rem Runs the program from a source checkout: the package lives in ..\..\..\src.
cd /d "%~dp0..\..\..\src"
set "PYTHONW="
for /f "delims=" %%P in ('where pythonw 2^>nul ^| findstr /I /V /L /C:"\WindowsApps\"') do (
    if not defined PYTHONW set "PYTHONW=%%P"
)
if not defined PYTHONW goto NOPYTHON
start "" "%PYTHONW%" -m alumenans
exit /b

:NOPYTHON
start "" "%~dp0python-setup.html"
exit /b
