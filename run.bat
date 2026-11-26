@echo off
title Market Intel
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat (echo Run setup.bat first. & pause & exit /b 1)
call .venv\Scripts\activate.bat
start "" cmd /c "timeout /t 3 >nul & start http://127.0.0.1:5000"
echo Market Intel is running. Keep this window open while you use it. Close it to stop.
python app.py
pause
