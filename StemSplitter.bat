@echo off
REM ============================================================
REM   Stem Splitter - one click to set up (first time) and run.
REM   Double-click this file. Nothing else needed.
REM ============================================================

cd /d "%~dp0"

REM First run: no virtual environment yet -> do the one-time setup.
if not exist ".venv\Scripts\python.exe" (
    echo First-time setup. This installs everything and takes a few minutes.
    echo.
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
    if errorlevel 1 (
        echo.
        echo Setup did not complete. Please see the messages above.
        pause
        exit /b 1
    )
)

echo Starting Stem Splitter... your browser will open shortly.
echo Close this window when you're done.
echo.

".venv\Scripts\python.exe" -m streamlit run app.py

pause
