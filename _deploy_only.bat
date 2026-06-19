@echo off
cd /d "%~dp0"
powershell.exe -ExecutionPolicy Bypass -Command "flyctl deploy --app botvot-prod --remote-only"
pause
