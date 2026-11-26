@echo off
setlocal
title Market Intel - setup
cd /d "%~dp0"
echo.
echo  Market Intel - one-time setup
echo  ------------------------------
REM Find Python: prefer the "py" launcher that python.org installs, else "python".
set PY=
py -3 --version >nul 2>&1 && set PY=py -3
if not defined PY (python --version >nul 2>&1 && set PY=python)
if not defined PY (
  echo  Python was not found.
  echo  Install Python 3.12 from https://www.python.org/downloads/windows/
  echo  and tick "Add python.exe to PATH" on the first installer screen. Then run setup.bat again.
  pause & exit /b 1
)
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 (
  echo  Your Python is too old. Install Python 3.12 from python.org, then run setup.bat again.
  pause & exit /b 1
)
echo  Using: & %PY% --version
echo  Creating a private environment in .venv ...
%PY% -m venv .venv || (echo  Could not create .venv & pause & exit /b 1)
call .venv\Scripts\activate.bat
echo  Downloading the free packages (needs internet, about 1-3 minutes) ...
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet || (echo  Package install failed. Check your internet and run setup.bat again. & pause & exit /b 1)
if not exist .env copy .env.example .env >nul
python manage.py init-db
echo.
echo  Setup complete.
echo  Next: double-click validate.bat (checks everything), then run.bat (opens the app).
echo.
pause
