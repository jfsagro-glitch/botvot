"""
Скрипт для создания файла .env с токеном вашего бота.
"""

from pathlib import Path

def create_env_file():
    """Создать файл .env с токеном Sales Bot."""
    env_content = """# Токены Telegram ботов
# Токен Sales Bot (StartNowQ_bot)
SALES_BOT_TOKEN=your_sales_bot_token_here

# Токен Course Bot - СОЗДАЙТЕ ВТОРОГО БОТА ЧЕРЕЗ @BotFather
COURSE_BOT_TOKEN=ваш_токен_курс_бота_здесь

# ID чата администратора (для обратной связи по заданиям)
ADMIN_CHAT_ID=ваш_admin_chat_id_здесь

# ID Telegram групп (опционально)
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
        return
    
    with open(env_path, 'w', encoding='utf-8') as f:
        f.write(env_content)
    
    print("[УСПЕХ] Файл .env создан!")
    print("\nСледующие шаги:")
    print("1. Создайте второго бота через @BotFather")
    print("2. Обновите COURSE_BOT_TOKEN в .env")
    print("3. Получите admin chat ID и обновите ADMIN_CHAT_ID")
    print("\nСм. НАСТРОЙКА.md для подробных инструкций.")

if __name__ == "__main__":
    create_env_file()

