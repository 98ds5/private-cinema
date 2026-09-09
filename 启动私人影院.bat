@echo off
rem ============================================================
rem  Private Cinema - double-click launcher
rem  Keep this file ASCII-only: cmd.exe reads .bat in the OEM
rem  codepage, so Chinese text inside it would garble.
rem ============================================================
setlocal

rem cd into the project dir: config.json and data\cinema.db are relative paths
cd /d "%~dp0"

set "PY=%~dp0cinema_env\pythonw.exe"
if not exist "%PY%" set "PY=%~dp0cinema_env\python.exe"

if not exist "%PY%" (
    echo.
    echo [ERROR] Python not found. Expected:
    echo         %~dp0cinema_env\pythonw.exe
    echo         %~dp0cinema_env\python.exe
    echo.
    echo The project-local virtual environment "cinema_env" seems to be missing.
    echo.
    pause
    exit /b 1
)

if not exist "%~dp0main.py" (
    echo.
    echo [ERROR] main.py not found in: %~dp0
    echo This .bat must stay in the project root, next to main.py.
    echo.
    pause
    exit /b 1
)

set PYTHONUTF8=1
set PYTHONUNBUFFERED=1

rem "start" IS required here: inside a batch file cmd.exe waits for a program
rem it invokes directly, even a GUI-subsystem one like pythonw.exe. Without
rem start the console window would sit there until the app is closed.
rem The leading "" is the window title -- omit it and start treats the first
rem quoted path as a title instead of a program.
rem
rem If it falls back to python.exe (console subsystem) you get a visible window
rem with the traceback, which is what you want when startup fails.
start "" "%PY%" "%~dp0main.py"

endlocal
