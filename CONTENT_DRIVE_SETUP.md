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
Синк берёт Google Docs как **text/plain**. Поэтому для форматирования вы **пишете Telegram HTML прямо в документе** (теги руками).

Поддерживаемые Telegram HTML теги (без CSS/стилей):
- `<b>...</b>` (или `<strong>`)
- `<i>...</i>` (или `<em>`)
- `<u>...</u>` (или `<ins>`)
- `<s>...</s>` (или `<strike>`, `<del>`)
- `<code>...</code>`
- `<pre>...</pre>`
- `<a href="https://...">текст</a>`
- `<tg-spoiler>...</tg-spoiler>` или `<span class="tg-spoiler">...</span>`
- `<blockquote>...</blockquote>`

Чтобы бот не падал из‑за случайных “кривых” тегов, синк прогоняет текст через санитайзер:
- неизвестные/опасные теги будут экранированы,
- “1 < 2” будет безопасно превращено в `1 &lt; 2`.

### Вариант: один “master Google Doc” на все уроки
Если вам удобнее редактировать **в одном документе**, можно использовать `DRIVE_MASTER_DOC_ID`.

В этом режиме бот парсит уроки из одного Google Doc, разделяя по заголовкам вида:
- `День 0`
- `День 1: (необязательно — название)`
- `День 2`

Внутри каждого дня:
- для задания используйте маркер строкой: `Задание:` (с новой строки)
- (опционально) название: `Заголовок: ...`

Переменные окружения:
- `DRIVE_CONTENT_ENABLED=1`
- `DRIVE_MASTER_DOC_ID=<id документа>` *(берётся из ссылки вида `https://docs.google.com/document/d/<id>/edit` )*
- `GOOGLE_SERVICE_ACCOUNT_JSON=...`

Примечание: если в тексте/задании вставить ссылку на Drive файл вида `https://drive.google.com/file/d/<id>/view`,
синк попытается скачать её как медиа и добавить в `media`.

