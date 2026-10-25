@echo off
cd /d "%~dp0"
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo Could not install dependencies. Check your Python installation.
  pause
  exit /b 1
)
python main.py
