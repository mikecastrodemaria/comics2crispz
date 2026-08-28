@echo off
REM comics2crispz - installation : venv + Pillow + config + livre d'exemple.
setlocal
cd /d "%~dp0"

where py >nul 2>&1
if errorlevel 1 ( set PYCMD=python ) else (
    py -3.10 -c "import sys" >nul 2>&1
    if errorlevel 1 ( set PYCMD=py ) else ( set PYCMD=py -3.10 )
)

if not exist ".venv\Scripts\python.exe" (
    echo [install] creating .venv ...
    %PYCMD% -m venv .venv
    if errorlevel 1 ( echo [install] venv creation FAILED & exit /b 1 )
)
echo [install] installing requirements ...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
if errorlevel 1 ( echo [install] pip install FAILED & exit /b 1 )

if not exist "config.json" (
    copy config-sample.json config.json >nul
    echo [install] config.json created from config-sample.json - edit the
    echo           'engines' paths/urls to match your crispz installs.
)

if not exist "books\exemple\project.json" (
    echo [install] building the example book ...
    ".venv\Scripts\python.exe" tools\make_example.py
)

echo.
echo [install] done. Start with:   run.bat books\exemple
endlocal
