@echo off
rem Lumo AI for Windows: double-click to start.
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating the Python environment...
    py -3 -m venv .venv 2>nul || python -m venv .venv
    if errorlevel 1 (
        echo Python 3.9+ is required: https://www.python.org/downloads/  ^(tick "Add python.exe to PATH"^)
        pause
        exit /b 1
    )
)

rem Libraries are installed once (internet needed only the first time).
if not exist ".venv\.deps-ok" (
    echo Installing libraries...
    ".venv\Scripts\python.exe" -m pip install --default-timeout=120 --retries 10 -r requirements.txt
    if errorlevel 1 (
        echo Could not install the libraries. Check the internet connection and run start.bat again.
        pause
        exit /b 1
    )
    type nul > ".venv\.deps-ok"
)

".venv\Scripts\python.exe" run.py %*
pause
