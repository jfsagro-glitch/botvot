# Скрипт для запуска всех ботов
Write-Host "Запуск ботов..." -ForegroundColor Green
Write-Host ""

# Sales Bot
Write-Host "1. Запуск Sales Bot (StartNowQ_bot)..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PWD'; `$Host.UI.RawUI.WindowTitle = 'Sales Bot - StartNowQ_bot'; Write-Host '=== SALES BOT ===' -ForegroundColor Green; python -m bots.sales_bot" -WindowStyle Normal

Start-Sleep -Seconds 2

# Course Bot
Write-Host "2. Запуск Course Bot (StartNowAI_bot)..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PWD'; `$Host.UI.RawUI.WindowTitle = 'Course Bot - StartNowAI_bot'; Write-Host '=== COURSE BOT ===' -ForegroundColor Cyan; python -m bots.course_bot" -WindowStyle Normal

Start-Sleep -Seconds 2

Write-Host ""
Write-Host "✅ Боты запущены!" -ForegroundColor Green
Write-Host ""
Write-Host "Проверьте открытые окна PowerShell." -ForegroundColor Yellow
Write-Host "Протестируйте:" -ForegroundColor Cyan
Write-Host "  - Sales Bot: t.me/StartNowQ_bot" -ForegroundColor White
Write-Host "  - Course Bot: t.me/StartNowAI_bot" -ForegroundColor White
Write-Host ""

