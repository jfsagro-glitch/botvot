@echo off
cd /d "%~dp0"
powershell.exe -ExecutionPolicy Bypass -File deploy_fly.ps1
pause
