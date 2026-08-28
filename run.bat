@echo off
REM comics2crispz - lance le serveur sur un projet.
REM   run.bat books\exemple  [--port 8770]
cd /d "%~dp0"
if "%~1"=="" (
    echo usage: run.bat ^<dossier_projet^> [--port 8770]
    echo pas de projet ? : python tools\make_example.py
    exit /b 2
)
where py >nul 2>&1
if errorlevel 1 ( set PYCMD=python ) else ( set PYCMD=py -3.10 )
if exist ".venv\Scripts\python.exe" set PYCMD=.venv\Scripts\python.exe
%PYCMD% c2c_server.py %*
