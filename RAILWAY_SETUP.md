# Настройка переменных окружения в Railway

## Обязательные переменные

Для работы ботов необходимо добавить следующие переменные окружения в Railway:

### 1. Откройте проект в Railway
- Перейдите на https://railway.app
- Откройте ваш проект
- Перейдите в раздел **Variables** (Переменные)

### 2. Добавьте обязательные переменные:

#### Обязательные (для работы ботов):
```
SALES_BOT_TOKEN=your_sales_bot_token_here
COURSE_BOT_TOKEN=your_course_bot_token_here
```

#### Опциональные (но рекомендуемые):
```
ADMIN_CHAT_ID=your_admin_chat_id
GENERAL_GROUP_ID=your_general_group_id
PREMIUM_GROUP_ID=your_premium_group_id
CURATOR_GROUP_ID=your_curator_group_id
DATABASE_PATH=./data/course_platform.db
PAYMENT_PROVIDER=mock
```

### 3. Где взять токены:

1. **SALES_BOT_TOKEN** и **COURSE_BOT_TOKEN**:
   - Откройте Telegram
   - Найдите @BotFather
   - Отправьте `/mybots`
   - Выберите нужного бота
   - Нажмите "API Token"
   - Скопируйте токен

2. **ADMIN_CHAT_ID**:
   - Напишите любому боту
   - Перейдите по ссылке: `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
   - Найдите `"chat":{"id":123456789}`
   - Это и есть ваш chat_id

### 4. Порядок действий:

1. Добавьте `SALES_BOT_TOKEN` и `COURSE_BOT_TOKEN` в Railway Variables
2. Railway автоматически перезапустит сервис
3. Проверьте логи - должно появиться:
   - ✅ HTTP сервер запущен
   - ✅ Продающий бот инициализирован
   - ✅ Курс-бот инициализирован

### 5. Проверка работы:

После добавления переменных:
- Healthcheck должен пройти успешно
- В логах не должно быть ошибок "❌ Неверная конфигурация"
- Боты должны запуститься и начать отвечать на команды

## Важные заметки:

- ❗ **НЕ КОММИТЬТЕ** `.env` файл в репозиторий!
- ❗ Токены являются секретными данными - храните их только в Railway Variables
- ❗ После изменения переменных Railway автоматически перезапустит сервис
