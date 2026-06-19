# Инструкция по загрузке в GitHub

## Подготовка к загрузке

1. **Проверьте .gitignore** - убедитесь, что `.env` и `data/` в игноре
2. **Создайте .env.example** - шаблон конфигурации без секретов
3. **Проверьте токены** - они НЕ должны быть в коде

## Команды для загрузки

```bash
# Инициализация git (если еще не сделано)
git init

# Добавление всех файлов
git add .

# Первый коммит
git commit -m "Initial commit: Telegram Course Platform"

# Добавление remote репозитория
git remote add origin https://github.com/jfsagro-glitch/botvot.git

# Загрузка в GitHub
git branch -M main
git push -u origin main
```

## Важно!

⚠️ **НЕ загружайте**:
- `.env` файл (уже в .gitignore)
- `data/` папку с базой данных
- Токены ботов
- Личные данные

✅ **Загрузите**:
- Весь код
- `requirements.txt`
- `README.md`
- `.env.example` (шаблон)
- Документацию

## После загрузки

1. Добавьте `.env.example` в репозиторий
2. Обновите README с инструкциями
3. Добавьте описание репозитория на GitHub

