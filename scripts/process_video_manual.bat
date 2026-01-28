@echo off
REM Скрипт для обработки видео урока 1 для Telegram
REM Требуется FFmpeg (установите с https://ffmpeg.org/download.html)

set INPUT_VIDEO=VIDEO\1\document_5461089794907998389.mp4
set OUTPUT_VIDEO=VIDEO\1\lesson1_telegram_optimized.mp4

echo ========================================
echo Обработка видео урока 1 для Telegram
echo ========================================
echo.

REM Проверяем наличие FFmpeg
ffmpeg -version >nul 2>&1
if errorlevel 1 (
    echo ОШИБКА: FFmpeg не найден!
    echo Установите FFmpeg с https://ffmpeg.org/download.html
    pause
    exit /b 1
)

echo FFmpeg найден, начинаю обработку...
echo.

REM Обрабатываем видео:
REM - Конвертируем в горизонтальный формат (если вертикальное)
REM - Масштабируем до 720px ширины
REM - Оптимизируем для Telegram
ffmpeg -i "%INPUT_VIDEO%" ^
    -vf "scale=720:-2" ^
    -c:v libx264 ^
    -preset medium ^
    -crf 23 ^
    -maxrate 2000k ^
    -bufsize 4000k ^
    -c:a aac ^
    -b:a 128k ^
    -movflags +faststart ^
    -y ^
    "%OUTPUT_VIDEO%"

if errorlevel 1 (
    echo.
    echo ОШИБКА: Обработка не удалась!
    pause
    exit /b 1
)

echo.
echo ========================================
echo Видео успешно обработано!
echo Выходной файл: %OUTPUT_VIDEO%
echo ========================================
pause
