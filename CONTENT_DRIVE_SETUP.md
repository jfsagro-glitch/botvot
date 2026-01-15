## Google Drive контент (вариант B) — как устроить папки и синхронизировать в бота

### Идея
Редактор/команда правят уроки в Google Drive, а бот по команде админа `/sync_content`:
- скачивает тексты/медиа,
- собирает единый `lessons.json` рядом с БД в Volume (обычно `/app/data/lessons.json`),
- и сразу подхватывает обновления через `LessonLoader.reload()`.

### Структура папок на Drive
В корневой папке (ID в `DRIVE_ROOT_FOLDER_ID`) создайте подпапки:
- `day_00`
- `day_01`
- …
- `day_30`

Внутри каждой `day_XX` (минимум нужен `lesson`):
- **lesson**: Google Doc с названием `lesson` (или файл `lesson.txt` / `lesson.html`)
- **task** (опционально): Google Doc `task` (или `task.txt` / `task.html`)
- **meta.json** (опционально): например:

```json
{
  "title": "День 1: Вопрос как инструмент",
  "silent": false
}
```

- **media** (опционально):
  - либо подпапка `media/` внутри `day_XX`,
  - либо медиа-файлы прямо в `day_XX`.
  - Поддерживаются бинарные файлы с MIME `image/*` и `video/*`.
  - Они будут скачаны в `data/content_media/day_XX/…` и в `lessons.json` попадут как `path`.

### Переменные окружения (Railway Variables)
- `DRIVE_CONTENT_ENABLED=1`
- `DRIVE_ROOT_FOLDER_ID=<id папки на Drive>`
- `GOOGLE_SERVICE_ACCOUNT_JSON=<json сервисного аккаунта>` *(предпочтительно)*
  - либо `GOOGLE_SERVICE_ACCOUNT_JSON_B64=<base64(json)>`
- `DRIVE_MEDIA_DIR=data/content_media` *(опционально)*

### Как запустить синхронизацию
В курс-боте (в Telegram), из `ADMIN_CHAT_ID`:
- `/sync_content`

В ответ бот пришлёт:
- сколько дней синхронизировано,
- сколько медиа скачано,
- путь к `lessons.json`,
- предупреждения (если есть).

### Важное про формат текста
Синк берёт Google Docs как **text/plain**. Если вам нужны жирный/ссылки — можно:
- писать Telegram HTML прямо в документе (теги `<b>`, `<a href=...>`),
- либо хранить `lesson.html`/`task.html` как обычные файлы в папке дня.

