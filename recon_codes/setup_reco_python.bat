@echo off
REM One-time setup: private Python (pandas 2.x) for the RECONCILIATION scripts.
REM Creates recon_codes\.venv  -> the console finds it automatically (or set RECO_PYTHON in .env).
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
echo.
echo Done. Restart the console.  Python used: %~dp0.venv\Scripts\python.exe
pause
