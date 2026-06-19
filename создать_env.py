"""
Скрипт для создания файла .env с токеном вашего бота.

Запустите этот скрипт, чтобы создать файл .env с токеном Sales Bot.
Вам все еще нужно будет добавить токен Course Bot и другие настройки.
"""

from pathlib import Path

def create_env_file():
    """Создать файл .env с токеном Sales Bot."""
    env_content = """# Токены Telegram ботов
# Токен Sales Bot (StartNowQ_bot)
SALES_BOT_TOKEN=your_sales_bot_token_here

# Токен Course Bot - СОЗДАЙТЕ ВТОРОГО БОТА ЧЕРЕЗ @BotFather
# Перейдите к @BotFather, отправьте /newbot, создайте второго бота для доставки курса
COURSE_BOT_TOKEN=ваш_токен_курс_бота_здесь

# ID чата администратора (для обратной связи по заданиям)
# Получите это так:
# 1. Отправьте сообщение вашему Sales Bot: t.me/StartNowQ_bot
# 2. Откройте: https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
# 3. Найдите ваш chat ID в ответе
ADMIN_CHAT_ID=ваш_admin_chat_id_здесь

# ID Telegram групп (опционально)
# Добавьте ваших ботов в группы как администраторов, отправьте сообщение, затем проверьте getUpdates
GENERAL_GROUP_ID=ваш_general_group_id_здесь
PREMIUM_GROUP_ID=ваш_premium_group_id_здесь

# База данных
DATABASE_PATH=./data/course_platform.db

# Настройки курса
COURSE_DURATION_DAYS=30
LESSON_INTERVAL_HOURS=24
"""
    
    env_path = Path(".env")
    
    if env_path.exists():
        print(".env файл уже существует.")
        print("Если хотите перезаписать, удалите его вручную и запустите скрипт снова.")
        return
    
    with open(env_path, 'w', encoding='utf-8') as f:
        f.write(env_content)
    
    print("[УСПЕХ] Файл .env создан успешно!")
    print("\nСледующие шаги:")
    print("1. Создайте второго бота через @BotFather для доставки курса")
    print("2. Обновите COURSE_BOT_TOKEN в .env")
    print("3. Получите ваш admin chat ID и обновите ADMIN_CHAT_ID")
    print("4. (Опционально) Настройте Telegram группы и обновите ID групп")
    print("\nСм. НАСТРОЙКА.md для подробных инструкций.")

if __name__ == "__main__":
    create_env_file()

