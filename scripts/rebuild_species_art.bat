@echo off
REM Re-render every species portrait in its own console, with a progress bar.
REM Uses the WINDOWS venv on purpose: ComfyUI is a Windows process and the WSL
REM interpreter cannot reach it (see CLAUDE.md -> Environment).
title Oracle - species portrait re-render
cd /d "%~dp0.."
".venv\Scripts\python.exe" "scripts\rebuild_species_art.py"
echo.
echo Finished. Press any key to close this window.
pause >nul
