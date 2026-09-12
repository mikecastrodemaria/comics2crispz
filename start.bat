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
REM requirements.txt changed since the last install (new optional deps such
REM as rembg)? bring the venv up to date before serving.
fc /b requirements.txt ".venvequirements.stamp" >nul 2>&1
if errorlevel 1 (
    echo [start] requirements.txt changed - updating the venv...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
    if errorlevel 1 ( echo [start] pip install FAILED & exit /b 1 )
    copy /y requirements.txt ".venvequirements.stamp" >nul
)
".venv\Scripts\python.exe" c2c_server.py --open %*
