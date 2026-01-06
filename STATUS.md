# Статус ботов

## Как проверить работу ботов

### 1. Проверка процессов
```powershell
Get-Process python
```
Должно быть 2 процесса Python (один для каждого бота)

### 2. Проверка подключения
```bash
python diagnose_bots.py
```
Должно показать:
- ✅ Конфигурация OK
- ✅ Бот подключен: @StartNowQ_bot
- ✅ База данных подключена

### 3. Тест в Telegram
1. Откройте `t.me/StartNowQ_bot`
2. Отправьте `/start`
3. Бот должен ответить

## Если бот не отвечает

### Проверьте логи
В окнах PowerShell должны быть сообщения:
- "Sales Bot started"
- "Bot is ready to receive messages"
- "Handlers registered successfully"
- "Received /start from user [ID]" (при отправке команды)

### Возможные проблемы

1. **Бот не запущен**
   - Запустите: `python -m bots.sales_bot`
   - Или используйте: `launch_bots.bat`

2. **Ошибки в логах**
   - Проверьте окна PowerShell
   - Ищите красный текст (ошибки)
   - Скопируйте ошибку для исправления

3. **Токены неверны**
   - Проверьте `.env` файл
   - Убедитесь, что токены правильные
   - Запустите: `python diagnose_bots.py`

## Быстрый запуск

```bash
# Windows
launch_bots.bat

# Или PowerShell
.\start_all_bots.ps1

# Или Python
python quick_start_bots.py
```

