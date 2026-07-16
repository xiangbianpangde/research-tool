@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo research-tool interactive setup
python scripts\setup_interactive.py %*
if errorlevel 1 pause
