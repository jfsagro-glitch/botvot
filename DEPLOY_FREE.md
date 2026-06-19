# Бесплатный деплой — Fly.io

Railway перешёл на платную модель. Ниже — лучшие **бесплатные** варианты для этого бота,
и пошаговая инструкция для Fly.io (рекомендуется).

---

## Сравнение вариантов

| Платформа | Бесплатно | SQLite (volume) | Docker | Без засыпания |
|---|---|---|---|---|
| **Fly.io** ⭐ | 3 VM shared-cpu | ✅ 3 GB | ✅ | ✅ |
| **Koyeb** | 2 сервиса | ❌ (нет volumes) | ✅ | ✅ |
| **Render** | 1 сервис | ❌ | ✅ | ❌ засыпает |
| **Oracle Cloud Always Free** | 2 VM 1 CPU/1 GB | ✅ | ✅ | ✅ |

**Рекомендация: Fly.io** — есть постоянный volume для SQLite, Docker готов,
не засыпает, бесплатного тарифа хватает для 3 ботов.

---

## Деплой на Fly.io

### Шаг 1. Установите flyctl

```bash
# macOS / Linux
curl -L https://fly.io/install.sh | sh

# Windows (PowerShell)
iwr https://fly.io/install.ps1 -useb | iex
```

Зарегистрируйтесь: https://fly.io/app/sign-up (через GitHub, карта не нужна для бесплатного)

### Шаг 2. Создайте fly.toml

Создайте файл `fly.toml` в корне проекта:

```toml
app = "botvot"          # придумайте уникальное имя
primary_region = "waw"  # Варшава — ближе к России

[build]
  dockerfile = "Dockerfile"

[mounts]
  source = "botvot_data"
  destination = "/app/data"

[env]
  PYTHONUNBUFFERED = "1"

[[services]]
  # Боты работают через polling, HTTP не нужен
  # Оставляем пустым
```

### Шаг 3. Запустите деплой

```bash
cd botvot

# Инициализация (создаст приложение на Fly.io)
flyctl launch --no-deploy

# Создайте постоянный volume для SQLite базы данных
flyctl volumes create botvot_data --size 1 --region waw

# Установите переменные окружения (замените на свои значения)
flyctl secrets set \
  SALES_BOT_TOKEN="..." \
  COURSE_BOT_TOKEN="..." \
  ADMIN_BOT_TOKEN="..." \
  DATABASE_PATH="/app/data/course.db" \
  PAYMENT_PROVIDER="yookassa" \
  YOOKASSA_SHOP_ID="..." \
  YOOKASSA_SECRET_KEY="..."

# Задеплойте
flyctl deploy
```

### Шаг 4. Проверьте работу

```bash
# Смотреть логи в реальном времени
flyctl logs

# Статус приложения
flyctl status
```

---

## Важно для SQLite на Fly.io

В `.env.example` и в реальных переменных окружения обязательно задайте:

```
DATABASE_PATH=/app/data/course.db
```

Volume монтируется в `/app/data/` — именно там должна лежать база.

---

## Альтернатива: Oracle Cloud Always Free

Если нужна полная VM (надёжнее, без ограничений):

1. Зарегистрируйтесь на https://cloud.oracle.com (нужна карта, но деньги не снимают)
2. Создайте **Always Free** VM: Ampere A1, 1 CPU, 1 GB RAM, Ubuntu 22.04
3. Подключитесь по SSH и запустите:

```bash
# Установка Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker ubuntu

# Клонирование репозитория
git clone https://github.com/ВАШ_АККАУНТ/botvot.git
cd botvot

# Создайте .env файл со всеми переменными
nano .env

# Запуск
docker build -t botvot .
docker run -d --name botvot \
  --env-file .env \
  -v $(pwd)/data:/app/data \
  --restart always \
  botvot
```

---

## Автодеплой из GitHub (Fly.io)

Добавьте в `.github/workflows/deploy.yml`:

```yaml
name: Deploy to Fly.io
on:
  push:
    branches: [main]
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: superfly/flyctl-actions/setup-flyctl@master
      - run: flyctl deploy --remote-only
        env:
          FLY_API_TOKEN: ${{ secrets.FLY_API_TOKEN }}
```

Получите токен: `flyctl auth token` → добавьте в GitHub Secrets как `FLY_API_TOKEN`.

---

## Checklist перед деплоем

- [ ] `DATABASE_PATH=/app/data/course.db` задан в secrets
- [ ] Все токены ботов заданы через `flyctl secrets set`
- [ ] `fly.toml` создан с разделом `[mounts]`
- [ ] Volume создан: `flyctl volumes create botvot_data --size 1`
- [ ] `flyctl deploy` прошёл без ошибок
- [ ] `flyctl logs` — боты запустились
