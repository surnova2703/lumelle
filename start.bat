@echo off
rem Lumelle AI for Windows: double-click to start.
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
copy /b /y requirements.txt+requirements-voice.txt ".venv\.deps-new" >nul
fc /b ".venv\.deps-new" ".venv\.deps-ok" >nul 2>&1
if errorlevel 1 (
    echo Installing libraries...
    ".venv\Scripts\python.exe" -m pip install --default-timeout=120 --retries 2 -r requirements.txt
    if errorlevel 1 goto noinstall
    ".venv\Scripts\python.exe" -m pip install --default-timeout=120 --retries 2 -r requirements-voice.txt || echo Voice input ^(Whisper^) was not installed. Everything else works.
    copy /y ".venv\.deps-new" ".venv\.deps-ok" >nul
)
goto run

:noinstall
".venv\Scripts\python.exe" -c "import fastapi, uvicorn, httpx" >nul 2>&1
if errorlevel 1 (
    echo Could not install the libraries. Check the internet connection and run start.bat again.
    pause
    exit /b 1
)
echo New libraries could not be installed ^(no internet?^). Lumelle starts anyway; try again next time online.

:run

".venv\Scripts\python.exe" run.py %*
pause
