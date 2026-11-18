@echo off
REM Push the console to GitHub (origin) and Bitbucket. Run after you have committed.
cd /d "%~dp0"
for /f %%b in ('git branch --show-current') do set BR=%%b
echo Pushing branch %BR% to GitHub ...
git push origin %BR% || goto :fail
echo Pushing branch %BR% to Bitbucket ...
git push bitbucket %BR% || goto :fail
echo.
echo Done - both remotes are updated.
pause
exit /b 0
:fail
echo.
echo PUSH FAILED - read the message above. Nothing else was changed.
pause
exit /b 1
