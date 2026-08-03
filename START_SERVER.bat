@echo off
title RMS Co-pilot Server
REM Go to the folder this .bat lives in - NOT a hardcoded path. This is what
REM lets the same file work on the dev machine (D:\CLAUDE\rms-copilot) and the
REM server (D:\CONSOLE\rms-copilot) without editing it per machine.
cd /d "%~dp0"
call .venv\Scripts\activate.bat 2>nul || echo (no venv, using system python)
echo Starting RMS Co-pilot  [AUTO-RELOAD OFF]
echo The server does NOT restart on file changes - this keeps live runs (downloads,
echo captcha/token waits, long pipelines) from being killed mid-way when a file is saved.
echo To pick up code changes, stop with Ctrl+C and start this again.

set "HAS_CERT="
if exist certs\cert.pem if exist certs\key.pem set "HAS_CERT=1"

if defined HAS_CERT (
  echo Dashboard: https://localhost:8000  [HTTPS - self-signed cert found in certs\]
  echo First visit from each browser: click through the one-time "not trusted" warning.
  echo To go back to plain HTTP, delete the certs folder and restart this.
  uvicorn api:app --host 0.0.0.0 --port 8000 --ssl-keyfile certs\key.pem --ssl-certfile certs\cert.pem
) else (
  echo Dashboard: http://localhost:8000  [PLAIN HTTP - not encrypted]
  echo To switch to HTTPS ^(recommended if others access this over the LAN^),
  echo run:  python generate_ssl_cert.py    then start this again.
  uvicorn api:app --host 0.0.0.0 --port 8000
)
pause
