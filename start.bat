@echo off
REM comics2crispz - one-click start: serves the most recently edited book
REM under books\ (or the folder you pass) and opens the browser.
REM   start.bat                     REM latest book
REM   start.bat books\plein-ecran   REM a specific book
REM   start.bat --port 8771         REM second book side by side
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo [start] no .venv yet - running install.bat first...
    call install.bat || exit /b 1
)
".venv\Scripts\python.exe" c2c_server.py --open %*
