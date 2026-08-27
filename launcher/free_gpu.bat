@echo off
REM Double-click this to free the GPU for a game.
REM Pass /hard to stop ComfyUI and Ollama outright instead of just unloading.
setlocal
set HARD=
if /I "%~1"=="/hard" set HARD=-Hard
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0free_gpu.ps1" %HARD%
echo.
pause
