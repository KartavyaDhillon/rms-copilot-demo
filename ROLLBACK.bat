@echo off
setlocal enabledelayedexpansion
title Rollback — RMS Co-pilot
cd /d D:\CLAUDE\rms-copilot
del .git\index.lock 2>nul
del .git\HEAD.lock 2>nul

echo.
echo ================================================================
echo  CHECKPOINTS  (newest first)
echo  Each line shows what files changed in that save
echo ================================================================
echo.

powershell -NoProfile -Command ^
  "git log --oneline -20 | ForEach-Object { " ^
  "  $hash = $_.Substring(0,7); " ^
  "  $msg  = $_.Substring(8); " ^
  "  Write-Host ('  ' + $hash + '  ' + $msg) -ForegroundColor Cyan; " ^
  "  $files = git show --stat --format='' $hash | Where-Object { $_ -match '\|' }; " ^
  "  if ($files) { $files | ForEach-Object { Write-Host ('         ' + $_.Trim()) -ForegroundColor DarkGray } } " ^
  "  Write-Host '' " ^
  "}"

echo.
echo ================================================================
echo  WHAT DO YOU WANT TO ROLL BACK?
echo  Copy a 7-letter hash from above (e.g.  a3f21b4)
echo ================================================================
echo.
set /p HASH=Hash (or Enter to quit):
if "!HASH!"=="" goto :quit

echo.
echo  1. Everything  — undo ALL changes since that checkpoint
echo  2. api.py only — restore just the backend code
echo  3. dashboard.html only — restore just the frontend
echo  4. api.py + dashboard.html — restore both code files
echo.
set /p CHOICE=Enter 1 / 2 / 3 / 4:

if "!CHOICE!"=="1" (
    echo.
    echo  WARNING: option 1 discards EVERY change made after !HASH!.
    set /p OK=  Type YES to confirm:
    if /i "!OK!"=="YES" (
        git reset --hard !HASH!
        echo.
        echo  Done. All files rolled back to !HASH!.
    ) else ( echo  Cancelled. )
) else if "!CHOICE!"=="2" (
    git checkout !HASH! -- api.py
    echo  Done. api.py rolled back to !HASH!.
) else if "!CHOICE!"=="3" (
    git checkout !HASH! -- dashboard.html
    echo  Done. dashboard.html rolled back to !HASH!.
) else if "!CHOICE!"=="4" (
    git checkout !HASH! -- api.py dashboard.html
    echo  Done. api.py + dashboard.html rolled back to !HASH!.
) else (
    echo  Invalid choice — nothing changed.
)

:quit
echo.
pause
