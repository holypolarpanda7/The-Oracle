@echo off
title Oracle - Cloudflare Tunnel
rem Public HTTPS front door for the backend so Discord can load the Activity
rem iframe (Discord cannot reach localhost).
rem
rem Two modes:
rem   * NAMED tunnel (stable URL, recommended): set ORACLE_TUNNEL_NAME to your
rem     tunnel's name. The hostname never changes, so the Discord URL mapping is
rem     set once. Requires one-time setup (see docs/ACTIVITY_SETUP.md).
rem   * QUICK tunnel (fallback): no env var -> a NEW random *.trycloudflare.com
rem     URL each launch; the main launcher window prints it.
set "CF_LOG=%~dp0cloudflared_url.log"
del "%CF_LOG%" 2>nul

rem Resolve the cloudflared executable (PATH, else the winget/MSI location).
rem Done outside any (...) block so the "(x86)" path can't break parsing.
set "CF=cloudflared"
where cloudflared >nul 2>nul || set "CF=%ProgramFiles(x86)%\cloudflared\cloudflared.exe"

if defined ORACLE_TUNNEL_NAME goto named
"%CF%" tunnel --url http://localhost:8000 --logfile "%CF_LOG%"
goto stopped

:named
echo Running named tunnel "%ORACLE_TUNNEL_NAME%" ...
"%CF%" tunnel --logfile "%CF_LOG%" run "%ORACLE_TUNNEL_NAME%"

:stopped
set EXITCODE=%ERRORLEVEL%
echo.
echo ============================================================
echo  Cloudflare tunnel stopped (exit code %EXITCODE%).
echo  The Discord Activity is unreachable while this is down.
echo ============================================================
pause >nul
