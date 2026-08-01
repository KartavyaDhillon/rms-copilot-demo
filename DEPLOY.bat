@echo off
setlocal enabledelayedexpansion
title RMS Copilot - Deploy

REM ===========================================================================
REM DEPLOY.bat - pull the latest code onto THIS machine and verify it.
REM
REM Run this on the SERVER, not the development machine.
REM
REM What it does, in order:
REM   1. Refuses to run if a pipeline is currently executing
REM   2. git pull
REM   3. pip install, but only if requirements.txt actually changed
REM   4. python tools\doctor.py  - and STOPS if anything failed
REM   5. Tells you to restart the server
REM
REM What it deliberately does NOT do: touch .env.local. That file is this
REM machine's own config and is gitignored, so a deploy can never clobber it.
REM ===========================================================================

cd /d "%~dp0"
echo.
echo ==========================================================================
echo   RMS Copilot - Deploy
echo   %CD%
echo ==========================================================================
echo.

REM -- 1. Is anything running right now? --------------------------------------
REM Deploying mid-run swaps modules under a live pipeline. Python has already
REM imported what it needs, but anything imported LATER in the run comes from
REM the new code - a genuinely nasty class of bug. So: check first.
tasklist /FI "IMAGENAME eq python.exe" 2>nul | find /I "python.exe" >nul
if not errorlevel 1 (
  echo   [!] python.exe is running.
  echo.
  echo       If that is the RMS server with a pipeline mid-run, deploying now
  echo       can swap code underneath it. Stop the server first.
  echo.
  set /p GO="       Continue anyway? (type YES to proceed): "
  if /I not "!GO!"=="YES" (
    echo.
    echo   Aborted. Nothing was changed.
    pause
    exit /b 1
  )
)

REM -- 2. Record requirements.txt state so we can tell if it changed ----------
set "REQ_BEFORE="
if exist requirements.txt (
  for /f "delims=" %%H in ('certutil -hashfile requirements.txt MD5 ^| find /v ":"') do (
    if not defined REQ_BEFORE set "REQ_BEFORE=%%H"
  )
)

REM -- 3. Pull ---------------------------------------------------------------
echo   Pulling latest code...
echo.
git pull
if errorlevel 1 (
  echo.
  echo   [X] git pull failed. Nothing else was done.
  echo       Common cause: local edits on this machine conflicting with the
  echo       incoming changes. This box is meant to be deploy-only - if you
  echo       edited code here, move those edits to the dev machine.
  pause
  exit /b 1
)

REM -- 4. Reinstall dependencies only if requirements actually changed --------
set "REQ_AFTER="
if exist requirements.txt (
  for /f "delims=" %%H in ('certutil -hashfile requirements.txt MD5 ^| find /v ":"') do (
    if not defined REQ_AFTER set "REQ_AFTER=%%H"
  )
)

if exist .venv\Scripts\activate.bat (
  call .venv\Scripts\activate.bat
) else (
  echo   [!] No .venv found - using system Python.
)

if not "%REQ_BEFORE%"=="%REQ_AFTER%" (
  echo.
  echo   requirements.txt changed - installing. This can take a while.
  echo.
  pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo   [X] pip install failed.
    echo       If it complained about the file encoding, requirements.txt has
    echo       been saved as UTF-16 again. Re-save it as UTF-8 on the dev
    echo       machine and commit that.
    pause
    exit /b 1
  )
) else (
  echo   requirements.txt unchanged - skipping install.
)

REM -- 5. Verify -------------------------------------------------------------
echo.
echo   Running preflight check...
echo.
python tools\doctor.py
if errorlevel 1 (
  echo.
  echo   ======================================================================
  echo   [X] PREFLIGHT FAILED - do NOT start the server.
  echo.
  echo       Fix what doctor.py listed above, then run this again.
  echo       If a new process was added and needs new folders, regenerate
  echo       this machine's config:
  echo.
  echo           python tools\make_env_local.py --root %%RMS_DATA_ROOT%% --reuse
  echo   ======================================================================
  pause
  exit /b 1
)

echo.
echo ==========================================================================
echo   Deploy complete and preflight passed.
echo.
echo   Restart the server to load the new code:
echo     - stop the running START_SERVER window (Ctrl+C), then
echo     - start it again, or let the scheduled task pick it up.
echo ==========================================================================
echo.
pause
