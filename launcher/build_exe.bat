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
  rem The desktop carries a COPY of the exe (not a shortcut), so refresh it
  rem whenever one is present - otherwise rebuilds never reach the desktop.
  if exist "%USERPROFILE%\OneDrive\Desktop\The Oracle.exe" (
    copy /y "%~dp0dist\The Oracle.exe" "%USERPROFILE%\OneDrive\Desktop\The Oracle.exe" >nul
    echo  Desktop copy updated: %USERPROFILE%\OneDrive\Desktop\The Oracle.exe
  )
  if exist "%USERPROFILE%\Desktop\The Oracle.exe" (
    copy /y "%~dp0dist\The Oracle.exe" "%USERPROFILE%\Desktop\The Oracle.exe" >nul
    echo  Desktop copy updated: %USERPROFILE%\Desktop\The Oracle.exe
  )
  echo ============================================================
) else (
  echo ============================================================
  echo  Build FAILED (exit code %EXITCODE%). Read the error above.
  echo ============================================================
)
pause >nul
