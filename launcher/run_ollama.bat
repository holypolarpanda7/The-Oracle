@echo off
title Oracle - Ollama (local LLM)
rem Ollama serves the local model on port 11434. The launcher only runs this
rem when no Ollama is already listening, so `serve` won't collide with an
rem instance you started yourself for other work.
rem
rem Optional: uncomment to size the context server-side instead of per-request
rem (the backend already sends num_ctx via LLM_NUM_CTX, so this is not required).
rem set "OLLAMA_CONTEXT_LENGTH=16384"

where ollama >nul 2>nul
if %ERRORLEVEL%==0 (
  ollama serve
) else (
  "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" serve
)
set EXITCODE=%ERRORLEVEL%
echo.
echo ============================================================
echo  Ollama stopped (exit code %EXITCODE%).
echo  The LLM is down while this is closed (the DM can't narrate).
echo ============================================================
pause >nul
