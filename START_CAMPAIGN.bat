@echo off
title MBBS Lead Bot - Outreach Campaign Launcher
color 0A
cd /d "%~dp0"

echo ================================================================
echo           MBBS LEAD BOT - OUTREACH DISPATCHER
echo ================================================================
echo.

if exist "leads.csv" (
    echo [*] Found 'leads.csv' in this folder.
    echo [*] Starting campaign from leads.csv with smart anti-spam interval...
    echo.
    .venv\Scripts\python.exe send_campaign.py --file leads.csv --delay 4.0
) else if exist "leads_sample.csv" (
    echo [*] Found 'leads_sample.csv' in this folder.
    echo [*] Starting campaign with smart anti-spam interval...
    echo.
    .venv\Scripts\python.exe send_campaign.py --file leads_sample.csv --delay 4.0
) else (
    echo [*] Running interactive mode...
    .venv\Scripts\python.exe send_campaign.py --delay 4.0
)

echo.
echo ================================================================
echo Done! Results saved to campaign_report.csv
echo ================================================================
pause
