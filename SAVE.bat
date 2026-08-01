@echo off
title Save checkpoint
cd /d D:\CLAUDE\rms-copilot
del .git\index.lock 2>nul
del .git\HEAD.lock 2>nul
set /p MSG=Checkpoint message (or press Enter for auto):
if "%MSG%"=="" set MSG=auto-checkpoint %date% %time%
git add -A
git commit -m "%MSG%"
echo.
echo Done. Your checkpoint is saved.
echo To see all checkpoints: git log --oneline
pause
