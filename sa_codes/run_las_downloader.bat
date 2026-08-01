@echo off
echo ==== %date% %time% ==== >> "D:\CLAUDE\rms-copilot\sa_codes\las_downloader_log.txt"
python "D:\CLAUDE\rms-copilot\sa_codes\las_email_downloader.py" >> "D:\CLAUDE\rms-copilot\sa_codes\las_downloader_log.txt" 2>&1
echo Exit code: %ERRORLEVEL% >> "D:\CLAUDE\rms-copilot\sa_codes\las_downloader_log.txt"
