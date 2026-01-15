"""
Configuration management for the Telegram Course Platform.

Loads configuration from environment variables and provides
centralized access to all system settings.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


def _get_env_value(key: str, default: str = "") -> str:
    """Получает переменную окружения с очисткой пробелов."""
    value = os.environ.get(key, "")
    if not value:
        value = os.getenv(key, default)
    # Удаляем пробелы в начале и конце (частая ошибка при копировании)
    return value.strip() if value else default


class Config:
    """Application configuration."""
    
    # Telegram Bot Tokens
    # Используем os.environ напрямую для надежного чтения переменных окружения
    # Railway устанавливает переменные в os.environ
    SALES_BOT_TOKEN: str = _get_env_value("SALES_BOT_TOKEN", "")
    COURSE_BOT_TOKEN: str = _get_env_value("COURSE_BOT_TOKEN", "")
    
    # Admin Chat ID (for assignment feedback)
    ADMIN_CHAT_ID: int = int(_get_env_value("ADMIN_CHAT_ID", "0") or "0")
    
    # Curator Group ID (for questions from users)
    CURATOR_GROUP_ID: str = _get_env_value("CURATOR_GROUP_ID", "")
    
    # Telegram Group Chat IDs
    # Note: use _get_env_value to strip spaces (common copy/paste issue in Railway Variables)
    GENERAL_GROUP_ID: str = _get_env_value("GENERAL_GROUP_ID", "")
    PREMIUM_GROUP_ID: str = _get_env_value("PREMIUM_GROUP_ID", "")
    
    # Telegram Group Invite Links (preferred over IDs for opening chats)
    # Example: https://t.me/+AbCdEfGhIjk or https://t.me/joinchat/AbCdEfGhIjk
    GENERAL_GROUP_INVITE_LINK: str = _get_env_value("GENERAL_GROUP_INVITE_LINK", "")
    PREMIUM_GROUP_INVITE_LINK: str = _get_env_value("PREMIUM_GROUP_INVITE_LINK", "")
    
    # Database
    DATABASE_PATH: str = _get_env_value("DATABASE_PATH", "./data/course_platform.db")

    # Content Sync (Google Drive)
    # If configured, admins can run /sync_content to pull lessons/tasks/media from Drive
    DRIVE_CONTENT_ENABLED: str = _get_env_value("DRIVE_CONTENT_ENABLED", "0")  # "1" to enable
    DRIVE_ROOT_FOLDER_ID: str = _get_env_value("DRIVE_ROOT_FOLDER_ID", "")
    # Provide one of these (preferred: JSON string; alternative: base64-encoded JSON)
    GOOGLE_SERVICE_ACCOUNT_JSON: str = _get_env_value("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    GOOGLE_SERVICE_ACCOUNT_JSON_B64: str = _get_env_value("GOOGLE_SERVICE_ACCOUNT_JSON_B64", "")
    # Where to store synced media relative to project root (/app in container)
    DRIVE_MEDIA_DIR: str = _get_env_value("DRIVE_MEDIA_DIR", "data/content_media")
    
    # Course Settings
    COURSE_DURATION_DAYS: int = int(os.getenv("COURSE_DURATION_DAYS", "30"))
    LESSON_INTERVAL_HOURS: int = int(os.getenv("LESSON_INTERVAL_HOURS", "24"))

    # Scheduling / Timezone
    # Used for mentor reminder window calculations. Default: Moscow time.
    SCHEDULE_TIMEZONE: str = _get_env_value("SCHEDULE_TIMEZONE", "Europe/Moscow")
    # Daily lesson delivery time in local time (SCHEDULE_TIMEZONE)
    # Example: "08:30"
    LESSON_DELIVERY_TIME_LOCAL: str = _get_env_value("LESSON_DELIVERY_TIME_LOCAL", "08:30")
    # Mentor reminders are allowed only within this local-time window
    MENTOR_REMINDER_START_LOCAL: str = _get_env_value("MENTOR_REMINDER_START_LOCAL", "09:30")
    MENTOR_REMINDER_END_LOCAL: str = _get_env_value("MENTOR_REMINDER_END_LOCAL", "22:00")
    
    # Payment Settings
    PAYMENT_PROVIDER: str = _get_env_value("PAYMENT_PROVIDER", "mock")  # "mock" or "yookassa"
    
    # YooKassa Settings (if PAYMENT_PROVIDER == "yookassa")
    YOOKASSA_SHOP_ID: str = _get_env_value("YOOKASSA_SHOP_ID", "")
    YOOKASSA_SECRET_KEY: str = _get_env_value("YOOKASSA_SECRET_KEY", "")
    YOOKASSA_RETURN_URL: str = _get_env_value("YOOKASSA_RETURN_URL", "https://t.me/StartNowQ_bot")

    # YooKassa receipt (54-FZ)
    # Some YooKassa shops require receipt data in every payment request.
    # In this project we collect user's email in SalesBot and pass it to payment metadata.
    YOOKASSA_RECEIPT_REQUIRED: str = _get_env_value("YOOKASSA_RECEIPT_REQUIRED", "0")  # "1" to enforce
    # Tax system code (1-6). Common: 2 (УСН доход), 3 (УСН доход-расход).
    YOOKASSA_TAX_SYSTEM_CODE: str = _get_env_value("YOOKASSA_TAX_SYSTEM_CODE", "2")
    # VAT code. Common: 1 (без НДС), 6 (20%).
    YOOKASSA_VAT_CODE: str = _get_env_value("YOOKASSA_VAT_CODE", "1")
    
    # Payment Currency
    PAYMENT_CURRENCY: str = _get_env_value("PAYMENT_CURRENCY", "RUB")  # RUB, USD, EUR, etc.
    
    @classmethod
    def validate(cls) -> bool:
        """Validate that required configuration is present."""
        # Перечитываем переменные окружения для надежности с очисткой пробелов
        sales_token = _get_env_value("SALES_BOT_TOKEN", "") or cls.SALES_BOT_TOKEN
        course_token = _get_env_value("COURSE_BOT_TOKEN", "") or cls.COURSE_BOT_TOKEN
        
        # Обновляем значения в классе
        cls.SALES_BOT_TOKEN = sales_token.strip() if sales_token else ""
        cls.COURSE_BOT_TOKEN = course_token.strip() if course_token else ""
        
        # Проверяем, что токены не пустые (после очистки пробелов)
        sales_valid = bool(sales_token and sales_token.strip())
        course_valid = bool(course_token and course_token.strip())
        
        return sales_valid and course_valid
    
    @classmethod
    def ensure_data_directory(cls):
        """Ensure data directory exists for database."""
        db_path = Path(cls.DATABASE_PATH)
        # Создаем директорию для базы данных
        # На Railway используется постоянное хранилище, но нужно убедиться, что директория существует
        try:
            db_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            # Логируем ошибку, но продолжаем работу
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"⚠️ Не удалось создать директорию для БД: {e}")
            # Пытаемся использовать текущую директорию
            cls.DATABASE_PATH = "course_platform.db"

