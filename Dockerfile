# Dockerfile — Fly.io / Docker

FROM python:3.11-slim

WORKDIR /app

# FFmpeg для сжатия видео
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Зависимости (кешируются отдельно)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Код приложения
COPY . .

# /app/data монтируется как Fly.io volume — не копировать туда ничего при сборке
RUN mkdir -p /app/data

# Запуск
CMD ["python", "run_all_bots.py"]

