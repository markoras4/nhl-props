@echo off
cd /d "%~dp0"
python -m nhlprops build %*
if errorlevel 1 pause
