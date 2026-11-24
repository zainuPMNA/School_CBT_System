@echo off
REM ==========================================================================
REM  CBT Exam App - double-click this file to start everything.
REM  * First time: sets up Python packages automatically (needs internet once)
REM  * Starts the exam server and opens it in your web browser
REM  * Other PCs on the same LAN / Wi-Fi can open the address shown below
REM  * To stop the app: close this window (or press Ctrl+C)
REM ==========================================================================
title CBT Exam App - keep this window open while using the app
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
    where python >nul 2>nul
    if errorlevel 1 (
        echo.
        echo  Python is not installed on this PC.
        echo  Install Python 3.12 or newer from https://www.python.org/downloads/
        echo  ^(tick "Add python.exe to PATH" during setup^), then double-click this file again.
        echo.
        pause
        exit /b 1
    )
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_windows.ps1" %*

echo.
echo  The CBT app has stopped. You can close this window.
pause >nul
