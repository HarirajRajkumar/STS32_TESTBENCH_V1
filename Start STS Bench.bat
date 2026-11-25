@echo off
rem Opens the bench in its own window. Use "python app.py --browser" for a browser tab instead.
cd /d "%~dp0"
python app.py
if errorlevel 1 pause
