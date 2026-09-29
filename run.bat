@echo off
REM Stem Splitter - one-click launcher.
REM Double-click this file to start the app in your browser.

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo Virtual environment not found.
    echo Run setup first:  powershell -ExecutionPolicy Bypass -File setup.ps1
    echo.
    pause
    exit /b 1
)

echo Starting Stem Splitter...
echo Your browser should open at http://localhost:8501
echo Close this window (or press Ctrl+C) to stop the app.
echo.

".venv\Scripts\python.exe" -m streamlit run app.py

pause
