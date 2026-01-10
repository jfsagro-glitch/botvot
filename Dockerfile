# Dockerfile для деплоя на Fly.io или других платформах

FROM python:3.11-slim

WORKDIR /app

# Копируем requirements и устанавливаем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Создаем директорию для данных
RUN mkdir -p /app/data

# Копируем весь код
COPY . .

# ВАЖНО: Явно копируем JSON файлы уроков ПОСЛЕ общего копирования,
# чтобы гарантировать их наличие (Railway может игнорировать некоторые файлы)
COPY data/lessons.json /app/data/lessons.json
COPY data/lesson19_images.json /app/data/lesson19_images.json
COPY data/lesson21_cards.json /app/data/lesson21_cards.json

# Проверяем, что файлы на месте (для диагностики)
RUN ls -la /app/data/ && echo "JSON files check:" && [ -f /app/data/lessons.json ] && echo "✅ lessons.json exists" || echo "❌ lessons.json NOT FOUND"

# Запускаем боты
CMD ["python", "run_all_bots.py"]

