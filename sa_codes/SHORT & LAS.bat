@echo off
setlocal ENABLEDELAYEDEXPANSION

REM ==================================================
REM CONFIG
REM ==================================================
set PYTHON=python
set BASE_DIR=C:\KARTAVYA\SHORT ALLOCATION

set CODE1=C:\DO NOT DELETE CODES\newshortallocationfiledownloader.py
set CODE2=C:\DO NOT DELETE CODES\shortallocation_final_new_with_co.py
set CODE3=C:\DO NOT DELETE CODES\LASUPTO3.py
set CODE4=C:\DO NOT DELETE CODES\LAS4.py

set STATE_FILE=%BASE_DIR%\run_state.txt

REM ==================================================
REM SAFETY
REM ==================================================
if not exist "%STATE_FILE%" (
    echo ❌ run_state.txt not found. Run Code-1 once manually.
    goto END
)

echo ================================================================
echo   SHORT ALLOCATION LAS AUTOMATION STARTED
echo ================================================================
echo.

REM ==================================================
REM STEP 1 — RUN CODE-1 (SELF-RETRYING)
REM ==================================================
echo ▶ Running Code-1 (Downloader + Retry + Manual Handling)
%PYTHON% "%CODE1%"
if errorlevel 1 goto FAIL

REM ==================================================
REM STEP 2 — READ UPDATED RUN
REM ==================================================
for /f "tokens=2 delims==" %%A in ('findstr "^RUN=" "%STATE_FILE%"') do (
    set RUN=%%A
)

REM ==================================================
REM 🔑 CRITICAL FIX — ALIGN WITH CODE-1 LOGIC
REM ==================================================
REM Code-1 increments RUN after success,
REM so batch must evaluate (RUN - 1)
set /a RUN=RUN-1

echo.
echo ▶ Effective RUN (Batch Decision) = %RUN%
echo.

REM ==================================================
REM STEP 3 — DECISION LOGIC (BASED ON EFFECTIVE RUN)
REM ==================================================

REM RUN 1 / 2 / 3 → Code-2 → Code-3
if %RUN% GEQ 1 if %RUN% LEQ 3 (
    echo ▶ FLOW: Code-2 → Code-3
    echo.

    %PYTHON% "%CODE2%"
    if errorlevel 1 goto FAIL

    %PYTHON% "%CODE3%"
    if errorlevel 1 goto FAIL

    goto SUCCESS
)

REM RUN 4 → Code-2 → Code-4
if %RUN% EQU 4 (
    echo ▶ FLOW: Code-2 → Code-4
    echo.

    %PYTHON% "%CODE2%"
    if errorlevel 1 goto FAIL

    %PYTHON% "%CODE4%"
    if errorlevel 1 goto FAIL

    goto SUCCESS
)

REM ==================================================
REM OPTIONAL SAFETY — RUN > 4
REM ==================================================
echo ⚠ All 4 runs already completed for today.
goto END

REM ==================================================
REM HANDLERS
REM ==================================================
:FAIL
echo.
echo ❌ ERROR: One of the scripts failed.
echo ⛔ Batch stopped.
goto END

:SUCCESS
echo.
echo ✅ Batch execution completed successfully.

:END
echo.
echo ================================================================
echo SHORT ALLOCATION LAS AUTOMATION FINISHED
echo ================================================================
pause
endlocal
