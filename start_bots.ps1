# Скрипт для запуска ботов
Write-Host "Запуск Sales Bot..." -ForegroundColor Green
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PWD'; `$env:PYTHONIOENCODING='utf-8'; python -m bots.sales_bot" -WindowStyle Normal

Start-Sleep -Seconds 2

Write-Host "Запуск Course Bot..." -ForegroundColor Green
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PWD'; `$env:PYTHONIOENCODING='utf-8'; python -m bots.course_bot" -WindowStyle Normal

Write-Host "`nБоты запущены! Проверьте открытые окна PowerShell." -ForegroundColor Yellow
Write-Host "Протестируйте бота: t.me/GameChangerQ_bot" -ForegroundColor Cyan

