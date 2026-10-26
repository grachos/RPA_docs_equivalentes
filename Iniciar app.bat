@echo off
rem Starts the desktop app without a console window.
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" -m app.ui
) else (
    start "" pythonw -m app.ui
)
