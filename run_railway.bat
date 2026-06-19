@echo off
powershell -ExecutionPolicy Bypass -File "%~dp0railway.ps1" > "%~dp0railway_log.txt" 2>&1
