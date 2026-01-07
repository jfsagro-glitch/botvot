# Dockerfile для деплоя на Fly.io или других платформах

FROM python:3.11-slim

WORKDIR /app

# Копируем requirements и устанавливаем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь код
COPY . .

# Создаем директорию для данных
RUN mkdir -p /app/data

# Запускаем боты
CMD ["python", "run_all_bots.py"]

