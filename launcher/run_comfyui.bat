@echo off
title Oracle - ComfyUI (image generation)
rem ComfyUI lives outside the project. Override its location with COMFYUI_HOME.
if "%COMFYUI_HOME%"=="" set "COMFYUI_HOME=D:\ComfyUI"
cd /d "%COMFYUI_HOME%"
".venv\Scripts\python.exe" main.py --listen 127.0.0.1 --port 8188
set EXITCODE=%ERRORLEVEL%
echo.
echo ============================================================
echo  ComfyUI stopped (exit code %EXITCODE%).
echo  Images will be skipped while this is down (game still runs).
echo ============================================================
pause >nul
