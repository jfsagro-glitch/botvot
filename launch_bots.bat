@echo off
chcp 65001 >nul
echo ========================================
echo   ЗАПУСК БОТОВ TELEGRAM COURSE PLATFORM
echo ========================================
echo.

echo [1/2] Запуск Sales Bot (StartNowQ_bot)...
start "Sales Bot" cmd /c "python -m bots.sales_bot & pause"

timeout /t 3 /nobreak >nul

echo [2/2] Запуск Course Bot (StartNowAI_bot)...
start "Course Bot" cmd /c "python -m bots.course_bot & pause"

echo.
echo ✅ Боты запущены!
echo.
echo Проверьте открытые окна командной строки.
echo Протестируйте: t.me/StartNowQ_bot
echo.
pause

