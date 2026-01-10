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
    ADMIN_CHAT_ID: int = int(os.getenv("ADMIN_CHAT_ID", "0"))
    
    # Curator Group ID (for questions from users)
    CURATOR_GROUP_ID: str = os.getenv("CURATOR_GROUP_ID", "")
    
    # Telegram Group Chat IDs
    GENERAL_GROUP_ID: str = os.getenv("GENERAL_GROUP_ID", "")
    PREMIUM_GROUP_ID: str = os.getenv("PREMIUM_GROUP_ID", "")
    
    # Database
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "./data/course_platform.db")
    
    # Course Settings
    COURSE_DURATION_DAYS: int = int(os.getenv("COURSE_DURATION_DAYS", "30"))
    LESSON_INTERVAL_HOURS: int = int(os.getenv("LESSON_INTERVAL_HOURS", "24"))
    
    # Payment Settings
    PAYMENT_PROVIDER: str = os.getenv("PAYMENT_PROVIDER", "mock")  # "mock" or "yookassa"
    
    # YooKassa Settings (if PAYMENT_PROVIDER == "yookassa")
    YOOKASSA_SHOP_ID: str = os.getenv("YOOKASSA_SHOP_ID", "")
    YOOKASSA_SECRET_KEY: str = os.getenv("YOOKASSA_SECRET_KEY", "")
    YOOKASSA_RETURN_URL: str = os.getenv("YOOKASSA_RETURN_URL", "https://t.me/StartNowQ_bot")
    
    # Payment Currency
    PAYMENT_CURRENCY: str = os.getenv("PAYMENT_CURRENCY", "RUB")  # RUB, USD, EUR, etc.
    
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

