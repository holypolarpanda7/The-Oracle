@echo off
title Oracle - Backend (DM brain)
rem %~dp0 is this launcher folder; the project root is one level up.
cd /d "%~dp0..\oracle-dm-backend"
"%~dp0..\.venv\Scripts\python.exe" fastapi-dm.py
set EXITCODE=%ERRORLEVEL%
echo.
echo ============================================================
echo  Backend stopped (exit code %EXITCODE%).
echo  If it crashed, read the error above.
echo ============================================================
pause >nul
