# Инструкция по обработке видео урока 1

## Вариант 1: Локальная обработка (если установлен FFmpeg)

1. Установите FFmpeg (если еще не установлен):
   - Windows: https://ffmpeg.org/download.html
   - Или через chocolatey: `choco install ffmpeg`

2. Запустите скрипт обработки:
   ```bash
   python scripts/process_lesson1_video.py
   ```

## Вариант 2: Онлайн обработка

Используйте онлайн-сервисы для обработки видео:
- https://www.freeconvert.com/video-compressor
- https://www.youcompress.com/
- https://www.clipchamp.com/

Параметры обработки:
- Разрешение: 720px ширина (высота пропорционально)
- Формат: MP4 (H.264)
- Размер: до 45 МБ
- Ориентация: горизонтальная (если видео вертикальное)

## Вариант 3: Обработка на Railway (после деплоя)

Скрипт `scripts/process_lesson1_video.py` будет работать на Railway, где FFmpeg установлен.

После обработки обновите путь в `data/lessons.json`:
```json
"path": "VIDEO/1/lesson1_telegram_optimized.mp4"
```
