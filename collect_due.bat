@echo off
REM Use this with Windows Task Scheduler to collect automatically.
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python manage.py collect --due >> data\collect-log.txt 2>&1
