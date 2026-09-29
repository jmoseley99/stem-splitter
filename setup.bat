@echo off
REM Stem Splitter - one-time setup (double-click me first).
REM Runs the PowerShell setup script with the right permissions so you don't
REM have to open a terminal or change any settings.

cd /d "%~dp0"

echo ============================================
echo   Stem Splitter - first-time setup
echo ============================================
echo.
echo This installs everything needed to run the app.
echo It can take several minutes and needs an internet connection.
echo.
pause

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"

echo.
echo ============================================
echo   Setup finished. To start the app, double-click run.bat
echo ============================================
echo.
pause
