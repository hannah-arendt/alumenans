@echo off
rem Diagnostic launcher. Starts the program exactly the way the shortcut
rem does - through pythonw, without a console - and, if no window opens
rem in 45 seconds, writes startup_stack.txt with the stacks of every
rem thread. Crashes go into the same file: without a console there is
rem nowhere else for them to go.
rem
rem ASCII only inside the commands on purpose: cmd breaks on Cyrillic
rem labels and variable names.

setlocal
rem The package lives in ..\..\..\src; the report is written there too.
cd /d "%~dp0..\..\..\src"

set "PYW="
for /f "delims=" %%P in ('where pythonw 2^>nul ^| findstr /I /V /L /C:"\WindowsApps\"') do (
    if not defined PYW set "PYW=%%P"
)
if not defined PYW (
    echo pythonw not found.
    pause
    exit /b 1
)

if exist "startup_stack.txt" del "startup_stack.txt"

echo Using: %PYW%
echo.
echo Starting the program the same way the shortcut does.
echo If a window opens, close it - nothing is wrong.
echo If nothing opens, wait 45 seconds; this window will report.
echo.
"%PYW%" -m alumenans.startup_stack
echo.
echo ------------------------------------------------------------
if exist "startup_stack.txt" (
    echo Report written to: %CD%\startup_stack.txt
    echo.
    type "startup_stack.txt"
) else (
    echo No report - the program started and closed normally.
)
echo.
pause
