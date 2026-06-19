# Script to start bots in separate PowerShell window with visible logs

$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptPath

Write-Host "Starting bots in new window..." -ForegroundColor Yellow
Write-Host "Check the new PowerShell window for logs" -ForegroundColor Green
Write-Host ""

$command = "cd '$scriptPath'; python run_all_bots.py; Write-Host ''; Write-Host 'Bots stopped. Press any key to close...' -ForegroundColor Red; `$null = `$Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')"

Start-Process powershell -ArgumentList "-NoExit", "-Command", $command
