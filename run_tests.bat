@echo off
rem Runs the Scheduler tests. Double-click, or run from cmd.
cd /d "%~dp0"
if exist venv\Scripts\python.exe (set PY=venv\Scripts\python.exe) else if exist .venv\Scripts\python.exe (set PY=.venv\Scripts\python.exe) else (set PY=python)
echo Using: %PY%
%PY% -m pip install tzdata
%PY% -m unittest discover -s tests -t . -v
echo.
pause
