@echo off
title Market Intel - validation
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat (echo Run setup.bat first. & pause & exit /b 1)
call .venv\Scripts\activate.bat
python manage.py validate --enable-working
echo.
echo The full report was saved in the data folder (validation-report-....txt).
pause
