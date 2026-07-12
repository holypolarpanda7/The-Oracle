@echo off
rem Rebuilds "The Oracle.exe" from launch_oracle.py after launcher changes.
rem Run this on WINDOWS (double-click, or from a Windows terminal) - NOT from
rem inside WSL, which would produce a Linux binary instead of a .exe.
title Oracle - build launcher exe
cd /d "%~dp0"
echo Building The Oracle.exe with PyInstaller ...
uv run --with pyinstaller pyinstaller "The Oracle.spec"
set EXITCODE=%ERRORLEVEL%
echo.
if %EXITCODE%==0 (
  echo ============================================================
  echo  Done. New exe: %~dp0dist\The Oracle.exe
  echo ============================================================
) else (
  echo ============================================================
  echo  Build FAILED (exit code %EXITCODE%). Read the error above.
  echo ============================================================
)
pause >nul
