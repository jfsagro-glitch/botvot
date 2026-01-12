# Dockerfile для деплоя на Fly.io или других платформах

FROM python:3.11-slim

WORKDIR /app

# Копируем requirements и устанавливаем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Создаем директорию для данных
RUN mkdir -p /app/data

# Копируем весь код (теперь JSON файлы из data/ копируются благодаря исправленному .dockerignore)
COPY . .

# Проверяем, что JSON файлы на месте (для диагностики при сборке)
RUN echo "=== Checking data directory ===" && \
    ls -la /app/data/ 2>/dev/null || echo "Directory /app/data does not exist" && \
    echo "=== JSON files check ===" && \
    ([ -f /app/data/lessons.json ] && echo "✅ lessons.json exists" || echo "❌ lessons.json NOT FOUND") && \
    ([ -f /app/data/lesson19_images.json ] && echo "✅ lesson19_images.json exists" || echo "❌ lesson19_images.json NOT FOUND") && \
    ([ -f /app/data/lesson21_cards.json ] && echo "✅ lesson21_cards.json exists" || echo "❌ lesson21_cards.json NOT FOUND") && \
    echo "=== seed_data files check (should exist in image) ===" && \
    (ls -la /app/seed_data/ 2>/dev/null || echo "Directory /app/seed_data does not exist") && \
    ([ -f /app/seed_data/lessons.json ] && echo "✅ seed_data/lessons.json exists" || echo "❌ seed_data/lessons.json NOT FOUND")

# Запускаем боты
CMD ["python", "run_all_bots.py"]

