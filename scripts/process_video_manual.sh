#!/bin/bash
# Скрипт для обработки видео урока 1 для Telegram
# Требуется FFmpeg

INPUT_VIDEO="VIDEO/1/document_5461089794907998389.mp4"
OUTPUT_VIDEO="VIDEO/1/lesson1_telegram_optimized.mp4"

echo "========================================"
echo "Обработка видео урока 1 для Telegram"
echo "========================================"
echo

# Проверяем наличие FFmpeg
if ! command -v ffmpeg &> /dev/null; then
    echo "ОШИБКА: FFmpeg не найден!"
    echo "Установите FFmpeg: sudo apt-get install ffmpeg"
    exit 1
fi

echo "FFmpeg найден, начинаю обработку..."
echo

# Обрабатываем видео:
# - Конвертируем в горизонтальный формат (если вертикальное)
# - Масштабируем до 720px ширины
# - Оптимизируем для Telegram
ffmpeg -i "$INPUT_VIDEO" \
    -vf "scale=720:-2" \
    -c:v libx264 \
    -preset medium \
    -crf 23 \
    -maxrate 2000k \
    -bufsize 4000k \
    -c:a aac \
    -b:a 128k \
    -movflags +faststart \
    -y \
    "$OUTPUT_VIDEO"

if [ $? -ne 0 ]; then
    echo
    echo "ОШИБКА: Обработка не удалась!"
    exit 1
fi

echo
echo "========================================"
echo "Видео успешно обработано!"
echo "Выходной файл: $OUTPUT_VIDEO"
echo "========================================"
