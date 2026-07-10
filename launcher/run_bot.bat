@echo off
title Oracle - Discord Bot + Music
rem %~dp0 is this launcher folder; the project root is one level up.
cd /d "%~dp0..\ai-dm-sicord-bot"
"%~dp0..\.venv\Scripts\python.exe" oracle-dm-discord-bot.py
set EXITCODE=%ERRORLEVEL%
echo.
echo ============================================================
echo  Discord bot stopped (exit code %EXITCODE%).
echo  If it crashed, read the error above.
echo ============================================================
pause >nul
